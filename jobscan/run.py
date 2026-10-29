"""Entry point.

    python -m jobscan                     # company feeds + open job boards -> sheet -> email
    python -m jobscan --group laptop      # boards that block GitHub's servers (LinkedIn, Naukri, Foundit)
    python -m jobscan --group laptop --if-due   # only the laptop boards that are due (the scheduled task)
    python -m jobscan --group boards      # every job board (or: --group feeds)
    python -m jobscan --dry-run           # print what would be added; no sheet, no email
    python -m jobscan --only meesho,nvidia,unstop
"""
import argparse
import hashlib
import logging
import platform
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

import yaml

from .core import IST, ROOT, fingerprint, job_key, today
from .filters import STRETCH, Gemini, experience_check, is_india, is_software
from .sources.boards import BOARDS, HOME_IP_ONLY, NEEDS_STATED_EXPERIENCE
from .sources.feeds import CUSTOM, READERS
from .state import Tracker

log = logging.getLogger("jobscan")
MAX_AGE_DAYS = 30
GROUPS = {
    "cloud": ("feeds", "boards"),         # GitHub Actions every morning: needs no login, browser or home IP
    "laptop": ("home-IP boards",),        # boards that block GitHub's servers: run from your laptop
    "feeds": ("feeds",),
    "boards": ("boards", "home-IP boards"),
}


def load_tasks(group="cloud", only=None):
    """(Run log name, reader, config) for every company feed and/or job board in the group."""
    tasks, parts = [], GROUPS[group]
    if "feeds" in parts:
        cfg = yaml.safe_load((ROOT / "companies.yaml").read_text(encoding="utf-8"))
        for system, entries in cfg.items():
            if system == "never_show":
                continue
            for c in entries or []:
                reader = READERS.get(system) or CUSTOM.get(c.get("reader", ""))
                if reader and (not only or c["name"].lower() in only or c.get("slug", c.get("tenant", "")) in only):
                    tasks.append((c["name"], reader, c))
    for name, reader in BOARDS.items():
        wanted = "home-IP boards" if name in HOME_IP_ONLY else "boards"
        if wanted in parts and (not only or name.lower() in only):
            tasks.append((name, reader, {"name": name}))
    return tasks


def load_never_show():
    """Companies listed under never_show in companies.yaml, as job_key() names them."""
    cfg = yaml.safe_load((ROOT / "companies.yaml").read_text(encoding="utf-8"))
    return {job_key(name, "") for name in cfg.get("never_show") or []}


def new_stats(fetched=0, error="", complete=False):
    return {"fetched": fetched, "error": error, "complete": complete, "fallbacks": 0, "conflicts": 0,
            "matched": 0, "kept": 0, "new": 0, "closed": 0, "copies": 0, "elsewhere": 0, "unread": 0}


def check_ids(found, st):
    """Per-source ID self-check. A posting listed twice is kept once. Two *different* jobs
    sharing an ID (a feed bug) are both kept under distinguishable IDs and counted, so a broken
    feed can create a duplicate row but can never hide a job."""
    out, first_title = [], {}
    for j in found:
        st["fallbacks"] += j.id_fallback
        if j.job_id in first_title:
            if first_title[j.job_id] == j.title:
                continue
            st["conflicts"] += 1
            j.job_id = f"{j.job_id}~{hashlib.sha1(j.title.encode()).hexdigest()[:6]}"
        first_title[j.job_id] = j.title
        out.append(j)
    return out


def fetch_all(tasks):
    """Run every reader in parallel. Returns (jobs, stats{company: {...}})."""
    stats, jobs = {}, []

    def run(task):
        name, reader, cfg = task
        try:
            found, complete = reader(cfg)
            return name, found, complete, "", cfg.get("unread", 0)
        except Exception as e:  # one broken source must not stop the others
            return name, [], False, f"{type(e).__name__}: {str(e)[:150]}", cfg.get("unread", 0)

    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, found, complete, err, unread in pool.map(run, tasks):
            for j in found:
                j.feed = name
            st = stats[name] = new_stats(len(found), err, complete and not err)
            st["unread"] = unread    # a reader that can tell sets cfg["unread"]: jobs it was told of but never shown
            jobs.extend(check_ids(found, st))
            flags = "".join([f"  ERROR {err}" if err else "", "" if st["complete"] or err else "  (partial read)",
                             f"  {st['fallbacks']} without ID" if st["fallbacks"] else "",
                             f"  {st['conflicts']} ID conflicts" if st["conflicts"] else "",
                             f"  up to {unread} more listed but out of reach" if unread else ""])
            log.info("%-16s %4d jobs%s", name, len(found), flags)
    return jobs, stats


def select(jobs, stats, tracker: Tracker, gemini=None, never_show=frozenset()):
    """Apply the checks to postings the sheet doesn't know yet.
    Returns (rows to add newest first, jobs dropped on experience)."""
    cutoff = today() - timedelta(days=MAX_AGE_DAYS)
    candidates, status_counts = [], Counter()
    for j in jobs:
        if j.posted and j.posted < cutoff:
            continue
        if not (is_india(j.location) or j.source in ("Workday", "SmartRecruiters")):
            continue
        if not is_software(j.title):
            continue
        if job_key(j.company, "") in never_show:
            status_counts["never show"] += 1
            continue
        stats[j.feed]["matched"] += 1
        status = tracker.status_of(j.job_id)
        if status == "new" and j.feed in BOARDS:
            row = tracker.other_site_match(j)
            if row:   # a job board listing an opening the sheet already has from another site
                row.attach_from_other_site(j)
                stats[j.feed]["elsewhere"] += 1
                status = "elsewhere"
        status_counts[status] += 1
        if status in ("known", "dropped", "elsewhere"):
            continue
        j.reposted = status == "reposted"
        candidates.append(j)
    log.info("India + software postings: %d already in the sheet, %d judged and dropped before, "
             "%d already in the sheet from another site, %d new, %d reposted, %d from never_show companies",
             status_counts["known"], status_counts["dropped"], status_counts["elsewhere"], status_counts["new"],
             status_counts["reposted"], status_counts["never show"])

    # Load descriptions only for the survivors, in parallel.
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda j: j.ensure_description(), candidates))
    log.info("Loaded %d descriptions in %.0fs", len(candidates), time.monotonic() - t0)

    # Same company + title + identical description as a row already in the sheet:
    #  - that row still open  -> the same opening posted again (extra headcount): attach, no new row
    #  - that row closed      -> the job was taken down and posted again: new row, marked Reposted
    fresh = []
    for j in candidates:
        twin = tracker.twin_of(j, fingerprint(j.description))
        if twin and twin.is_open and not j.reposted:
            twin.attach(j)
            stats[j.feed]["copies"] += 1
            log.info("  copy of open row %d: %s | %s", twin.number, j.company, j.title[:60])
            continue
        if twin and not twin.is_open:
            j.reposted = True
        fresh.append(j)
    candidates = fresh

    gemini = gemini or Gemini()
    kept, dropped = [], []
    checked = [(j, *experience_check(j.title, j.description)) for j in candidates]
    log.info("Experience check: %d keep, %d drop, %d unclear -> Gemini",
             *(sum(1 for c in checked if c[1] == d) for d in ("keep", "drop", "unclear")))
    for j, decision, label, reason in checked:
        if decision == "unclear":
            verdict = gemini.classify(j.title, j.description) if gemini.available() else None
            log.info("  AI %-14s %-50s -> %s", j.company[:14], j.title[:50], verdict)
            if verdict is None:
                decision, reason = "keep", f"{reason}; check the description"
            else:
                decision, reason = ("keep" if verdict[0] else "drop"), f"AI: {verdict[1]}"
            # The description asks for years of experience but also invites freshers: a long shot.
            label = f"{STRETCH}{label}"
        if decision == "keep" and label == "Not mentioned" and j.feed in NEEDS_STATED_EXPERIENCE:
            decision = "drop"
        if decision != "keep":
            dropped.append(j)
            continue
        j.experience, j.why_kept = label, ("Reposted. " if j.reposted else "") + reason
        kept.append(j)

    rows = merge_copies(kept)
    for j in rows:
        stats[j.feed]["kept"] += 1
        stats[j.feed]["new"] += 1
    return sorted(rows, key=lambda j: (j.posted or today()), reverse=True), dropped


def merge_copies(kept):
    """One row per opening.
    1. Identical copies on one site (the same opening posted once per city): same Greenhouse
       internal job, or same company + title + identical description. Merged only within a run,
       so a repost on a later day always gets its own row.
    2. The same opening on two different sites (company feed + a job board): same company +
       title, only ever across sources, never two postings from the same site."""
    def copy_key(j):
        if j.group_id:
            return ("group", j.group_id)
        fp = fingerprint(j.description)
        if not fp:
            return ("id", j.job_id)   # nothing to compare: never merge on an empty description
        return ("desc", j.source, job_key(j.company, j.title), fp)

    by_copy, rows = {}, []
    for j in kept:
        k = copy_key(j)
        if k in by_copy:
            _absorb(by_copy[k], j)
        else:
            by_copy[k] = j
            rows.append(j)

    # The company's own posting comes first, so its link is the one kept.
    board_rank = lambda j: list(BOARDS).index(j.feed) + 1 if j.feed in BOARDS else 0
    by_title, out = defaultdict(list), []
    for j in sorted(rows, key=board_rank):
        k = job_key(j.company, j.title)
        other = next((r for r in by_title[k] if r.source != j.source and j.source not in r.also_seen_on), None)
        if other:
            _absorb(other, j)
            other.also_seen_on.append(j.source)
        else:
            by_title[k].append(j)
            out.append(j)
    return out


def _absorb(row, copy):
    for place in (copy.location or "").split("; "):
        if place and place not in row.location:
            row.location = f"{row.location}; {place}" if row.location else place
    row.extra_ids += [i for i in copy.all_ids if i not in row.all_ids]
    row.reposted = row.reposted or copy.reposted


@dataclass
class Update:
    """What one sheet update did, for the summary email."""
    new: list
    closed: list
    copies: int
    broken: set
    id_problems: dict


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobscan")
    ap.add_argument("--group", default="cloud", choices=list(GROUPS),
                    help="cloud = company feeds + job boards (default, GitHub Actions); laptop = boards "
                         "that block GitHub's servers; feeds; boards")
    ap.add_argument("--if-due", action="store_true",
                    help="laptop group only: read just the boards that are due, each saved as soon as it's "
                         "read (the scheduled task; see laptop.py)")
    ap.add_argument("--dry-run", action="store_true", help="print results; don't touch the sheet or email")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--only", help="comma-separated company names/slugs or board names, for testing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    only = {s.strip().lower() for s in args.only.split(",")} if args.only else None
    if args.if_due:
        if args.group != "laptop":
            ap.error("--if-due is for --group laptop")
        return run_due(only, dry_run=args.dry_run, email=not args.no_email)
    tasks = load_tasks(args.group, only)
    log.info("Reading %d sources...", len(tasks))
    jobs, stats = fetch_all(tasks)

    if args.dry_run:
        print_new(jobs, stats)
        return

    from .sheet import Sheet
    sheet = Sheet()
    # GitHub's runs and the laptop's both rewrite the sheet by row number: one at a time.
    with sheet.lock(f"{args.group} run on {platform.node()}"):
        done = update_sheet(sheet, args.group, jobs, stats)
    if not args.no_email:
        send_email(sheet, args.group, [done])
    log.info("Done: %d new rows in %s", len(done.new), sheet.url)


def print_new(jobs, stats):
    new, _ = select(jobs, stats, Tracker([], [], today()), never_show=load_never_show())
    log.info("\n%d jobs fetched, %d India + software, %d new rows",
             len(jobs), sum(s["matched"] for s in stats.values()), len(new))
    for j in new:
        log.info("  %-11s | %-14s | %-50s | %-13s | %s", j.source[:11], j.company[:14], j.title[:50],
                 j.experience, j.why_kept)


def run_due(only=None, dry_run=False, email=True):
    """The laptop's scheduled task, started at log in, unlock, when the internet connects and
    every hour. Reads the laptop boards that are due one at a time, and saves each to the sheet
    as soon as it's read, so a run cut short keeps the boards it finished (see laptop.py)."""
    from . import laptop
    from .sheet import Sheet

    log.info("%s laptop check", f"{datetime.now(IST):%Y-%m-%d %H:%M}")
    problem = laptop.connection_problem(laptop.GOOGLE)
    if problem:
        log.info("Nothing read: %s. Trying again at the next start.", problem)
        return
    if laptop.battery_low():
        log.info("Nothing read: on battery at %d%% or less. Trying again at the next start.", laptop.MIN_BATTERY)
        return

    state = laptop.State.load()
    sheet = Sheet()
    state.seed(sheet.last_good_reads(HOME_IP_ONLY))
    now, due = datetime.now(IST), []
    for task in load_tasks("laptop", only):
        name = task[0]
        why = state.why_not_due(name, now)
        if not why:   # a bad network doesn't use up one of the day's tries
            why = laptop.connection_problem(laptop.HOSTS[name])
        if why:
            log.info("%-9s skipped: %s", name, why)
        else:
            due.append(task)

    updates, read = [], 0
    with laptop.keep_awake():
        for name, reader, cfg in due:
            started = datetime.now(IST)
            days, missed = state.window_days(name, started), state.days_missed(name, started)
            log.info("\n%s: reading postings from the last %d days%s", name, days,
                     f" ({missed} older days were missed: the laptop was off too long)" if missed else "")
            cfg.update(days=days, deadline=time.monotonic() + laptop.SOURCE_MINUTES * 60)
            if dry_run:
                print_new(*fetch_all([(name, reader, cfg)]))
                continue
            state.tried(name, started)
            state.save()
            jobs, stats = fetch_all([(name, reader, cfg)])
            try:
                with sheet.lock(f"laptop run on {platform.node()}"):
                    updates.append(update_sheet(sheet, "laptop", jobs, stats))
            except Exception as e:   # not saved: the board is tried again at a later start
                log.info("%s was read but not saved: %s: %s", name, type(e).__name__, e)
                stats[name]["error"] = stats[name]["error"] or f"not saved: {type(e).__name__}: {str(e)[:150]}"
            error = stats[name]["error"] or ("" if stats[name]["fetched"] else "no jobs")
            if error:
                state.failed(name, datetime.now(IST), error, pause=error.startswith("SignInNeeded"))
            else:
                state.succeeded(name, started)
                read += 1
            state.save()

    # Only a session that read something emails: failures are retried later, are in the Run log,
    # and the daily GitHub email warns once a board has gone 2 days unread.
    if read and email:
        send_email(sheet, "laptop", updates)
    if updates:
        log.info("Done: %d new rows in %s", sum(len(u.new) for u in updates), sheet.url)


def send_email(sheet, group, updates):
    """One summary for the run, with a warning for laptop boards not read for 2+ days."""
    from . import laptop
    from .notify import send_summary
    overdue = laptop.overdue(sheet.last_good_reads(HOME_IP_ONLY), datetime.now(IST), HOME_IP_ONLY)
    sent = send_summary(group, [j for u in updates for j in u.new], sheet.url, set().union(*(u.broken for u in updates)),
                        closed_rows=[r for u in updates for r in u.closed],
                        id_problems={k: v for u in updates for k, v in u.id_problems.items()},
                        copies=sum(u.copies for u in updates), overdue=overdue)
    log.info("Summary email %s", "sent" if sent else "skipped (Gmail not configured)")


def update_sheet(sheet, group, jobs, stats) -> Update:
    action = sheet.prepare(jobs)
    if action != "ok":
        log.info("Sheet layout: %s", action)
    tracker = sheet.tracker()

    new, dropped = select(jobs, stats, tracker, never_show=load_never_show())
    log.info("\n%d jobs fetched, %d India + software, %d new rows",
             len(jobs), sum(s["matched"] for s in stats.values()), len(new))

    # With --only, sources outside the selection weren't read: their rows must not be closed.
    complete = {n for n, s in stats.items() if s["complete"]}
    reposted_ids = {i for j in new if j.reposted for i in j.all_ids}
    closed = tracker.refresh({j.job_id for j in jobs}, complete, reposted_ids)
    for r in closed:
        stats[r.feed]["closed"] += 1
    changed = sheet.write_statuses(tracker)      # before inserting rows, which shifts row numbers
    sheet.add_jobs(new)
    sheet.add_seen(dropped)
    copies = sum(s["copies"] for s in stats.values())
    elsewhere = sum(s["elsewhere"] for s in stats.values())
    log.info("Status changes on existing rows: %d (closed today: %d); identical postings attached "
             "to existing rows: %d; job-board listings of rows from another site: %d",
             changed, len(closed), copies, elsewhere)

    run_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    broken_before = sheet.previous_zero_sources()
    sheet.add_log([[run_at, group, name, s["fetched"], s["matched"], s["kept"], s["new"], s["error"],
                    "yes" if s["complete"] else "no", s["fallbacks"], s["conflicts"], s["closed"], s["unread"]]
                   for name, s in sorted(stats.items())])
    broken_now = {n for n, s in stats.items() if s["fetched"] == 0 or s["error"]}
    id_problems = {n: (s["fallbacks"], s["conflicts"]) for n, s in stats.items() if s["fallbacks"] or s["conflicts"]}
    return Update(new, closed, copies, broken_now & broken_before, id_problems)

if __name__ == "__main__":
    main()

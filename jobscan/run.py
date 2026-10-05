"""Entry point.

    python -m jobscan                     # company feeds + open job boards -> sheet -> email
    python -m jobscan --group laptop      # boards that block GitHub's servers (Foundit)
    python -m jobscan --group boards      # every job board (or: --group feeds)
    python -m jobscan --dry-run           # print what would be added; no sheet, no email
    python -m jobscan --only meesho,nvidia,unstop
"""
import argparse
import hashlib
import logging
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import yaml

from .core import IST, ROOT, fingerprint, job_key, today
from .filters import Gemini, experience_check, is_india, is_software
from .sources.boards import BOARDS, HOME_IP_ONLY
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
            for c in entries or []:
                reader = READERS.get(system) or CUSTOM.get(c.get("reader", ""))
                if reader and (not only or c["name"].lower() in only or c.get("slug", c.get("tenant", "")) in only):
                    tasks.append((c["name"], reader, c))
    for name, reader in BOARDS.items():
        wanted = "home-IP boards" if name in HOME_IP_ONLY else "boards"
        if wanted in parts and (not only or name.lower() in only):
            tasks.append((name, reader, {"name": name}))
    return tasks


def new_stats(fetched=0, error="", complete=False):
    return {"fetched": fetched, "error": error, "complete": complete, "fallbacks": 0, "conflicts": 0,
            "matched": 0, "kept": 0, "new": 0, "closed": 0, "copies": 0, "elsewhere": 0}


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
            return name, found, complete, ""
        except Exception as e:  # one broken source must not stop the others
            return name, [], False, f"{type(e).__name__}: {str(e)[:150]}"

    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, found, complete, err in pool.map(run, tasks):
            for j in found:
                j.feed = name
            st = stats[name] = new_stats(len(found), err, complete and not err)
            jobs.extend(check_ids(found, st))
            flags = "".join([f"  ERROR {err}" if err else "", "" if st["complete"] or err else "  (partial read)",
                             f"  {st['fallbacks']} without ID" if st["fallbacks"] else "",
                             f"  {st['conflicts']} ID conflicts" if st["conflicts"] else ""])
            log.info("%-16s %4d jobs%s", name, len(found), flags)
    return jobs, stats


def select(jobs, stats, tracker: Tracker, gemini=None):
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
             "%d already in the sheet from another site, %d new, %d reposted", status_counts["known"],
             status_counts["dropped"], status_counts["elsewhere"], status_counts["new"], status_counts["reposted"])

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobscan")
    ap.add_argument("--group", default="cloud", choices=list(GROUPS),
                    help="cloud = company feeds + job boards (default, GitHub Actions); laptop = boards "
                         "that block GitHub's servers; feeds; boards")
    ap.add_argument("--dry-run", action="store_true", help="print results; don't touch the sheet or email")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--only", help="comma-separated company names/slugs or board names, for testing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    only = {s.strip().lower() for s in args.only.split(",")} if args.only else None
    tasks = load_tasks(args.group, only)
    log.info("Reading %d sources...", len(tasks))
    jobs, stats = fetch_all(tasks)

    sheet = None
    if args.dry_run:
        tracker = Tracker([], [], today())
    else:
        from .sheet import Sheet
        sheet = Sheet()
        action = sheet.prepare(jobs)
        if action != "ok":
            log.info("Sheet layout: %s", action)
        tracker = sheet.tracker()

    new, dropped = select(jobs, stats, tracker)
    log.info("\n%d jobs fetched, %d India + software, %d new rows",
             len(jobs), sum(s["matched"] for s in stats.values()), len(new))
    if args.dry_run:
        for j in new:
            log.info("  %-11s | %-14s | %-50s | %-13s | %s", j.source[:11], j.company[:14], j.title[:50],
                     j.experience, j.why_kept)
        return

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
    sheet.add_log([[run_at, args.group, name, s["fetched"], s["matched"], s["kept"], s["new"], s["error"],
                    "yes" if s["complete"] else "no", s["fallbacks"], s["conflicts"], s["closed"]]
                   for name, s in sorted(stats.items())])
    broken_now = {n for n, s in stats.items() if s["fetched"] == 0 or s["error"]}
    id_problems = {n: (s["fallbacks"], s["conflicts"]) for n, s in stats.items() if s["fallbacks"] or s["conflicts"]}
    if not args.no_email:
        from .notify import send_summary
        sent = send_summary(args.group, new, sheet.url, broken_now & broken_before,
                            closed_rows=closed, id_problems=id_problems, copies=copies)
        log.info("Summary email %s", "sent" if sent else "skipped (Gmail not configured)")
    log.info("Done: %d new rows in %s", len(new), sheet.url)


if __name__ == "__main__":
    main()

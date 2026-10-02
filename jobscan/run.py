"""Entry point.

    python -m jobscan                     # company feeds -> sheet -> email
    python -m jobscan --dry-run           # print what would be added; no sheet, no email
    python -m jobscan --only meesho,nvidia
"""
import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import yaml

from .core import IST, ROOT, env, today
from .filters import Gemini, experience_check, is_india, is_software
from .sheet import Sheet, job_key
from .sources.feeds import CUSTOM, READERS

log = logging.getLogger("jobscan")
MAX_AGE_DAYS = 30
# When the same job shows up in several places, keep the link from the first source here.
SOURCE_PRIORITY = ["Greenhouse", "Lever", "Ashby", "SmartRecruiters", "Workday", "Amazon Jobs",
                   "Microsoft Careers"]


def load_feed_tasks(only=None):
    cfg = yaml.safe_load((ROOT / "companies.yaml").read_text(encoding="utf-8"))
    tasks = []
    for system, entries in cfg.items():
        for c in entries or []:
            reader = READERS.get(system) or CUSTOM.get(c.get("reader", ""))
            if reader and (not only or c["name"].lower() in only or c.get("slug", c.get("tenant", "")) in only):
                tasks.append((c["name"], reader, c))
    return tasks


def fetch_all(tasks):
    """Run every reader in parallel. Returns (jobs, stats{company: {fetched, error}})."""
    stats, jobs = {}, []

    def run(task):
        name, reader, cfg = task
        try:
            return name, reader(cfg), ""
        except Exception as e:  # one broken source must not stop the others
            return name, [], f"{type(e).__name__}: {str(e)[:150]}"

    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, found, err in pool.map(run, tasks):
            stats[name] = {"fetched": len(found), "error": err, "matched": 0, "kept": 0, "new": 0}
            jobs.extend(found)
            log.info("%-16s %4d jobs%s", name, len(found), f"  ERROR {err}" if err else "")
    return jobs, stats


def select(jobs, stats, seen_keys):
    """Apply the four checks. Returns (jobs to add newest first, keys of jobs dropped on experience)."""
    cutoff = today() - timedelta(days=MAX_AGE_DAYS)
    candidates = []
    for j in jobs:
        if j.posted and j.posted < cutoff:
            continue
        if not (is_india(j.location) or j.source in ("Workday", "SmartRecruiters")):
            continue
        if not is_software(j.title):
            continue
        stats[j.company]["matched"] += 1
        if job_key(j.company, j.title) in seen_keys:      # check 1: already in the sheet
            continue
        candidates.append(j)

    # Load descriptions only for the survivors, in parallel.
    log.info("%d India + software jobs not yet in the sheet; loading descriptions...", len(candidates))
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda j: j.ensure_description(), candidates))
    log.info("Descriptions loaded in %.0fs", time.monotonic() - t0)

    gemini, kept, dropped = Gemini(), [], set()
    checked = [(j, *experience_check(j.title, j.description)) for j in candidates]
    unclear = sum(1 for c in checked if c[1] == "unclear")
    log.info("Experience check: %d keep, %d drop, %d unclear -> Gemini",
             sum(1 for c in checked if c[1] == "keep"), sum(1 for c in checked if c[1] == "drop"), unclear)
    for j, decision, label, reason in checked:
        if decision == "unclear":
            verdict = gemini.classify(j.title, j.description) if gemini.available() else None
            log.info("  AI %-14s %-50s -> %s", j.company[:14], j.title[:50], verdict)
            if verdict is None:
                decision, reason = "keep", f"{reason}; check the description"
            else:
                decision, reason = ("keep" if verdict[0] else "drop"), f"AI: {verdict[1]}"
        if decision != "keep":
            dropped.add(job_key(j.company, j.title))
            continue
        j.experience, j.why_kept = label, reason
        kept.append(j)

    # Merge the same job seen in several places (same company + title).
    merged = {}
    for j in sorted(kept, key=lambda j: SOURCE_PRIORITY.index(j.source)
                    if j.source in SOURCE_PRIORITY else 99):
        k = job_key(j.company, j.title)
        if k not in merged:
            merged[k] = j
            continue
        first = merged[k]
        if j.location and j.location not in first.location:
            first.location = f"{first.location}; {j.location}"
        if j.source != first.source and j.source not in first.also_seen_on:
            first.also_seen_on.append(j.source)
    out = sorted(merged.values(), key=lambda j: (j.posted or today()), reverse=True)
    for j in out:
        stats[j.company]["kept"] += 1
        stats[j.company]["new"] += 1
    return out, dropped


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobscan")
    ap.add_argument("--group", default="feeds", choices=["feeds"])
    ap.add_argument("--dry-run", action="store_true", help="print results; don't touch the sheet or email")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--only", help="comma-separated company names/slugs, for testing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    only = {s.strip().lower() for s in args.only.split(",")} if args.only else None
    tasks = load_feed_tasks(only)
    log.info("Reading %d company feeds...", len(tasks))
    jobs, stats = fetch_all(tasks)

    sheet = None if args.dry_run else Sheet()
    seen = sheet.recent_keys() if sheet else set()
    new, dropped = select(jobs, stats, seen)

    total_matched = sum(s["matched"] for s in stats.values())
    log.info("\n%d jobs fetched, %d India + software, %d new and passing the experience check",
             len(jobs), total_matched, len(new))
    if args.dry_run:
        for j in new:
            log.info("  %-14s | %-50s | %-13s | %s", j.company[:14], j.title[:50], j.experience, j.why_kept)
        return

    sheet.add_jobs(new)
    sheet.add_seen(dropped)
    run_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    broken_before = sheet.previous_zero_sources(args.group)
    sheet.add_log([[run_at, args.group, name, s["fetched"], s["matched"], s["kept"], s["new"], s["error"]]
                   for name, s in sorted(stats.items())])
    broken_now = {n for n, s in stats.items() if s["fetched"] == 0 or s["error"]}
    if not args.no_email:
        from .notify import send_summary
        sent = send_summary(args.group, new, sheet.url, broken_now & broken_before)
        log.info("Summary email %s", "sent" if sent else "skipped (Gmail not configured)")
    log.info("Done: %d new rows in %s", len(new), sheet.url)


if __name__ == "__main__":
    main()

"""Live audit of posting IDs across every company feed. No credentials needed.

    python -m jobscan.audit

Exits with status 1 if any posting lacks an ID, two different jobs share one, or more than
MAX_FEED_ERRORS feeds fail, so a scheduled CI run turns red (and GitHub emails the owner)
when a hiring system changes its API.
"""
import logging
import sys

from .run import fetch_all, load_feed_tasks

MAX_FEED_ERRORS = 3


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    jobs, stats = fetch_all(load_feed_tasks())
    print(f"{'company':18} {'postings':>8} {'no ID':>6} {'conflicts':>9} {'complete':>8}  error")
    for name, s in sorted(stats.items()):
        print(f"{name:18} {s['fetched']:8} {s['fallbacks']:6} {s['conflicts']:9} "
              f"{'yes' if s['complete'] else 'no':>8}  {s['error']}")
    fallbacks = sum(s["fallbacks"] for s in stats.values())
    conflicts = sum(s["conflicts"] for s in stats.values())
    errors = [n for n, s in stats.items() if s["error"]]
    print(f"\n{len(jobs)} postings from {len(stats)} feeds: {fallbacks} without ID, "
          f"{conflicts} ID conflicts, {len(errors)} feeds failed")
    if fallbacks or conflicts or len(errors) > MAX_FEED_ERRORS:
        print("AUDIT FAILED")
        return 1
    print("AUDIT PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""What the sheet already knows about each posting, and how today's feeds change that.

Pure logic with no Google Sheets calls, so it can be unit-tested.

A posting is identified by its hiring system's own ID (core.posting_id), never by its title:
  - different openings that share a title ("Software Engineer" x 13 at Cisco) are separate;
  - a job taken down and posted again gets a new ID, so it appears as a new row.
One refinement: a new posting with the same company, title and an identical description as a
row that is still OPEN is the same opening posted again for extra headcount; its ID is attached
to that row instead of adding a duplicate. If the original row is CLOSED, the identical posting
is a repost and gets a new row labelled "Reposted".

Job boards re-list jobs from company sites with their own IDs and wording. A new board posting
with the same company and title as an OPEN row from another site is attached to that row and
the board is added to its "Also seen on". Only board postings are matched this way: a company
site's own posting always gets its own row unless its ID is known.

"Open?" column values written by refresh():
  "Open"                          seen in today's feed
  "Closed 2026-10-05"             missing from a complete read of its company's feed on that date
  "Closed (reposted 2026-10-07)"  superseded by a newer row for the same posting ID
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .core import job_key
from .sources.boards import BOARDS

REPOST_GAP_DAYS = 2   # an ID that comes back after being closed this long counts as a repost
OPEN = "Open"
REPOSTED_PREFIX = "Closed (reposted"
BOARD_SYSTEMS = {name.lower() for name in BOARDS}   # "unstop:1762594" -> "unstop"


def _system(job_id: str) -> str:
    return job_id.split(":", 1)[0]


@dataclass
class Row:
    number: int                 # sheet row number (header is row 1)
    ids: list
    company: str
    status: str = ""            # current "Open?" cell
    title: str = ""
    user_status: str = ""       # your Status column (Applied, Skip, ...)
    fingerprint: str = ""       # core.fingerprint of the description
    location: str = ""
    source: str = ""            # Source column: "Greenhouse", "Unstop", ...
    feed: str = ""              # Run log line that can close it: the company, or the board
    also_seen_on: str = ""      # "Also seen on" column: "Unstop, Hirist"
    new_status: Optional[str] = None
    added: list = field(default_factory=list)   # postings attached this run

    def __post_init__(self):
        self.feed = self.feed or self.company

    @property
    def closed_on(self) -> Optional[date]:
        if self.status.startswith("Closed ") and not self.status.startswith(REPOSTED_PREFIX):
            try:
                return date.fromisoformat(self.status[7:17])
            except ValueError:
                return None
        return None

    @property
    def superseded(self) -> bool:
        return self.status.startswith(REPOSTED_PREFIX)

    @property
    def is_open(self) -> bool:
        return not self.status.startswith("Closed")

    def attach(self, job) -> None:
        """Record an identical posting of this opening (extra headcount) on this row."""
        self.added.append(job)
        for place in (job.location or "").split("; "):
            if place and place not in self.location:
                self.location = f"{self.location}; {place}" if self.location else place

    def attach_from_other_site(self, job) -> None:
        """Record the same opening listed on a job board: its ID, and the board in Also seen on."""
        self.added.append(job)
        if job.source not in self.sites:
            self.also_seen_on = f"{self.also_seen_on}, {job.source}" if self.also_seen_on else job.source

    @property
    def sites(self) -> list:
        return [self.source] + [s.strip() for s in self.also_seen_on.split(",") if s.strip()]

    @property
    def all_ids(self) -> list:
        out = list(self.ids)
        for j in self.added:
            out += [i for i in j.all_ids if i not in out]
        return out


class Tracker:
    def __init__(self, rows, dropped_ids, today: date):
        self.rows = rows                         # newest first, as in the sheet
        self.dropped = set(dropped_ids)          # judged before and failed the experience check
        self.today = today
        self.by_id = {}
        self.by_content = {}                     # (company+title, fingerprint) -> rows, newest first
        self.by_title = {}                       # company+title -> rows, newest first
        for r in rows:
            if r.superseded:
                continue
            for i in r.ids:
                self.by_id.setdefault(i, r)      # newest row wins
            if r.fingerprint:
                self.by_content.setdefault((job_key(r.company, r.title), r.fingerprint), []).append(r)
            self.by_title.setdefault(job_key(r.company, r.title), []).append(r)

    def status_of(self, job_id: str) -> str:
        """'new' | 'known' | 'reposted' | 'dropped'"""
        if job_id in self.dropped:
            return "dropped"
        row = self.by_id.get(job_id)
        if row is None:
            return "new"
        closed = row.closed_on
        if closed and (self.today - closed).days >= REPOST_GAP_DAYS:
            return "reposted"
        return "known"   # still open, or gone so briefly it was most likely a feed glitch

    def twin_of(self, job, fp: str):
        """An existing row with the same company, title and identical description, or None.
        Open twins are preferred: that means the same opening is still live."""
        rows = self.by_content.get((job_key(job.company, job.title), fp)) if fp else None
        if not rows:
            return None
        return next((r for r in rows if r.is_open), rows[0])

    def other_site_match(self, job):
        """An OPEN row for the same company + title found on a different site, which doesn't
        already hold a posting from this job's site (two postings from one site are two
        openings), or None."""
        system = _system(job.job_id)
        for r in self.by_title.get(job_key(job.company, job.title), []):
            if r.is_open and job.source not in r.sites and not any(_system(i) == system for i in r.all_ids):
                return r
        return None

    def refresh(self, fetched_ids: set, complete_feeds: set, reposted_ids: set):
        """Work out each existing row's new "Open?" value. Returns the rows closed today.
        complete_feeds: Run log names (companies or boards) whose whole listing was read today."""
        closed_today = []
        for r in self.rows:
            if r.superseded:
                continue
            # A job board that still lists a job the company has taken down must not keep it open:
            # only IDs from the row's own site count.
            home = _system(r.ids[0])
            own = [i for i in r.all_ids if _system(i) == home or _system(i) not in BOARD_SYSTEMS]
            if any(i in reposted_ids and self.by_id.get(i) is r for i in r.ids):
                r.new_status = f"{REPOSTED_PREFIX} {self.today.isoformat()})"
            elif any(i in fetched_ids for i in own):
                r.new_status = OPEN
            elif r.feed in complete_feeds and r.is_open:
                r.new_status = f"Closed {self.today.isoformat()}"
                closed_today.append(r)
        return closed_today

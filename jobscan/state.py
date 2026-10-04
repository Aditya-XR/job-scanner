"""What the sheet already knows about each posting, and how today's feeds change that.

Pure logic with no Google Sheets calls, so it can be unit-tested.

A posting is identified by its hiring system's own ID (core.posting_id), never by its title:
  - different openings that share a title ("Software Engineer" x 13 at Cisco) are separate;
  - a job taken down and posted again gets a new ID, so it appears as a new row.
One refinement: a new posting with the same company, title and an identical description as a
row that is still OPEN is the same opening posted again for extra headcount; its ID is attached
to that row instead of adding a duplicate. If the original row is CLOSED, the identical posting
is a repost and gets a new row labelled "Reposted".

"Open?" column values written by refresh():
  "Open"                          seen in today's feed
  "Closed 2026-10-05"             missing from a complete read of its company's feed on that date
  "Closed (reposted 2026-10-07)"  superseded by a newer row for the same posting ID
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .core import job_key

REPOST_GAP_DAYS = 2   # an ID that comes back after being closed this long counts as a repost
OPEN = "Open"
REPOSTED_PREFIX = "Closed (reposted"


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
    new_status: Optional[str] = None
    added: list = field(default_factory=list)   # identical postings attached this run

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
        for r in rows:
            if r.superseded:
                continue
            for i in r.ids:
                self.by_id.setdefault(i, r)      # newest row wins
            if r.fingerprint:
                self.by_content.setdefault((job_key(r.company, r.title), r.fingerprint), []).append(r)

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

    def refresh(self, fetched_ids: set, complete_companies: set, reposted_ids: set):
        """Work out each existing row's new "Open?" value. Returns the rows closed today."""
        closed_today = []
        for r in self.rows:
            if r.superseded:
                continue
            if any(i in reposted_ids and self.by_id.get(i) is r for i in r.ids):
                r.new_status = f"{REPOSTED_PREFIX} {self.today.isoformat()})"
            elif any(i in fetched_ids for i in r.all_ids):
                r.new_status = OPEN
            elif r.company in complete_companies and r.is_open:
                r.new_status = f"Closed {self.today.isoformat()}"
                closed_today.append(r)
        return closed_today

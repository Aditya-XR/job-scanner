"""Shared pieces: settings from .env, the Job record, and an HTTP session with retries."""
import hashlib
import html
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = timezone(timedelta(hours=5, minutes=30))


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def today() -> date:
    return datetime.now(IST).date()


def posting_id(system: str, scope: str, native, url: str = "", *fallback_parts) -> tuple:
    """The identity of one posting: the hiring system's own ID, e.g.
    "greenhouse:rubrik:8166537" or "workday:cisco:/job/Bangalore-India/Software-Engineer_2008391".

    Returns (id, used_fallback). If a feed ever omits its ID we fall back to the apply link,
    then to a hash of the visible fields. A fallback can at worst create a duplicate row;
    it can never hide a job, and every fallback is counted in the Run log.
    """
    if native not in (None, ""):
        return (f"{system}:{scope}:{native}" if scope else f"{system}:{native}"), False
    if url:
        return f"url:{url}", True
    digest = hashlib.sha1("|".join(map(str, fallback_parts)).encode()).hexdigest()[:16]
    return f"hash:{digest}", True


def fingerprint(description: str) -> str:
    """Identical-description check that ignores only case and whitespace (Broadcom's two copies of
    one role differ by a few line-break spaces). Numbers are kept on purpose: "0-1 years" vs
    "5+ years" must never look identical. Empty description -> "" (never matches anything)."""
    text = " ".join((description or "").lower().split())
    return hashlib.sha1(text.encode()).hexdigest()[:16] if text else ""


# Words job boards add to a company's name: "Walmart Global Tech India Pvt. Ltd." -> "walmart"
_COMPANY_NOISE = re.compile(
    r"\b(private|pvt|limited|ltd|inc|incorporated|llp|llc|corp|corporation|co|company|"
    r"technologies|technology|tech|global|india|solutions|services|software|labs)\b")


def job_key(company: str, title: str) -> str:
    """Company + title, normalized. Only used to spot the same opening on two different
    sites (e.g. a company feed and a job board), never to decide whether a posting is new."""
    norm = lambda s: " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())
    name = norm(_COMPANY_NOISE.sub(" ", norm(company))) or norm(company)
    return f"{name}|{norm(title)}"


@dataclass
class Job:
    source: str                      # where we read it: "Greenhouse", "Workday", "Unstop"...
    company: str
    title: str
    location: str
    url: str                         # apply link shown in the sheet
    job_id: str = ""                 # see posting_id()
    id_fallback: bool = False        # True if the feed gave no ID and we had to improvise
    group_id: str = ""               # same opening posted several times (e.g. one per city)
    posted: Optional[date] = None
    description: str = ""
    # Some feeds list jobs without the description; this fetches it only for jobs
    # that survive the cheap title/location filters.
    load_description: Optional[Callable[[], str]] = field(default=None, repr=False)
    experience: str = ""             # "0-1 yrs", "Not mentioned", ...
    why_kept: str = ""
    also_seen_on: list = field(default_factory=list)
    extra_ids: list = field(default_factory=list)  # IDs of identical copies merged into this row
    reposted: bool = False
    feed: str = ""                   # the Run log line it came from: a company feed or a job board

    @property
    def all_ids(self) -> list:
        return [self.job_id] + [i for i in self.extra_ids if i != self.job_id]

    def ensure_description(self) -> None:
        if not self.description and self.load_description:
            try:
                self.description = self.load_description() or ""
            except Exception:  # a broken detail page must not stop the run; the job is kept
                self.description = ""


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=32))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
        "Accept": "application/json",
    })
    return s


HTTP = make_session()


def get_json(url: str, **kw):
    r = HTTP.get(url, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def post_json(url: str, body: dict, **kw):
    r = HTTP.post(url, json=body, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def get_text(url: str, **kw) -> str:
    r = HTTP.get(url, timeout=30, **kw)
    r.raise_for_status()
    return r.text


class Throttle:
    """At most one request every `interval` seconds, shared by all threads. Job boards get
    one detail request per job, and the description loader runs 12 at a time."""

    def __init__(self, interval: float):
        self.interval, self._next, self._lock = interval, 0.0, threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            time.sleep(start - now)


def html_to_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse double-escapes its HTML
    text = BeautifulSoup(s, "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def json_ld_description(page_html: str) -> str:
    """Description text from the schema.org JobPosting block that job pages embed for Google
    Jobs, or "" if the page has none."""
    for script in BeautifulSoup(page_html, "html.parser").select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text(), strict=False)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return html_to_text(data.get("description", ""))
    return ""


def parse_date(value) -> Optional[date]:
    """Accepts ISO strings, epoch seconds/milliseconds, or 'September 24, 2026'."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(ts, IST).date()
        v = str(value).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", v):
            if "T" not in v:
                return date.fromisoformat(v[:10])
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.astimezone(IST).date() if dt.tzinfo else dt.date()
        return datetime.strptime(v, "%B %d, %Y").date()
    except (ValueError, OverflowError, OSError):
        return None

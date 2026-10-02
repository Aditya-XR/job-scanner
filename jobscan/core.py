"""Shared pieces: settings from .env, the Job record, and an HTTP session with retries."""
import html
import os
import re
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


@dataclass
class Job:
    source: str                      # where we read it: "Greenhouse", "Workday", "LinkedIn"...
    company: str
    title: str
    location: str
    url: str                         # apply link shown in the sheet
    posted: Optional[date] = None
    description: str = ""
    # Some feeds list jobs without the description; this fetches it only for jobs
    # that survive the cheap title/location filters.
    load_description: Optional[Callable[[], str]] = field(default=None, repr=False)
    experience: str = ""             # "0-1 yrs", "Not mentioned", ...
    why_kept: str = ""
    also_seen_on: list = field(default_factory=list)

    def ensure_description(self) -> None:
        if not self.description and self.load_description:
            try:
                self.description = self.load_description() or ""
            except requests.RequestException:
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


def html_to_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse double-escapes its HTML
    text = BeautifulSoup(s, "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n", text).strip()


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

"""When the laptop run reads each of its job boards (python -m jobscan --group laptop --if-due).

The laptop isn't on at a fixed time, so the scheduled task starts often: at log in, on unlock,
when the internet connects, and every hour (laptop/install_task.ps1). Each start reads only the
boards that are due: not read successfully today, not tried in the last hour, and with tries
left for today. A due board is asked for postings back to its own last good read, so the days
the laptop was off are caught up (up to MAX_DAYS).

The state lives in state/laptop.json (git-ignored). A board the file doesn't know yet (first
run, or the file was deleted) starts from its last good read in the sheet's Run log.
"""
import ctypes
import json
import socket
import ssl
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta

import certifi

from .core import ROOT

STATE_FILE = ROOT / "state" / "laptop.json"
MIN_DAYS, MAX_DAYS = 2, 7          # how far back a board is asked for postings
RETRY_AFTER = timedelta(hours=1)
MIN_GAP = timedelta(hours=6)       # just after midnight, last evening's read still counts as today's
MAX_TRIES = {"LinkedIn": 2}        # the burner account: as few sessions a day as possible
DEFAULT_TRIES = 3
SIGN_IN_PAUSE = timedelta(hours=48)
SOURCE_MINUTES = 20                # a board still reading after this stops and keeps what it has
MIN_BATTERY = 25                   # percent, when not plugged in
OVERDUE = timedelta(days=2)        # the email warns about a board not read for this long
HOSTS = {"Foundit": "www.foundit.in", "LinkedIn": "www.linkedin.com", "Naukri": "www.naukri.com"}
GOOGLE = "sheets.googleapis.com"


@dataclass
class Board:
    last_success: str = ""     # ISO times in IST
    last_try: str = ""
    tries_on: str = ""         # the day `tries` counts
    tries: int = 0
    last_error: str = ""
    paused_until: str = ""


def _at(s: str):
    return datetime.fromisoformat(s) if s else None


class State:
    def __init__(self, boards=None, path=None):
        self.boards, self.path = boards or {}, path or STATE_FILE

    @classmethod
    def load(cls, path=None):
        path = path or STATE_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        known = {f.name for f in fields(Board)}
        return cls({name: Board(**{k: v for k, v in b.items() if k in known}) for name, b in raw.items()}, path)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({n: asdict(b) for n, b in self.boards.items()}, indent=2), encoding="utf-8")
        for _ in range(20):
            try:
                tmp.replace(self.path)    # never a half-written file, even if the laptop dies here
                return
            except PermissionError:       # Windows: antivirus or the indexer can hold the file a moment
                time.sleep(0.1)
        tmp.replace(self.path)

    def board(self, name) -> Board:
        return self.boards.setdefault(name, Board())

    def seed(self, last_reads: dict):
        """Boards this file has never seen read successfully start from their last good read in
        the Run log, so the first run after installing still catches up."""
        for name, at in last_reads.items():
            b = self.board(name)
            if not b.last_success:
                b.last_success = at.isoformat()

    def why_not_due(self, name, now) -> str:
        """'' if the board is due, otherwise why not (for the log)."""
        b = self.board(name)
        paused, last, tried = _at(b.paused_until), _at(b.last_success), _at(b.last_try)
        if paused and now < paused:
            return f"paused until {paused:%d %b %H:%M}: {b.last_error[:120]}"
        if last and (last.date() == now.date() or now - last < MIN_GAP):
            return f"read at {last:%d %b %H:%M}"
        tries = b.tries if b.tries_on == now.date().isoformat() else 0
        if tries >= MAX_TRIES.get(name, DEFAULT_TRIES):
            return f"tried {tries} times today; last error: {b.last_error[:120]}"
        if tried and now - tried < RETRY_AFTER:
            return f"tried at {tried:%H:%M}; trying again after {tried + RETRY_AFTER:%H:%M}"
        return ""

    def window_days(self, name, now) -> int:
        """Days of postings to ask for: back to the last good read, plus a day of overlap (job IDs
        drop the repeats)."""
        last = _at(self.board(name).last_success)
        if not last:
            return MIN_DAYS
        return min(max((now - last).days + 1, MIN_DAYS), MAX_DAYS)

    def days_missed(self, name, now) -> int:
        """Days before the window that can no longer be caught up (the laptop was off too long)."""
        last = _at(self.board(name).last_success)
        return max(0, (now - last).days + 1 - MAX_DAYS) if last else 0

    def tried(self, name, now):
        b, day = self.board(name), now.date().isoformat()
        b.tries = (b.tries if b.tries_on == day else 0) + 1
        b.tries_on, b.last_try = day, now.isoformat()

    def succeeded(self, name, started):
        """`started`: when the read began, so the next window reaches back to it."""
        b = self.board(name)
        b.last_success, b.last_error, b.paused_until = started.isoformat(), "", ""

    def failed(self, name, now, error, pause=False):
        b = self.board(name)
        b.last_error = error
        if pause:
            b.paused_until = (now + SIGN_IN_PAUSE).isoformat()


def overdue(last_reads: dict, now, names) -> dict:
    """{board: its last good read, or None} for laptop boards not read for OVERDUE."""
    return {n: last_reads.get(n) for n in sorted(names)
            if not last_reads.get(n) or now - last_reads[n] > OVERDUE}


def connection_problem(host, timeout=10) -> str:
    """'' if `host` answers with a certificate Python trusts, otherwise what went wrong.
    A network that puts its own certificate in the way (some campus and office Wi-Fi) is skipped,
    never worked around: the browser would be sending the burner account's cookie through it."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return ""
    except ssl.SSLCertVerificationError as e:
        return f"{host}'s certificate isn't trusted on this network ({e.verify_message})"
    except OSError as e:
        return f"can't reach {host} ({type(e).__name__})"


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def battery_low() -> bool:
    """On battery and at MIN_BATTERY% or less. Unknown (no battery, not Windows) counts as fine."""
    if sys.platform != "win32":
        return False
    s = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return False
    return s.ACLineStatus == 0 and s.BatteryLifePercent <= MIN_BATTERY   # 255 = unknown


_ES_CONTINUOUS, _ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


@contextmanager
def keep_awake():
    """Ask Windows not to sleep while the boards are read. Closing the lid still sleeps the laptop."""
    if sys.platform != "win32":
        yield
        return
    kernel = ctypes.windll.kernel32
    kernel.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        kernel.SetThreadExecutionState(_ES_CONTINUOUS)

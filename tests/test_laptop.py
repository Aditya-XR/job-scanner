"""The laptop run: which boards are due, how far back each one reads, and the --if-due run."""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

import jobscan.laptop as laptop
import jobscan.run as run
import jobscan.sheet as sheet_mod
from jobscan.core import IST, Job
from jobscan.laptop import State

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=IST)


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


def state(tmp_path, **boards):
    s = State(path=tmp_path / "laptop.json")
    for name, fields in boards.items():
        for k, v in fields.items():
            setattr(s.board(name), k, v)
    return s


# ---------- when a board is due ----------

def test_a_board_never_read_is_due(tmp_path):
    assert state(tmp_path).why_not_due("Naukri", NOW) == ""


def test_a_board_read_today_waits_for_tomorrow(tmp_path):
    s = state(tmp_path, Naukri={"last_success": ago(hours=8)})
    assert s.why_not_due("Naukri", NOW).startswith("read at 30 Oct 01:00")
    assert s.why_not_due("Naukri", NOW + timedelta(days=1)) == ""


def test_last_evenings_read_still_counts_just_after_midnight(tmp_path):
    s = state(tmp_path, Naukri={"last_success": "2026-10-29T23:30:00+05:30"})
    assert s.why_not_due("Naukri", datetime(2026, 10, 30, 0, 30, tzinfo=IST))      # 1 hour later
    assert s.why_not_due("Naukri", datetime(2026, 10, 30, 6, 0, tzinfo=IST)) == ""  # 6.5 hours later


def test_a_failed_board_is_retried_an_hour_later_and_at_most_three_times_a_day(tmp_path):
    s = state(tmp_path)
    s.tried("Naukri", NOW - timedelta(minutes=20))
    s.failed("Naukri", NOW, "RuntimeError: Naukri served no search results")
    assert "trying again after 09:40" in s.why_not_due("Naukri", NOW)
    assert s.why_not_due("Naukri", NOW + timedelta(minutes=41)) == ""
    s.tried("Naukri", NOW + timedelta(hours=1))
    s.tried("Naukri", NOW + timedelta(hours=2))
    assert "tried 3 times today" in s.why_not_due("Naukri", NOW + timedelta(hours=5))
    assert s.why_not_due("Naukri", NOW + timedelta(days=1)) == ""          # a new day, new tries


def test_linkedin_gets_two_tries_a_day(tmp_path):
    s = state(tmp_path)
    s.tried("LinkedIn", NOW)
    s.tried("LinkedIn", NOW + timedelta(hours=1))
    assert "tried 2 times today" in s.why_not_due("LinkedIn", NOW + timedelta(hours=3))


def test_a_sign_in_wall_pauses_the_board_for_two_days(tmp_path):
    s = state(tmp_path)
    s.failed("LinkedIn", NOW, "SignInNeeded: LinkedIn wants the burner account to sign in again", pause=True)
    assert s.why_not_due("LinkedIn", NOW + timedelta(hours=47)).startswith("paused until 01 Nov 09:00")
    assert s.why_not_due("LinkedIn", NOW + timedelta(hours=49)) == ""
    s.succeeded("LinkedIn", NOW + timedelta(hours=49))
    assert s.board("LinkedIn").paused_until == "" and s.board("LinkedIn").last_error == ""


# ---------- how far back a board reads ----------

@pytest.mark.parametrize("since, days, missed", [
    (None, 2, 0),                     # never read: the normal 2 days
    (timedelta(hours=20), 2, 0),
    (timedelta(days=3, hours=19), 4, 0),   # Oct 26 13:39 -> Oct 30 09:00
    (timedelta(days=6, hours=1), 7, 0),
    (timedelta(days=10), 7, 4),       # off for too long: 4 older days can't be caught up
])
def test_window_reaches_back_to_the_last_good_read(tmp_path, since, days, missed):
    s = state(tmp_path, Foundit={"last_success": ago(**{"seconds": since.total_seconds()}) if since else ""})
    assert s.window_days("Foundit", NOW) == days
    assert s.days_missed("Foundit", NOW) == missed


def test_the_run_log_seeds_only_boards_the_state_file_doesnt_know(tmp_path):
    s = state(tmp_path, Foundit={"last_success": ago(hours=2)})
    s.seed({"Foundit": NOW - timedelta(days=3), "Naukri": NOW - timedelta(days=3)})
    assert s.board("Foundit").last_success == ago(hours=2)
    assert s.board("Naukri").last_success == ago(days=3)


def test_state_survives_a_save_and_a_broken_file_starts_over(tmp_path):
    s = state(tmp_path)
    s.tried("Naukri", NOW)
    s.succeeded("Naukri", NOW)
    s.save()
    again = State.load(tmp_path / "laptop.json")
    assert again.board("Naukri").last_success == NOW.isoformat() and again.board("Naukri").tries == 1
    (tmp_path / "laptop.json").write_text("{not json", encoding="utf-8")
    assert State.load(tmp_path / "laptop.json").boards == {}


def test_overdue_lists_boards_not_read_for_two_days():
    reads = {"Foundit": NOW - timedelta(hours=5), "LinkedIn": NOW - timedelta(days=3)}
    assert laptop.overdue(reads, NOW, {"Foundit", "LinkedIn", "Naukri"}) == {
        "LinkedIn": NOW - timedelta(days=3), "Naukri": None}


def test_connection_problems_are_explained(monkeypatch):
    def refuse(*a, **kw):
        raise ConnectionRefusedError()
    monkeypatch.setattr(laptop.socket, "create_connection", refuse)
    assert laptop.connection_problem("www.naukri.com") == "can't reach www.naukri.com (ConnectionRefusedError)"


# ---------- the Run log ----------

class FakeLog:
    def __init__(self, rows):
        self.rows = rows

    def get_values(self, rng):
        return self.rows


def test_last_good_reads_skip_errors_and_empty_reads():
    sh = sheet_mod.Sheet.__new__(sheet_mod.Sheet)
    sh.log = FakeLog([
        ["2026-10-26 13:39", "laptop", "LinkedIn", "172", "40", "10", "8", "", "no"],
        ["2026-10-26 13:39", "laptop", "Foundit", "806", "200", "50", "40", "", "no"],
        ["2026-10-29 13:07", "laptop", "Foundit", "1261", "273", "46", "46", "", "no"],
        ["2026-10-29 13:07", "laptop", "LinkedIn", "0", "0", "0", "0", "Error: Page.goto: net::ERR_CERT"],
        ["2026-10-29 13:13", "cloud", "Unstop", "60", "41", "4", "4", "", "yes"],
    ])
    assert sh.last_good_reads({"Foundit", "LinkedIn", "Naukri"}) == {
        "LinkedIn": datetime(2026, 10, 26, 13, 39, tzinfo=IST), "Foundit": datetime(2026, 10, 29, 13, 7, tzinfo=IST)}


# ---------- python -m jobscan --group laptop --if-due ----------

class SignInNeeded(RuntimeError):
    pass


class FakeSheet:
    url = "https://sheet"
    reads = {}

    def last_good_reads(self, names):
        return dict(self.reads)

    @contextmanager
    def lock(self, holder):
        yield


@pytest.fixture
def laptop_run(monkeypatch, tmp_path):
    """Run run_due() with fake boards and sheet. Returns what each board was asked and saved."""
    seen = {"cfg": {}, "saved": [], "emails": []}
    boards = {}

    def board(name, result):
        def reader(cfg):
            seen["cfg"][name] = dict(cfg)
            if isinstance(result, Exception):
                raise result
            return result, False
        boards[name] = reader

    monkeypatch.setattr(laptop, "STATE_FILE", tmp_path / "laptop.json")
    monkeypatch.setattr(laptop, "connection_problem", lambda host: "")
    monkeypatch.setattr(laptop, "battery_low", lambda: False)
    monkeypatch.setattr(sheet_mod, "Sheet", FakeSheet)
    monkeypatch.setattr(run, "load_tasks", lambda group, only=None: [(n, r, {"name": n}) for n, r in boards.items()])

    def update(sheet, group, jobs, stats):
        seen["saved"].append(sorted(stats))
        return run.Update([j for j in jobs], [], 0, set(), {})
    monkeypatch.setattr(run, "update_sheet", update)
    monkeypatch.setattr(run, "send_email", lambda sheet, group, updates: seen["emails"].append(
        [j.title for u in updates for j in u.new]))
    seen["board"] = board
    return seen


def job(title):
    return Job("Foundit", "Acme", title, "Bengaluru", "https://x", job_id=f"foundit:{title}")


def test_only_due_boards_are_read_each_saved_on_its_own_with_one_email(laptop_run, monkeypatch):
    now = datetime.now(IST)
    FakeSheet.reads = {"Foundit": now - timedelta(days=3, hours=2), "Naukri": now - timedelta(minutes=30)}
    laptop_run["board"]("Foundit", [job("SDE 1")])
    laptop_run["board"]("Naukri", [job("never read")])
    laptop_run["board"]("LinkedIn", [job("SDE intern")])
    run.run_due()

    assert set(laptop_run["cfg"]) == {"Foundit", "LinkedIn"}        # Naukri was read 30 minutes ago
    assert laptop_run["cfg"]["Foundit"]["days"] == 4                # back to its last read, 3 days ago
    assert laptop_run["cfg"]["LinkedIn"]["days"] == 2
    assert laptop_run["saved"] == [["Foundit"], ["LinkedIn"]]        # one sheet update per board
    assert laptop_run["emails"] == [["SDE 1", "SDE intern"]]
    s = State.load()
    assert s.board("Foundit").tries == 1 and s.why_not_due("Foundit", datetime.now(IST)).startswith("read at")


def test_a_bad_network_skips_a_board_without_using_a_try(laptop_run, monkeypatch):
    FakeSheet.reads = {}
    monkeypatch.setattr(laptop, "connection_problem",
                        lambda host: "www.linkedin.com's certificate isn't trusted" if "linkedin" in host else "")
    laptop_run["board"]("LinkedIn", [job("SDE intern")])
    laptop_run["board"]("Foundit", [job("SDE 1")])
    run.run_due()
    assert set(laptop_run["cfg"]) == {"Foundit"}
    assert State.load().board("LinkedIn").tries == 0


def test_no_google_connection_reads_nothing(laptop_run, monkeypatch):
    monkeypatch.setattr(laptop, "connection_problem", lambda host: "can't reach sheets.googleapis.com (gaierror)")
    laptop_run["board"]("Foundit", [job("SDE 1")])
    run.run_due()
    assert laptop_run["cfg"] == {} and laptop_run["emails"] == []


def test_a_failed_board_is_kept_for_later_and_a_sign_in_wall_pauses_it(laptop_run):
    FakeSheet.reads = {}
    laptop_run["board"]("LinkedIn", SignInNeeded("LinkedIn wants the burner account to sign in again"))
    laptop_run["board"]("Naukri", [])
    run.run_due()
    s, now = State.load(), datetime.now(IST)
    assert s.why_not_due("LinkedIn", now).startswith("paused until")
    assert s.board("Naukri").last_error == "no jobs" and "trying again after" in s.why_not_due("Naukri", now)
    assert not s.board("Naukri").last_success
    assert laptop_run["saved"] == [["LinkedIn"], ["Naukri"]]   # both in the Run log
    assert laptop_run["emails"] == []                          # but nothing read: no email


def test_a_board_that_couldnt_be_saved_is_not_marked_read(laptop_run, monkeypatch):
    FakeSheet.reads = {}
    laptop_run["board"]("Foundit", [job("SDE 1")])

    def locked(*a):
        raise RuntimeError("The sheet is still in use (cloud run on gh) after 20 minutes")
    monkeypatch.setattr(run, "update_sheet", locked)
    run.run_due()
    b = State.load().board("Foundit")
    assert not b.last_success and b.last_error.startswith("not saved: RuntimeError")
    assert laptop_run["emails"] == []


def test_dry_run_reads_due_boards_but_saves_nothing(laptop_run, monkeypatch):
    FakeSheet.reads = {}
    monkeypatch.setattr(run, "print_new", lambda jobs, stats: None)
    laptop_run["board"]("Foundit", [job("SDE 1")])
    run.run_due(dry_run=True)
    assert set(laptop_run["cfg"]) == {"Foundit"} and laptop_run["saved"] == []
    assert not laptop.STATE_FILE.exists()

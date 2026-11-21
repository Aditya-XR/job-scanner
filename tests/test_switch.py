"""`jobscan off`: the Python side of the laptop's off switch (laptop/scanner_switch.ps1 is the rest;
laptop/switch_test/run_tests.ps1 tests that on Windows)."""
import pytest

import jobscan.laptop as laptop
import jobscan.run as run
from test_laptop import FakeSheet, job, laptop_run  # noqa: F401  (laptop_run is a fixture)
from test_lock import FakeLockTab, claim, make_sheet


def test_on_unless_state_off_exists(switched_on):
    assert laptop.switched_off() == ""
    switched_on.write_text("off since 21 Nov 2026 17:50", encoding="utf-8")
    assert laptop.switched_off() == "off since 21 Nov 2026 17:50"


def test_an_empty_or_unreadable_off_file_still_means_off(switched_on):
    switched_on.write_bytes(b"\xef\xbb\xbf")                 # empty, with a BOM
    assert laptop.switched_off() == "switched off"
    switched_on.unlink()
    switched_on.mkdir()                                      # can't be read as a file
    assert laptop.switched_off().startswith("switched off")
    switched_on.rmdir()
    switched_on.write_bytes(b"\xff\xfe\x00garbage")          # not UTF-8
    assert laptop.switched_off().startswith("switched off")


@pytest.mark.parametrize("argv", [[], ["--group", "laptop", "--if-due"], ["--group", "laptop"], ["--dry-run"]])
def test_every_run_refuses_to_start_while_off(switched_on, monkeypatch, argv):
    def must_not_run(*a, **kw):
        raise AssertionError("read something while switched off")
    monkeypatch.setattr(run, "load_tasks", must_not_run)
    monkeypatch.setattr(run, "run_due", must_not_run)
    switched_on.write_text("off since 21 Nov 2026 17:50", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run.main(argv)
    assert "switched OFF" in str(e.value) and "jobscan on" in str(e.value)


def test_switching_off_mid_run_opens_no_more_boards(laptop_run, switched_on, monkeypatch):
    FakeSheet.reads = {}
    read = []

    def foundit(cfg):
        read.append("Foundit")
        switched_on.write_text("off since now", encoding="utf-8")   # `jobscan off` while Foundit reads
        return [job("SDE 1")], False

    def naukri(cfg):
        read.append("Naukri")
        return [job("never read")], False
    monkeypatch.setattr(run, "load_tasks", lambda group, only=None: [
        ("Foundit", foundit, {"name": "Foundit"}), ("Naukri", naukri, {"name": "Naukri"})])
    run.run_due()
    assert read == ["Foundit"]
    assert laptop_run["saved"] == [["Foundit"]]                    # what was read is still saved


def test_off_releases_only_this_laptops_lock(monkeypatch):
    tab = FakeLockTab(claim(3, "laptop run on HOME"))
    assert make_sheet(monkeypatch, tab).release_lock_of("HOME").startswith("laptop run on HOME |")
    assert tab.value == ""

    tab = FakeLockTab(claim(3, "cloud run on fv-az123"))            # GitHub's run: never touched
    assert make_sheet(monkeypatch, tab).release_lock_of("HOME") == "" and tab.writes == []

    tab = FakeLockTab(claim(3, "laptop run on OTHER-HOME"))         # another laptop's run
    assert make_sheet(monkeypatch, tab).release_lock_of("HOME") == "" and tab.writes == []

    tab = FakeLockTab("")
    assert make_sheet(monkeypatch, tab).release_lock_of("HOME") == "" and tab.writes == []

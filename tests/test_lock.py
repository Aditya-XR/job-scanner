"""The sheet's Lock tab: one writer at a time across GitHub's runs and the laptop's."""
from datetime import datetime, timedelta

import pytest

import jobscan.sheet as sheet_mod
from jobscan.core import IST


class Cell:
    def __init__(self, value):
        self.value = value


class FakeLockTab:
    """A1 of the Lock tab. `others` are claims written by another machine between our reads."""

    def __init__(self, value="", others=()):
        self.value, self.others, self.writes = value, list(others), []

    def acell(self, label):
        if self.others:
            self.value = self.others.pop(0)
        return Cell(self.value)

    def update_acell(self, label, value):
        self.writes.append(value)
        self.value = value


def make_sheet(monkeypatch, tab):
    monkeypatch.setattr(sheet_mod.time, "sleep", lambda s: None)
    sh = sheet_mod.Sheet.__new__(sheet_mod.Sheet)
    sh._worksheet = lambda name, hidden=False: tab
    return sh


def claim(minutes_ago, who="laptop run on HOME"):
    return f"{who} | {datetime.now(IST) - timedelta(minutes=minutes_ago):%Y-%m-%d %H:%M:%S}"


def test_free_sheet_is_claimed_and_released(monkeypatch):
    tab = FakeLockTab()
    with make_sheet(monkeypatch, tab).lock("cloud run on gh"):
        assert tab.value.startswith("cloud run on gh | ")
    assert tab.value == ""


def test_a_fresh_claim_is_waited_for_and_a_stale_one_taken_over(monkeypatch):
    busy = claim(2)
    tab = FakeLockTab(busy, others=[busy, busy, ""])         # the laptop run finishes after a while
    with make_sheet(monkeypatch, tab).lock("cloud run on gh"):
        assert tab.value.startswith("cloud run on gh")
    tab = FakeLockTab(claim(90))                               # a crashed run's claim expires
    with make_sheet(monkeypatch, tab).lock("cloud run on gh"):
        assert tab.value.startswith("cloud run on gh")


def test_gives_up_when_the_sheet_stays_busy(monkeypatch):
    tab = FakeLockTab(claim(1))
    sh = make_sheet(monkeypatch, tab)
    monkeypatch.setattr(sheet_mod.time, "monotonic", iter(range(0, 10**6, 600)).__next__)
    with pytest.raises(RuntimeError, match="still in use"):
        with sh.lock("cloud run on gh", wait_minutes=20):
            pass


def test_losing_a_simultaneous_claim_backs_off(monkeypatch):
    tab = FakeLockTab("", others=["", "", claim(0, "other"), claim(0, "other"), ""])
    with make_sheet(monkeypatch, tab).lock("cloud run on gh"):
        assert tab.value.startswith("cloud run on gh")
    assert len(tab.writes) == 3       # claimed, lost to "other", claimed again; then released


def test_another_runs_claim_is_never_released(monkeypatch):
    tab = FakeLockTab()
    with make_sheet(monkeypatch, tab).lock("cloud run on gh"):
        tab.value = claim(0, "other")         # our claim expired and someone took over
    assert tab.value.startswith("other")

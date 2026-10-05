"""Open / closed / reposted tracking."""
from datetime import date

from jobscan.state import OPEN, Row, Tracker

TODAY = date(2026, 10, 10)


def row(n, ids, company="Cisco", status=OPEN, title="Software Engineer", user_status=""):
    return Row(number=n, ids=ids if isinstance(ids, list) else [ids], company=company,
               status=status, title=title, user_status=user_status)


def test_status_of():
    t = Tracker([row(2, "a"), row(3, "b", status="Closed 2026-10-09"), row(4, "c", status="Closed 2026-10-07")],
                dropped_ids=["d"], today=TODAY)
    assert t.status_of("new-id") == "new"
    assert t.status_of("a") == "known"
    assert t.status_of("b") == "known"       # gone only 1 day: treated as a feed glitch
    assert t.status_of("c") == "reposted"    # gone 3 days and back
    assert t.status_of("d") == "dropped"


def test_open_rows_stay_open_and_missing_rows_close():
    t = Tracker([row(2, "a"), row(3, "b")], [], TODAY)
    closed = t.refresh(fetched_ids={"a"}, complete_feeds={"Cisco"}, reposted_ids=set())
    assert t.rows[0].new_status == OPEN
    assert t.rows[1].new_status == "Closed 2026-10-10"
    assert closed == [t.rows[1]]


def test_never_close_after_a_partial_or_failed_read():
    t = Tracker([row(2, "a")], [], TODAY)
    assert t.refresh(fetched_ids=set(), complete_feeds=set(), reposted_ids=set()) == []
    assert t.rows[0].new_status is None      # untouched


def test_closed_row_reopens_if_back_quickly():
    t = Tracker([row(2, "a", status="Closed 2026-10-09")], [], TODAY)
    t.refresh({"a"}, {"Cisco"}, set())
    assert t.rows[0].new_status == OPEN


def test_closed_rows_are_not_closed_again():
    t = Tracker([row(2, "a", status="Closed 2026-10-01")], [], TODAY)
    assert t.refresh(set(), {"Cisco"}, set()) == []
    assert t.rows[0].new_status is None      # keeps its original closing date


def test_merged_row_stays_open_while_any_copy_is_live():
    t = Tracker([row(2, ["a", "b"])], [], TODAY)
    t.refresh({"b"}, {"Cisco"}, set())
    assert t.rows[0].new_status == OPEN


def test_reposted_same_id_supersedes_old_row():
    old = row(2, "c", status="Closed 2026-10-07")
    t = Tracker([old], [], TODAY)
    assert t.status_of("c") == "reposted"
    t.refresh({"c"}, {"Cisco"}, reposted_ids={"c"})
    assert old.new_status == "Closed (reposted 2026-10-10)"
    # once superseded, the old row is ignored by later runs
    later = Tracker([row(2, "c", status=OPEN), row(3, "c", status=old.new_status)], [], TODAY)
    assert later.by_id["c"].number == 2
    later.refresh({"c"}, {"Cisco"}, set())
    assert later.rows[1].new_status is None


def test_edge_case_take_down_and_repost_under_new_id():
    """edgeCases.txt: a job is posted, taken down hours later, and posted again with the same
    company and title. The repost has a new posting ID, so it must be a NEW row, and the
    original (no longer valid) row must be marked closed."""
    original = row(2, "workday:cisco:/job/Bangalore/Software-Engineer_R1")
    t = Tracker([original], [], TODAY)
    repost_id = "workday:cisco:/job/Bangalore/Software-Engineer_R1-1"
    assert t.status_of(repost_id) == "new"               # not skipped despite same title
    closed = t.refresh({repost_id}, {"Cisco"}, set())
    assert original.new_status == "Closed 2026-10-10" and closed == [original]


def test_rows_from_other_companies_untouched():
    t = Tracker([row(2, "a", company="Adobe")], [], TODAY)
    t.refresh(set(), {"Cisco"}, set())
    assert t.rows[0].new_status is None


# ---- job boards (2026-10-05) ----

def board_row(n, ids, source="Unstop", company="Acme", status=OPEN, title="Software Engineer"):
    return Row(number=n, ids=ids if isinstance(ids, list) else [ids], company=company, status=status,
               title=title, source=source, feed=source)


def test_board_rows_close_only_when_their_board_was_read_in_full():
    t = Tracker([board_row(2, "unstop:1"), board_row(3, "foundit:9", source="Foundit")], [], TODAY)
    closed = t.refresh(set(), complete_feeds={"Unstop"}, reposted_ids=set())
    assert closed == [t.rows[0]] and t.rows[1].new_status is None


def test_a_board_row_is_not_closed_by_its_companys_own_feed():
    """Unstop lists a Swiggy job; Swiggy's own feed being read in full says nothing about it."""
    t = Tracker([board_row(2, "unstop:1", company="Swiggy")], [], TODAY)
    assert t.refresh(set(), {"Swiggy"}, set()) == []


def test_a_stale_board_listing_does_not_keep_a_taken_down_job_open():
    r = Row(2, ["workday:cisco:/job/1", "foundit:77"], "Cisco", OPEN, title="Software Engineer",
            source="Workday", also_seen_on="Foundit")
    t = Tracker([r], [], TODAY)
    closed = t.refresh({"foundit:77"}, {"Cisco"}, set())
    assert closed == [r] and r.new_status == "Closed 2026-10-10"


def test_other_site_match():
    from jobscan.core import Job
    feed_row = Row(2, ["greenhouse:swiggy:1"], "Swiggy", OPEN, title="SDE 1", source="Greenhouse")
    t = Tracker([feed_row], [], TODAY)
    on_board = Job("Unstop", "Swiggy Pvt. Ltd.", "SDE 1", "Bengaluru", "https://u/1", job_id="unstop:5")
    assert t.other_site_match(on_board) is feed_row
    feed_row.attach_from_other_site(on_board)
    assert feed_row.also_seen_on == "Unstop" and "unstop:5" in feed_row.all_ids
    # a second Unstop posting with the same title is a different opening
    assert t.other_site_match(Job("Unstop", "Swiggy", "SDE 1", "Pune", "https://u/2", job_id="unstop:6")) is None
    # ...and a closed row is never matched
    t2 = Tracker([Row(2, ["greenhouse:swiggy:1"], "Swiggy", "Closed 2026-10-01", title="SDE 1",
                      source="Greenhouse")], [], TODAY)
    assert t2.other_site_match(on_board) is None

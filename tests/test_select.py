"""Choosing new rows: identity by posting ID, merging identical copies, reposts."""
from datetime import date

from jobscan.core import Job
from jobscan.run import select
from jobscan.state import Row, Tracker

FRESHER_JD = "We hire freshers. 0-1 years of experience in Java or Python."
SENIOR_JD = "You need 5+ years of professional experience building distributed systems."


def job(jid, title="Software Engineer", company="Cisco", source="Workday", loc="Bangalore, India",
        desc=FRESHER_JD, group=""):
    return Job(source, company, title, loc, f"https://example.com/{jid}", job_id=jid, group_id=group,
               posted=date(2026, 10, 3), description=desc)


def run(jobs, stats, no_ai, rows=(), dropped=()):
    return select(jobs, stats, Tracker(list(rows), list(dropped), date(2026, 10, 4)), gemini=no_ai)


def test_same_title_different_postings_are_all_considered(fixed_today, stats, no_ai):
    """Regression: company+title identity hid 111 postings. One Cisco 'Software Engineer'
    needing 5 years must not hide the other one that takes freshers."""
    new, dropped = run([job("cisco:1", desc=SENIOR_JD), job("cisco:2")], stats, no_ai)
    assert [j.job_id for j in new] == ["cisco:2"]
    assert [j.job_id for j in dropped] == ["cisco:1"]
    # next day: the dropped one stays dropped, a brand-new same-title posting is still considered
    new2, _ = run([job("cisco:1", desc=SENIOR_JD), job("cisco:2"), job("cisco:3")], stats, no_ai,
                  rows=[Row(2, ["cisco:2"], "Cisco", "Open")], dropped=["cisco:1"])
    assert [j.job_id for j in new2] == ["cisco:3"]


def test_identical_copies_in_one_run_become_one_row(fixed_today, stats, no_ai):
    a = job("gh:1", source="Greenhouse", loc="Remote - India", group="gh:job:9")
    b = job("gh:2", source="Greenhouse", loc="Bengaluru, India", group="gh:job:9")
    c = job("wd:1", title="DevOps Engineer", loc="Pune, India")
    d = job("wd:2", title="DevOps Engineer", loc="Chennai, India")            # same description too
    new, _ = run([a, b, c, d], stats, no_ai)
    assert len(new) == 2
    gh = next(j for j in new if j.source == "Greenhouse")
    assert gh.all_ids == ["gh:1", "gh:2"] and gh.location == "Remote - India; Bengaluru, India"
    wd = next(j for j in new if j.source == "Workday")
    assert wd.all_ids == ["wd:1", "wd:2"]


def test_same_title_with_different_descriptions_stays_separate(fixed_today, stats, no_ai):
    new, _ = run([job("wd:1"), job("wd:2", desc=FRESHER_JD + " Team: networking.")], stats, no_ai)
    assert len(new) == 2


def test_empty_descriptions_are_never_merged(fixed_today, stats, no_ai):
    new, _ = run([job("wd:1", desc=""), job("wd:2", desc="")], stats, no_ai)
    assert len(new) == 2


def test_reposted_posting_is_added_again_and_labelled(fixed_today, stats, no_ai):
    rows = [Row(2, ["wd:1"], "Cisco", "Closed 2026-10-01")]
    new, _ = run([job("wd:1")], stats, no_ai, rows=rows)
    assert len(new) == 1 and new[0].reposted and new[0].why_kept.startswith("Reposted.")


def test_known_posting_is_not_added_twice(fixed_today, stats, no_ai):
    new, _ = run([job("wd:1")], stats, no_ai, rows=[Row(2, ["wd:1"], "Cisco", "Open")])
    assert new == []


def test_same_opening_on_two_sites_is_one_row(fixed_today, stats, no_ai):
    feed = job("gh:1", source="Greenhouse", company="Swiggy")
    board = job("li:1", source="LinkedIn", company="Swiggy", desc=FRESHER_JD + " (via LinkedIn)")
    new, _ = run([board, feed], stats, no_ai)
    assert len(new) == 1
    assert new[0].source == "Greenhouse" and new[0].also_seen_on == ["LinkedIn"]
    assert set(new[0].all_ids) == {"gh:1", "li:1"}


def test_filters_still_apply(fixed_today, stats, no_ai):
    jobs = [job("1", title="Senior Software Engineer"), job("2", loc="Austin, TX", source="Greenhouse"),
            job("3", title="Sales Engineer")]
    new, dropped = run(jobs, stats, no_ai)
    assert new == [] and dropped == []


# ---- identical postings across days (found in real data: ServiceNow, Broadcom, Amazon) ----

from jobscan.core import fingerprint  # noqa: E402


def open_row(jid, desc=FRESHER_JD, status="Open", title="Software Engineer", loc="Bangalore, India"):
    return Row(2, [jid], "Cisco", status, title=title, user_status="Applied",
               fingerprint=fingerprint(desc), location=loc)


def test_identical_posting_of_an_open_row_is_attached_not_added(fixed_today, stats, no_ai):
    row = open_row("wd:R027060-7")
    copy = job("wd:R027061", desc="  " + FRESHER_JD.replace(" ", "\n ", 1), loc="Pune, India")  # whitespace differs
    tracker = Tracker([row], [], date(2026, 10, 4))
    new, dropped = select([copy], stats, tracker, gemini=no_ai)
    assert new == [] and dropped == []
    assert row.all_ids == ["wd:R027060-7", "wd:R027061"]
    assert row.location == "Bangalore, India; Pune, India"
    # and it stays tracked: tomorrow the attached ID alone keeps the row open
    assert tracker.refresh({"wd:R027061"}, {"Cisco"}, set()) == [] and row.new_status == "Open"


def test_identical_posting_after_original_closed_is_a_repost_row(fixed_today, stats, no_ai):
    """edgeCases.txt with a new posting ID and the very same description."""
    row = open_row("wd:R1", status="Closed 2026-10-03")
    new, _ = select([job("wd:R1-1")], stats, Tracker([row], [], date(2026, 10, 4)), gemini=no_ai)
    assert len(new) == 1 and new[0].reposted and new[0].why_kept.startswith("Reposted.")
    assert row.added == []


def test_same_title_different_description_is_a_new_row(fixed_today, stats, no_ai):
    row = open_row("wd:1")
    new, _ = select([job("wd:2", desc=FRESHER_JD + " Team: storage.")], stats,
                    Tracker([row], [], date(2026, 10, 4)), gemini=no_ai)
    assert [j.job_id for j in new] == ["wd:2"] and row.added == []


def test_fingerprint_ignores_case_and_spacing_but_not_numbers():
    assert fingerprint("0-1 Years of\n  Java") == fingerprint("0-1 years of java")
    assert fingerprint("0-1 years of Java") != fingerprint("5+ years of Java")
    assert fingerprint("") == fingerprint("   ") == ""

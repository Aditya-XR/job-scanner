"""Job board readers, from recorded real responses (tests/record_fixtures.py)."""
import pytest
import requests

import jobscan.sources.boards as boards
from conftest import load
from jobscan.filters import experience_check, is_india


def serve(monkeypatch, get):
    monkeypatch.setattr(boards, "get_json", get)


def check(jobs, prefix):
    assert jobs, "reader returned no jobs"
    assert all(j.job_id.startswith(prefix) and not j.id_fallback for j in jobs)
    assert len({j.job_id for j in jobs}) == len(jobs)
    assert all(j.url.startswith("https://") for j in jobs)
    assert all(is_india(j.location) for j in jobs)    # what select() checks


def blocked(url, **kw):
    raise requests.ConnectionError("blocked")


def test_unstop(monkeypatch):
    data = load("board_unstop.json")
    serve(monkeypatch, lambda url, **kw: data)
    jobs, complete = boards.unstop({})
    assert complete
    check(jobs, "unstop:")
    assert [j.job_id for j in jobs] == [f"unstop:{x['id']}" for x in data["data"]["data"]]
    assert all("open to freshers" in j.description for j in jobs)   # from the eligibility field


def test_unstop_skips_jobs_outside_india_and_marks_wfh_remote(monkeypatch):
    data = load("board_unstop.json")
    rows = data["data"]["data"]
    for place in rows[0]["locations"]:
        place["country"] = "United Arab Emirates"
    rows[1]["locations"], rows[1]["jobDetail"]["type"] = [], "wfh"
    serve(monkeypatch, lambda url, **kw: data)
    jobs, _ = boards.unstop({})
    assert [j.job_id for j in jobs] == [f"unstop:{rows[1]['id']}", f"unstop:{rows[2]['id']}"]
    assert jobs[0].location == boards.REMOTE


def test_hirist(monkeypatch):
    data = load("board_hirist.json")
    serve(monkeypatch, lambda url, **kw: data)
    jobs, complete = boards.hirist({})
    assert complete
    check(jobs, "hirist:")
    exotel = next(j for j in jobs if j.job_id == "hirist:1675108")
    assert exotel.company == "Exotel" and not exotel.title.startswith("Exotel - ")
    assert exotel.url.endswith("-1675108")


def test_a_failed_detail_request_keeps_the_boards_experience_range(monkeypatch):
    data = load("board_hirist.json")
    serve(monkeypatch, lambda url, **kw: blocked(url) if "/detail" in url else data)
    jobs, _ = boards.hirist({})
    jobs[0].ensure_description()
    assert jobs[0].description.startswith("Experience: 1-3 years")
    assert experience_check(jobs[0].title, jobs[0].description)[0] == "drop"   # 1+ years


def test_foundit_skips_ad_slots_and_is_never_complete(monkeypatch):
    data = load("board_foundit.json")
    serve(monkeypatch, lambda url, **kw: data)
    jobs, complete = boards.foundit({})
    assert not complete                     # a window of recent days: must never close rows
    check(jobs, "foundit:")
    assert len(jobs) == 3
    # 0-0 means Foundit doesn't know the experience; a real range is passed on
    monkeypatch.setattr(boards, "get_json", blocked)
    for j in jobs:
        j.ensure_description()
    assert [j.description for j in jobs] == ["", "", "Experience: 0-1 years\n"]


def test_internshala(monkeypatch):
    data = load("board_internshala.json")
    calls = []
    serve(monkeypatch, lambda url, **kw: calls.append(url) or data)
    jobs, _ = boards.internshala({})
    check(jobs, "internshala:")
    assert all(j.posted for j in jobs)      # from the Unix time ending each job URL
    assert len(calls) > 1                   # no internship on page 1, so it read on


def test_internshala_stops_at_the_first_internship(monkeypatch):
    data = load("board_internshala.json")
    data["internship_list_html"] = data["internship_list_html"].replace(
        'employment_type="job"', 'employment_type="internship"', 1)
    calls = []
    serve(monkeypatch, lambda url, **kw: calls.append(url) or data)
    jobs, complete = boards.internshala({})
    assert complete and len(calls) == 1 and len(jobs) == 2


def test_cutshort_keeps_0_1_year_minimums_and_stops_at_the_last_page(monkeypatch, fixed_today):
    data = load("board_cutshort.json")
    rows = data["data"]["pageData"]["jobs"]
    rows[0]["expRange"] = {"min": 0, "max": 2}        # fresher
    rows[1]["expRange"] = {"min": 0.5, "max": 1.5}    # fresher
    rows[2]["expRange"] = {"min": 3, "max": 5}        # not
    for x in rows:
        x["jobFactSummary"]["postedDate"] = "2026-10-03T10:00:00Z"

    def get(url, **kw):
        if "page=1&" in url:
            return data
        err = requests.HTTPError("400 jobs_not_found")   # what Cutshort says past the last page
        err.response = type("Response", (), {"status_code": 400})()
        raise err
    serve(monkeypatch, get)
    jobs, complete = boards.cutshort({})
    assert not complete
    check(jobs, "cutshort:")
    assert [j.job_id for j in jobs] == [f"cutshort:{rows[0]['_id']}", f"cutshort:{rows[1]['_id']}"]
    assert [experience_check("Software Engineer", j.description)[0] for j in jobs] == ["keep", "keep"]


@pytest.mark.parametrize("names,expected", [
    (["Bangalore"], "Bangalore"),
    (["Bhopal"], "Bhopal, India"),                   # an Indian city missing from INDIA_PLACES
    (["Remote"], boards.REMOTE),
    (["Anywhere in India/Multiple Locations"], "Anywhere in India/Multiple Locations"),
    (["Singapore"], None),
    (["Overseas/International"], None),
    (["Riyadh", "Pune"], "Riyadh, Pune"),
    ([], "India"),
])
def test_place(names, expected):
    assert boards._place(names) == expected

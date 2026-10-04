"""Every reader must give every posting the hiring system's own ID, from recorded real responses."""
import copy

import pytest

import jobscan.sources.feeds as feeds
from conftest import load
from jobscan.run import check_ids, new_stats


def serve(monkeypatch, get=None, post=None):
    monkeypatch.setattr(feeds, "get_json", get or (lambda url, **kw: pytest.fail(f"unexpected GET {url}")))
    monkeypatch.setattr(feeds, "post_json", post or (lambda url, body, **kw: pytest.fail(f"unexpected POST {url}")))


def assert_ids(jobs, prefix, native_ids):
    assert jobs, "reader returned no jobs"
    assert all(not j.id_fallback for j in jobs)
    assert [j.job_id for j in jobs] == [f"{prefix}{n}" for n in native_ids]
    assert len({j.job_id for j in jobs}) == len(jobs)
    assert all(j.url.startswith("https://") for j in jobs)


def test_greenhouse(monkeypatch):
    data = load("greenhouse_rubrik.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.greenhouse({"slug": "rubrik", "name": "Rubrik"})
    assert complete
    assert_ids(jobs, "greenhouse:rubrik:", [j["id"] for j in data["jobs"]])
    assert all(j.group_id.startswith("greenhouse:rubrik:job:") for j in jobs)


def test_lever(monkeypatch):
    data = load("lever_meesho.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.lever({"slug": "meesho", "name": "Meesho"})
    assert complete
    assert_ids(jobs, "lever:meesho:", [j["id"] for j in data])


def test_ashby(monkeypatch):
    data = load("ashby_sarvam.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.ashby({"slug": "sarvam", "name": "Sarvam AI"})
    assert complete
    assert_ids(jobs, "ashby:sarvam:", [j["id"] for j in data["jobs"]])


def test_smartrecruiters(monkeypatch):
    data = load("smartrecruiters_swiggy.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.smartrecruiters({"slug": "swiggy", "name": "Swiggy"})
    assert complete
    assert_ids(jobs, "smartrecruiters:swiggy:", [j["id"] for j in data["content"]])


def test_workday(monkeypatch):
    data = load("workday_nvidia.json")
    calls = []

    def post(url, body, **kw):
        calls.append(copy.deepcopy(body))
        return data
    serve(monkeypatch, post=post)
    jobs, complete = feeds.workday({"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite",
                                    "name": "NVIDIA"})
    assert complete
    assert_ids(jobs, "workday:nvidia:", [p["externalPath"] for p in data["jobPostings"]])
    # the India facet was found and applied after the first call
    assert calls[1]["appliedFacets"] == {"locationHierarchy1": ["2fcb99c455831013ea52b82135ba3266"]}


def test_workday_old_postings_are_still_returned(monkeypatch, fixed_today):
    """30+ day old postings must be returned (so they aren't wrongly marked closed) but dated
    old enough for the age filter to skip them as new rows."""
    data = load("workday_nvidia.json")
    data["jobPostings"][0]["postedOn"] = "Posted 30+ Days Ago"
    serve(monkeypatch, post=lambda url, body, **kw: data)
    jobs, _ = feeds.workday({"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite", "name": "NVIDIA"})
    assert len(jobs) == len(data["jobPostings"])
    assert (fixed_today - jobs[0].posted).days == 31


def test_amazon(monkeypatch):
    data = load("amazon.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.amazon({"name": "Amazon"})
    assert complete
    assert_ids(jobs, "amazon:", [j.get("id_icims") or j["id"] for j in data["jobs"]])


def test_microsoft(monkeypatch):
    data = load("microsoft.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.microsoft({"name": "Microsoft"})
    assert complete
    assert_ids(jobs, "microsoft:", [p["id"] for p in data["data"]["positions"]])


def test_partial_read_is_not_complete(monkeypatch):
    data = load("smartrecruiters_swiggy.json")
    data["totalFound"] = 5000   # more than the reader will page through
    serve(monkeypatch, get=lambda url, **kw: data)
    monkeypatch.setattr(feeds, "SR_MAX", 200)
    _, complete = feeds.smartrecruiters({"slug": "swiggy", "name": "Swiggy"})
    assert not complete


def test_missing_id_falls_back_and_is_counted(monkeypatch):
    data = load("greenhouse_rubrik.json")
    del data["jobs"][0]["id"]                      # feed omits the ID: use the link
    del data["jobs"][1]["id"]
    data["jobs"][1]["absolute_url"] = ""           # ...and the link: hash the visible fields
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, _ = feeds.greenhouse({"slug": "rubrik", "name": "Rubrik"})
    assert jobs[0].job_id.startswith("url:https://") and jobs[0].id_fallback
    assert jobs[1].job_id.startswith("hash:") and jobs[1].id_fallback
    st = new_stats()
    assert len(check_ids(jobs, st)) == len(jobs)   # still kept, never dropped
    assert st["fallbacks"] == 2


def test_check_ids_dedupes_repeats_and_keeps_conflicts():
    from jobscan.core import Job
    a = Job("Workday", "X", "Software Engineer", "Pune", "https://x/1", job_id="workday:x:/1")
    a_again = Job("Workday", "X", "Software Engineer", "Pune", "https://x/1", job_id="workday:x:/1")
    clash = Job("Workday", "X", "Data Engineer", "Pune", "https://x/2", job_id="workday:x:/1")
    st = new_stats()
    out = check_ids([a, a_again, clash], st)
    assert [j.title for j in out] == ["Software Engineer", "Data Engineer"]   # repeat dropped, clash kept
    assert out[0].job_id != out[1].job_id
    assert st["conflicts"] == 1

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


def test_eightfold_keeps_microsoft_ids(monkeypatch):
    """Microsoft now goes through the general Eightfold reader; its IDs must not change, or every
    Microsoft row already in the sheet would come back as new."""
    data = load("microsoft.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.eightfold({"host": "apply.careers.microsoft.com", "domain": "microsoft.com",
                                      "name": "Microsoft"})
    assert complete
    assert_ids(jobs, "microsoft:", [p["id"] for p in data["data"]["positions"]])
    assert all(j.source == "Microsoft Careers" for j in jobs)


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


# ---- career sites added 2026-10-05 (most moved off Workday) ----

def test_eightfold_qualcomm(monkeypatch):
    data = load("eightfold_qualcomm.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.eightfold({"host": "careers.qualcomm.com", "domain": "qualcomm.com", "name": "Qualcomm"})
    assert complete
    assert_ids(jobs, "qualcomm:", [p["id"] for p in data["data"]["positions"]])
    assert jobs[0].url == f"https://careers.qualcomm.com/careers/job/{data['data']['positions'][0]['id']}"


def test_eightfold_v2_netapp(monkeypatch):
    data = load("eightfold_v2_netapp.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.eightfold_v2({"host": "netapp.eightfold.ai", "domain": "netapp.com", "name": "NetApp"})
    assert complete
    assert_ids(jobs, "netapp:", [p["id"] for p in data["positions"]])


def test_jibe_amd(monkeypatch):
    data = load("jibe_amd.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.jibe({"host": "careers.amd.com", "name": "AMD"})
    assert complete
    assert_ids(jobs, "amd:", [j["data"]["req_id"] for j in data["jobs"]])
    assert all(j.description for j in jobs)        # in the list: no extra request per job


def test_talentbrew_synopsys(monkeypatch):
    data = load("talentbrew_synopsys.json")
    serve(monkeypatch, get=lambda url, **kw: data)
    jobs, complete = feeds.talentbrew({"host": "careers.synopsys.com", "country_facet": "1269750",
                                       "name": "Synopsys"})
    assert complete
    assert len(jobs) == 3
    assert_ids(jobs, "synopsys:", [j.url.rsplit("/", 1)[1] for j in jobs])   # the number ending the job URL
    assert all(j.posted for j in jobs)


def test_oracle_dell(monkeypatch):
    data = load("oracle_dell.json")
    urls = []
    serve(monkeypatch, get=lambda url, **kw: urls.append(url) or data)
    jobs, complete = feeds.oracle({"api": "enterpriseplatform.dell.com", "site": "CX_1001",
                                   "country_facet": "300000000471053",
                                   "jobs_page": "jobs.dell.com/en/sites/careers", "name": "Dell"})
    assert complete
    assert_ids(jobs, "dell:", [r["Id"] for r in data["items"][0]["requisitionList"]])
    assert "finder=findReqs;siteNumber=CX_1001," in urls[0]   # Oracle rejects an encoded finder


def test_goldman(monkeypatch):
    data = load("goldman.json")
    empty = {"data": {"roleSearch": {"totalCount": 0, "items": []}}}
    serve(monkeypatch, post=lambda url, body, **kw:
          data if "PROFESSIONAL" in body["variables"]["searchQueryInput"]["experiences"] else empty)
    jobs, complete = feeds.goldman({"name": "Goldman Sachs"})
    assert complete
    assert_ids(jobs, "goldman:", [r["externalSource"]["sourceId"] for r in data["data"]["roleSearch"]["items"]])
    # titles name the team and rank, so the job function is added for the software-title check
    assert all("software engineering" in j.title.lower() for j in jobs)

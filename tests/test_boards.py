"""Job board readers, from recorded real responses (tests/record_fixtures.py)."""
import re
import time

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


def no_wait(monkeypatch, throttle):
    monkeypatch.setattr(throttle, "wait", lambda: None)


def test_instahyre_keeps_software_titles_with_the_date_from_their_page(monkeypatch):
    data = load("board_instahyre.json")
    serve(monkeypatch, lambda url, **kw: data["listing"])
    monkeypatch.setattr(boards, "get_text", lambda url, **kw: data["pages"][url])
    no_wait(monkeypatch, boards._INSTAHYRE)
    jobs, complete = boards.instahyre({})
    assert complete                                   # the whole 0-years listing was read
    check(jobs, "instahyre:")
    assert [j.title for j in jobs] == ["DevOps Engineer", "Software Engineer"]   # not the sales job
    assert all(j.posted for j in jobs)                # the listing has no dates; the job page does
    assert all(experience_check(j.title, j.description)[0] == "keep" for j in jobs)


def test_instahyre_keeps_a_job_whose_page_failed(monkeypatch):
    data = load("board_instahyre.json")
    serve(monkeypatch, lambda url, **kw: data["listing"])
    monkeypatch.setattr(boards, "get_text", blocked)
    no_wait(monkeypatch, boards._INSTAHYRE)
    jobs, _ = boards.instahyre({})
    assert len(jobs) == 2 and all(j.posted is None for j in jobs)


def test_wellfound_skips_2_year_listings_and_stops_when_a_page_adds_nothing(monkeypatch):
    data = load("board_wellfound.json")
    pages = []
    monkeypatch.setattr(boards, "get_text", lambda url, **kw: pages.append(url) or data["page"])
    no_wait(monkeypatch, boards._WELLFOUND)
    jobs, complete = boards.wellfound({})
    assert not complete
    check(jobs, "wellfound:")
    assert [(j.company, j.job_id) for j in jobs] == [("Insight Consultants", "wellfound:4824775"),
                                                     ("ClearFeed", "wellfound:4377418")]
    assert all(j.description and j.posted for j in jobs)
    # first role: page 1, then page 2 adds nothing; every other role: page 1 adds nothing
    assert len(pages) == len(boards.WELLFOUND_ROLES) + 1


def test_wellfound_reports_a_blocked_page():
    with pytest.raises(RuntimeError, match="__NEXT_DATA__"):
        boards._apollo_state("<html><title>Just a moment...</title></html>")


def test_naukri_search_response():
    from jobscan.sources.browser import naukri_job     # parsing only: no browser needed
    jobs = [naukri_job(x) for x in load("board_naukri.json")["jobDetails"]]
    check(jobs, "naukri:")
    cisco = next(j for j in jobs if j.company == "Cisco")
    assert cisco.description.startswith("Experience: 0-2 years") and cisco.location == "Bengaluru"
    assert all(j.posted for j in jobs)


def naukri_site(population):
    """A fake Naukri search: shows the first 20 matching jobs and counts them all (noOfJobs)."""
    template, calls = load("board_naukri.json")["jobDetails"][0], []

    def search(url):
        calls.append(url)
        slug, city, days, mode = re.match(r"https://www\.naukri\.com/(.+?)-jobs(?:-in-([a-z-]+))?"
                                          r"\?experience=0&jobAge=(\d+)(?:&wfhType=(\d))?$", url).groups()
        hits = [x for x in population if x["slug"] == slug and city in (None, x["city"]) and mode in (None, x["mode"])]
        return {"noOfJobs": len(hits), "jobDetails": [
            dict(template, jobId=x["id"], placeholders=[{"type": "location", "label": x["place"]}]) for x in hits[:20]]}
    return search, calls


def naukri_jobs(n, city, mode, place, first):
    return [{"id": str(first + i), "slug": "software-engineer", "city": city, "mode": mode, "place": place}
            for i in range(n)]


# 2026-10-30's shape: one search with more than a page, Bengaluru more than a page by itself.
NAUKRI_POPULATION = (naukri_jobs(30, "bengaluru", "0", "Bengaluru", 0) + naukri_jobs(5, "bengaluru", "3", "Hybrid - Bengaluru", 100)
                     + naukri_jobs(10, "pune", "0", "Pune", 200) + naukri_jobs(5, None, "0", "Kolkata", 300))


def test_naukri_splits_a_search_with_more_than_a_page_by_city_then_work_mode():
    from jobscan.sources.browser import NAUKRI_SEARCHES, read_naukri
    search, calls = naukri_site(NAUKRI_POPULATION)
    jobs, unread = read_naukri(search, {"days": 3})
    check(jobs, "naukri:")
    assert len(jobs) == 35            # Bengaluru office's first page, Bengaluru hybrid, Pune
    assert unread == 15               # 10 more Bengaluru office jobs, and Kolkata (no city search)
    assert all("jobAge=3" in u for u in calls)
    split = [u for u in calls if "-in-" in u or "wfhType" in u]
    assert len(calls) == len(NAUKRI_SEARCHES) + len(split) and len(split) == 6 + 1 + 3   # cities, remote, Bengaluru's modes
    assert "https://www.naukri.com/software-engineer-jobs-in-bengaluru?experience=0&jobAge=3&wfhType=3" in calls


def test_naukri_splits_at_most_a_set_number_of_extra_searches(monkeypatch):
    import jobscan.sources.browser as browser
    monkeypatch.setattr(browser, "NAUKRI_MAX_SPLITS", 3)
    search, calls = naukri_site(NAUKRI_POPULATION)
    jobs, unread = browser.read_naukri(search, {})
    assert len(calls) == len(browser.NAUKRI_SEARCHES) + 3
    assert (len(jobs), unread) == (25, 25)


def test_naukri_keeps_what_it_read_when_a_later_search_fails():
    from jobscan.sources.browser import read_naukri
    search, calls = naukri_site(NAUKRI_POPULATION)

    def flaky(url):
        if len(calls) >= 2:
            raise RuntimeError("Naukri served no search results ('Access Denied'): TimeoutError")
        return search(url)
    jobs, _ = read_naukri(flaky, {})
    assert len(jobs) == 20            # the first search, before the failure
    with pytest.raises(RuntimeError, match="Access Denied"):
        read_naukri(flaky, {})        # failing from the first search: nothing read is an error


def test_naukri_stops_at_its_time_limit():
    from jobscan.sources.browser import read_naukri
    search, calls = naukri_site(NAUKRI_POPULATION)
    assert read_naukri(search, {"deadline": time.monotonic() - 1}) == ([], 0) and not calls


def test_foundit_and_linkedin_read_back_as_many_days_as_asked(monkeypatch):
    data, urls = load("board_foundit.json"), []
    serve(monkeypatch, lambda url, **kw: urls.append(url) or data)
    boards.foundit({})
    boards.foundit({"days": 5})
    assert "jobFreshness=2" in urls[0] and "jobFreshness=5" in urls[-1]

    li, params = load("board_linkedin.json"), []
    monkeypatch.setattr(boards, "_linkedin_get", lambda url, **kw: params.append(kw.get("params")) or li["search"])
    boards.linkedin({})
    boards.linkedin({"days": 4})
    assert params[0]["f_TPR"] == "r172800" and params[-1]["f_TPR"] == "r345600"


def linkedin_site(monkeypatch, limit_after=None):
    """LinkedIn from the fixture; with limit_after=n, the n+1th request is rate-limited."""
    data, calls = load("board_linkedin.json"), []

    def get(url, **kw):
        calls.append(url)
        if limit_after is not None and len(calls) > limit_after:
            raise boards._RateLimited()
        return data["search"] if url == boards.LINKEDIN_SEARCH else data["posting"]
    monkeypatch.setattr(boards, "_linkedin_get", get)
    return calls


def test_linkedin(monkeypatch):
    calls = linkedin_site(monkeypatch)
    jobs, complete = boards.linkedin({})
    assert not complete                               # a 2-day window: never closes rows
    check(jobs, "linkedin:")
    assert len(jobs) == 3 and len(calls) == len(boards.LINKEDIN_QUERIES)   # under 10 cards: one page each
    assert jobs[0].url == f"https://www.linkedin.com/jobs/view/{jobs[0].job_id.split(':')[1]}/"
    assert all(j.posted for j in jobs)
    jobs[0].ensure_description()
    assert jobs[0].description.startswith("LinkedIn seniority level: ")


def test_linkedin_rate_limit_keeps_what_was_read(monkeypatch):
    linkedin_site(monkeypatch, limit_after=1)
    jobs, _ = boards.linkedin({})                     # the 2nd search is refused: page 1 stays
    assert len(jobs) == 3
    jobs[0].ensure_description()                      # refused too: kept, marked as unchecked
    assert jobs[0].description == ""
    assert experience_check(jobs[0].title, "")[2] == "description not available"
    linkedin_site(monkeypatch, limit_after=0)
    with pytest.raises(RuntimeError, match="rate-limiting"):
        boards.linkedin({})


def test_linkedin_signed_in_search_and_prefetched_descriptions():
    from jobscan.sources.browser import linkedin_job, read_linkedin   # parsing only: no browser needed
    data = load("board_linkedin_signed_in.json")
    cards, descriptions = {}, {}
    assert read_linkedin([data["search"], data["prefetch"], {}], cards, descriptions) == 3
    jobs = [linkedin_job(card, descriptions.get(native, "")) for native, card in cards.items()]
    check(jobs, "linkedin:")
    assert [j.job_id for j in jobs] == ["linkedin:4479074097", "linkedin:4477930290", "linkedin:4478109780"]
    assert jobs[0].company == "Hire Daily" and jobs[0].url == "https://www.linkedin.com/jobs/view/4479074097/"
    assert all(j.posted for j in jobs)
    assert jobs[0].description and jobs[1].description and not jobs[2].description   # not prefetched: kept unchecked


def test_linkedin_signed_in_leaves_the_next_pages_cards_for_that_page():
    """Page 1 also fetches page 2's cards, without their descriptions: they wait for page 2."""
    from jobscan.sources.browser import read_linkedin
    data = load("board_linkedin_signed_in.json")
    ahead = {"data": {"paging": {"total": 50, "start": 25}}, "included": data["search"]["included"]}
    cards, descriptions = {}, {}
    assert read_linkedin([ahead, data["prefetch"]], cards, descriptions, start=0) == 50
    assert not cards and len(descriptions) == 2
    read_linkedin([ahead], cards, descriptions, start=25)
    assert len(cards) == 3


def test_linkedin_uses_the_burner_only_when_its_cookie_is_set(monkeypatch):
    import jobscan.sources.browser as browser
    monkeypatch.setattr(browser, "linkedin_signed_in", lambda c: (["signed in"], False))
    monkeypatch.setattr(boards, "linkedin", lambda c: (["guest"], False))
    monkeypatch.setenv("LINKEDIN_LI_AT", "x")
    assert boards.BOARDS["LinkedIn"]({}) == (["signed in"], False)
    monkeypatch.setenv("LINKEDIN_LI_AT", "")
    assert boards.BOARDS["LinkedIn"]({}) == (["guest"], False)


def test_run_groups():
    """Boards that refuse GitHub's servers or need a browser run from the laptop, not in the cloud run."""
    from jobscan.run import load_tasks
    names = lambda group: {name for name, _, _ in load_tasks(group)}
    assert {"Unstop", "Internshala", "Hirist", "Cutshort", "Walmart", "Goldman Sachs"} <= names("cloud")
    assert not names("cloud") & boards.HOME_IP_ONLY
    assert names("laptop") == {"Foundit", "LinkedIn", "Naukri"}
    assert {"Instahyre", "Wellfound"} <= names("cloud")
    assert names("boards") == set(boards.BOARDS)


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

"""Re-record the sample API responses used by the tests (trimmed to a few India postings).

    python tests/record_fixtures.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobscan.core import get_json, post_json  # noqa: E402
from jobscan.filters import is_india  # noqa: E402

OUT = Path(__file__).parent / "fixtures"
CUT = 600  # characters of each description kept


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("saved", name)


def trim(d, *keys):
    for k in keys:
        if isinstance(d.get(k), str):
            d[k] = d[k][:CUT]
    return d


gh = get_json("https://boards-api.greenhouse.io/v1/boards/rubrik/jobs?content=true")["jobs"]
save("greenhouse_rubrik.json", {"jobs": [trim(j, "content") for j in gh
                                         if is_india((j.get("location") or {}).get("name", ""))][:3]})

lv = [j for j in get_json("https://api.lever.co/v0/postings/meesho?mode=json")
      if is_india((j.get("categories") or {}).get("location", ""))][:3]
for j in lv:
    trim(j, "descriptionPlain", "description", "additionalPlain", "additional", "openingPlain", "opening",
         "descriptionBody", "descriptionBodyPlain")
    j["lists"] = [trim(l, "content") for l in j.get("lists", [])[:2]]
save("lever_meesho.json", lv)

ab = [trim(j, "descriptionPlain", "descriptionHtml") for j in
      get_json("https://api.ashbyhq.com/posting-api/job-board/sarvam")["jobs"]][:3]
save("ashby_sarvam.json", {"jobs": ab})

sr = get_json("https://api.smartrecruiters.com/v1/companies/swiggy/postings?limit=3&offset=0&country=in")
sr["content"] = sr["content"][:3]
sr["totalFound"] = len(sr["content"])
save("smartrecruiters_swiggy.json", sr)

api = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
first = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
page = post_json(f"{api}/jobs", {"appliedFacets": {"locationHierarchy1": ["2fcb99c455831013ea52b82135ba3266"]},
                                 "limit": 3, "offset": 0, "searchText": "software"})
save("workday_nvidia.json", {"facets": first["facets"], "total": 3, "jobPostings": page["jobPostings"][:3]})

amz = get_json("https://www.amazon.jobs/en/search.json?base_query=software&normalized_country_code%5B%5D=IND"
               "&result_limit=3&offset=0&sort=recent")
amz["jobs"] = [trim(j, "description", "basic_qualifications", "preferred_qualifications", "description_short")
               for j in amz["jobs"][:3]]
amz["hits"] = len(amz["jobs"])
save("amazon.json", {"hits": amz["hits"], "jobs": amz["jobs"]})

ms = get_json("https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query=software"
              "&location=India&start=0")["data"]
save("microsoft.json", {"data": {"count": 3, "positions": ms["positions"][:3]}})

# ---------- added 2026-10-05: career sites that moved off Workday, and job boards ----------

from bs4 import BeautifulSoup  # noqa: E402

from jobscan.sources import boards  # noqa: E402

qc = get_json("https://careers.qualcomm.com/api/pcsx/search?domain=qualcomm.com&query=software&location=India&start=0")
save("eightfold_qualcomm.json", {"data": {"count": 3, "positions": qc["data"]["positions"][:3]}})

na = get_json("https://netapp.eightfold.ai/api/apply/v2/jobs?domain=netapp.com&location=India&start=0&num=10")
save("eightfold_v2_netapp.json", {"count": 3, "positions": na["positions"][:3]})

amd = get_json("https://careers.amd.com/api/jobs?location=India&page=1&limit=3")
for j in amd["jobs"]:
    d = j["data"]
    j["data"] = {k: (v[:CUT] if isinstance(v, str) else v) for k, v in d.items() if k != "meta_data"}
save("jibe_amd.json", {"totalCount": 3, "jobs": amd["jobs"]})

syn = get_json("https://careers.synopsys.com/search-jobs/results?CurrentPage=1&RecordsPerPage=3&SearchType=5"
               "&SearchResultsModuleName=Search+Results&FacetFilters%5B0%5D.ID=1269750"
               "&FacetFilters%5B0%5D.FacetType=2&FacetFilters%5B0%5D.IsApplied=true",
               headers={"X-Requested-With": "XMLHttpRequest"})
soup = BeautifulSoup(syn["results"], "html.parser")
box = soup.select_one("#search-results")
for s in box.select("script"):
    s.decompose()                                  # a large debug blob the reader doesn't use
box["data-total-pages"] = "1"
save("talentbrew_synopsys.json", {"results": str(box)})

finder = ("findReqs;siteNumber=CX_1001,facetsList=LOCATIONS,limit=3,offset=0,"
          "selectedLocationsFacet=300000000471053,sortBy=POSTING_DATES_DESC")
dell = get_json("https://enterpriseplatform.dell.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                f"?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}")
item = dell["items"][0]
save("oracle_dell.json", {"items": [{"TotalJobsCount": 3, "requisitionList": item["requisitionList"][:3]}]})

gs = post_json("https://api-higher.gs.com/gateway/api/v1/graphql", {
    "operationName": "GetRoles", "query": __import__("jobscan.sources.feeds", fromlist=["GS_QUERY"]).GS_QUERY,
    "variables": {"searchQueryInput": {"page": {"pageSize": 3, "pageNumber": 0},
                                       "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
                                       "filters": [{"filterCategoryType": "LOCATION",
                                                    "filters": [{"filter": "India", "subFilters": []}]}],
                                       "experiences": ["EARLY_CAREER", "PROFESSIONAL"], "searchTerm": ""}}},
    headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"})
for r in gs["data"]["roleSearch"]["items"]:
    r["descriptionHtml"] = (r.get("descriptionHtml") or "")[:CUT]
gs["data"]["roleSearch"]["totalCount"] = 3
save("goldman.json", gs)

un = get_json(f"{boards.UNSTOP_API}?opportunity=jobs&oppstatus=open&usertype=fresher&roles={boards.UNSTOP_ROLES}"
              "&per_page=3&page=1", headers={"Referer": "https://unstop.com/job"})
keep = ("id", "title", "seo_url", "public_url", "approved_date", "locations", "organisation", "jobDetail",
        "regnRequirements", "details")
rows = [{k: (x.get(k)[:CUT] if isinstance(x.get(k), str) else x.get(k)) for k in keep} for x in un["data"]["data"][:3]]
for x in rows:
    x["organisation"] = {"name": x["organisation"]["name"]}
    x["regnRequirements"] = {k: x["regnRequirements"].get(k) for k in ("start_regn_dt", "eligibility")}
save("board_unstop.json", {"data": {"total": 3, "last_page": 1, "data": rows}})

hi = get_json(f"{boards.HIRIST_API}/category/?categoryId={boards.HIRIST_CATEGORIES}&minexp=0&maxexp=1&page=0&size=3",
              headers=boards.HIRIST_HEADERS)
keep = ("id", "title", "min", "max", "createdTimeMs", "locations", "companyData", "confidential")
hi_rows = [{k: x.get(k) for k in keep} for x in hi["data"][:3]]
for x in hi_rows:
    cd = x["companyData"] or {}
    x["companyData"] = {"companyName": cd.get("companyName"),
                        "ambitionBoxInfo": {"companyName": (cd.get("ambitionBoxInfo") or {}).get("companyName")}}
save("board_hirist.json", {"data": hi_rows, "totalJobs": 3, "hasMore": False})

fo = get_json(f"{boards.FOUNDIT}/middleware/jobsearch?sort=2&limit=4&start=0&query=software"
              "&experienceRanges=0~1&locations=India&jobFreshness=2", headers=boards.FOUNDIT_HEADERS)
keep = ("jobId", "title", "companyName", "locations", "freshness", "createdAt", "minimumExperience",
        "maximumExperience", "seoJdUrl", "jdUrl")
fo_rows = [{k: x.get(k) for k in keep} for x in fo["jobSearchResponse"]["data"] if x.get("jobId")][:3]
save("board_foundit.json", {"jobSearchResponse": {"meta": {"paging": {"total": 3}},
                                                  "data": fo_rows[:1] + [{"index": "0", "type": "adsense"}] + fo_rows[1:]}})

page = get_json(f"{boards.INTERNSHALA}/fresher-jobs_ajax/{boards.INTERNSHALA_PATH}",
                headers={"X-Requested-With": "XMLHttpRequest"})
cards = BeautifulSoup(page["internship_list_html"], "html.parser").select("div.individual_internship[internshipid]")
jobs_ = [c for c in cards if c.get("employment_type") == "job"][:3]
interns = [c for c in cards if c.get("employment_type") == "internship"][:1]
save("board_internshala.json", {"internship_list_html": "".join(map(str, jobs_ + interns)),
                                "is_last_page": False, "next_page_number": 2})

cs = get_json(f"{boards.CUTSHORT}?page=1")
cs_jobs = cs["data"]["pageData"]["jobs"]
pick = [x for x in cs_jobs if (x.get("expRange") or {}).get("min", 9) <= 1][:2] + \
       [x for x in cs_jobs if (x.get("expRange") or {}).get("min", 0) > 1][:1]
keep = ("_id", "headline", "publicUrl", "locations", "remoteType", "expRange", "companyDetails", "companyId",
        "jobFactSummary", "sanitizedComment")
save("board_cutshort.json", {"data": {"pageData": {"jobs": [
    {k: (x.get(k)[:CUT] if isinstance(x.get(k), str) else x.get(k)) for k in keep} for x in pick]}}})

# ---------- added 2026-10-13: laptop boards (Phase 3) ----------

import re  # noqa: E402

from jobscan.core import get_text, json_ld_posting  # noqa: E402
from jobscan.filters import is_software  # noqa: E402

ih = get_json(f"{boards.INSTAHYRE}/api/v1/job_search?company_size=0&isLandingPage=true&job_type=0&years=0"
              "&limit=20&offset=0")
soft = [x for x in ih["objects"] if is_software(x["title"])][:2]
other = [x for x in ih["objects"] if not is_software(x["title"])][:1]
pages = {}
for x in soft:
    ld = json_ld_posting(get_text(x["public_url"], headers={"Accept": "text/html"}))
    ld["description"] = (ld.get("description") or "")[:CUT]
    pages[x["public_url"]] = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
keep = ("id", "title", "candidate_title", "locations", "public_url", "employer")
save("board_instahyre.json", {"listing": {"meta": {"total_count": 3},
                                          "objects": [{k: x.get(k) for k in keep} for x in soft + other]},
                              "pages": pages})

wf = get_text(f"{boards.WELLFOUND}/role/l/software-engineer/india", headers={"Accept": "text/html"})
state = boards._apollo_state(wf)
listings = {k: v for k, v in state.items() if isinstance(v, dict) and v.get("__typename") == "JobListingSearchResult"}
fresher = [k for k, v in listings.items() if (v.get("yearsExperienceMin") or 0) <= 1
           and any(re.search("bangalore|bengaluru|pune|noida|gurgaon|hyderabad|mumbai|chennai|delhi", n, re.I)
                   for n in v.get("locationNames") or [])][:2]
senior = [k for k, v in listings.items() if (v.get("yearsExperienceMin") or 0) > 1][:1]
picked = {k: dict(listings[k], description=(listings[k].get("description") or "")[:CUT]) for k in fresher + senior}
startups = {k: dict(v, highlightedJobListings=[r for r in v.get("highlightedJobListings") or [] if r["__ref"] in picked])
            for k, v in state.items() if isinstance(v, dict) and v.get("__typename") == "StartupResult"
            and any(r["__ref"] in picked for r in v.get("highlightedJobListings") or [])}
next_data = {"props": {"pageProps": {"apolloState": {"data": {**startups, **picked}}}}}
save("board_wellfound.json", {"page": f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'})

# LinkedIn rate-limits hard: record it on its own, a few requests, spaced out.
li_search = get_text(boards.LINKEDIN_SEARCH, params={"keywords": "software engineer", **boards.linkedin_filters(),
                                                     "start": 0}, headers={"Accept": "text/html"})
cards = BeautifulSoup(li_search, "html.parser").select("li")[:3]
native = cards[0].select_one("[data-entity-urn]")["data-entity-urn"].rsplit(":", 1)[-1]
__import__("time").sleep(5)
li_post = BeautifulSoup(get_text(f"{boards.LINKEDIN}/jobs-guest/jobs/api/jobPosting/{native}",
                                 headers={"Accept": "text/html"}), "html.parser")
body = li_post.select_one(".show-more-less-html__markup")
body.string = body.get_text("\n", strip=True)[:CUT]
criteria = li_post.select_one(".description__job-criteria-list")
save("board_linkedin.json", {"search": "".join(map(str, cards)), "posting": str(criteria) + str(body)})

# Naukri needs a real browser window: the search page's own API response, read through Playwright.
from playwright.sync_api import sync_playwright  # noqa: E402

from jobscan.sources.browser import naukri_url  # noqa: E402

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(str(Path(__file__).resolve().parent.parent / "browser-profile" / "naukri"),
                                                   channel="chrome", headless=False)
    tab = browser.new_page()
    with tab.expect_response(lambda r: "/jobapi/v3/search" in r.url, timeout=45000) as hit:
        tab.goto(naukri_url("software-engineer", 2), wait_until="domcontentloaded")
    nk = hit.value.json()
    browser.close()
keep = ("jobId", "title", "companyName", "jdURL", "createdDate", "minimumExperience", "maximumExperience",
        "placeholders", "jobDescription")
save("board_naukri.json", {"noOfJobs": 3, "jobDetails": [
    {k: (x.get(k)[:CUT] if isinstance(x.get(k), str) else x.get(k)) for k in keep} for x in nk["jobDetails"][:3]]})

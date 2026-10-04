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

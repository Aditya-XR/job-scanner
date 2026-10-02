"""Readers for company career feeds (hiring systems with open JSON APIs).

Each reader takes one entry from companies.yaml and returns a list of Jobs located in
India (or remote-India). Descriptions are loaded lazily where the list endpoint lacks them.
"""
import re
from datetime import timedelta
from urllib.parse import quote

from ..core import Job, get_json, html_to_text, parse_date, post_json, today
from ..filters import is_india

# ---------- Greenhouse ----------

def greenhouse(c):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    jobs = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [])
        if not (is_india(loc) or is_india(offices)):
            continue
        jobs.append(Job("Greenhouse", c["name"], j["title"], loc or offices, j["absolute_url"],
                        posted=parse_date(j.get("first_published") or j.get("updated_at")),
                        description=html_to_text(j.get("content", ""))))
    return jobs


# ---------- Lever ----------

def lever(c):
    data = get_json(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    jobs = []
    for j in data:
        cat = j.get("categories") or {}
        locs = [cat.get("location") or ""] + list(cat.get("allLocations") or [])
        if not any(is_india(x) for x in locs) and j.get("country") != "IN":
            continue
        lists = "\n".join(f"{l.get('text', '')}\n{html_to_text(l.get('content', ''))}"
                          for l in j.get("lists") or [])
        desc = "\n".join([j.get("descriptionPlain", ""), lists, j.get("additionalPlain", "")])
        jobs.append(Job("Lever", c["name"], j["text"], cat.get("location", ""), j["hostedUrl"],
                        posted=parse_date(j.get("createdAt")), description=desc))
    return jobs


# ---------- Ashby ----------

def ashby(c):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}")
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [
            (x.get("location") if isinstance(x, dict) else str(x)) or ""
            for x in j.get("secondaryLocations") or []]
        if not any(is_india(x) for x in locs):
            continue
        jobs.append(Job("Ashby", c["name"], j["title"], j.get("location", ""), j["jobUrl"],
                        posted=parse_date(j.get("publishedAt")),
                        description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))))
    return jobs


# ---------- SmartRecruiters ----------

def smartrecruiters(c):
    jobs, offset = [], 0
    while offset < 1000:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings"
                        f"?limit=100&offset={offset}&country=in")
        page = data.get("content", [])
        for j in page:
            company_id = (j.get("company") or {}).get("identifier", c["slug"])
            jobs.append(Job("SmartRecruiters", c["name"], j["name"],
                            (j.get("location") or {}).get("fullLocation", "India"),
                            f"https://jobs.smartrecruiters.com/{company_id}/{j['id']}",
                            posted=parse_date(j.get("releasedDate")),
                            load_description=_sr_description(j["ref"])))
        offset += 100
        if offset >= data.get("totalFound", 0) or not page:
            break
    return jobs


def _sr_description(ref):
    def load():
        sections = (get_json(ref).get("jobAd") or {}).get("sections") or {}
        return "\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                         for k in ("jobDescription", "qualifications", "additionalInformation"))
    return load


# ---------- Workday ----------

def workday(c):
    base = f"https://{c['tenant']}.{c['wd']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    first = post_json(f"{api}/jobs", body)
    facets = _workday_india_facets(first.get("facets") or [])
    body["appliedFacets"] = facets

    jobs, total = [], None
    while body["offset"] < 1500:
        page = post_json(f"{api}/jobs", body)
        if total is None:
            total = page.get("total", 0)  # Workday only reports the total on the first page
        for p in page.get("jobPostings") or []:
            if not p.get("title") or not p.get("externalPath"):
                continue
            posted = _workday_posted(p.get("postedOn", ""))
            if posted is None:            # "Posted 30+ Days Ago": too old
                continue
            loc = p.get("locationsText", "")
            if not facets and not is_india(loc):
                continue
            jobs.append(Job("Workday", c["name"], p["title"], loc if is_india(loc) else f"India ({loc})",
                            f"{base}/{c['site']}{p['externalPath']}", posted=posted,
                            load_description=_workday_description(api, p["externalPath"])))
        body["offset"] += 20
        if body["offset"] >= total or not page.get("jobPostings"):
            break
    return jobs


def _workday_india_facets(facets):
    """Find the facet that limits results to India. Tenants name it differently
    (locationCountry, locationHierarchy1, Country_and_Jurisdiction...)."""
    country, cities = None, ("locations", [])

    def walk(items):
        nonlocal country
        for f in items:
            values = f.get("values") or []
            if values and "facetParameter" in values[0]:
                walk(values)
                continue
            param = f.get("facetParameter")
            for v in values:
                d = (v.get("descriptor") or "").strip()
                if d == "India" and country is None:
                    country = (param, v["id"])
                elif param == "locations" and "india" in d.lower():
                    cities[1].append(v["id"])

    walk(facets)
    if country:
        return {country[0]: [country[1]]}
    return {cities[0]: cities[1]} if cities[1] else {}


def _workday_posted(text):
    t = text.lower()
    if "30+" in t:
        return None
    if "today" in t:
        return today()
    if "yesterday" in t:
        return today() - timedelta(days=1)
    m = re.search(r"(\d+)", t)
    return today() - timedelta(days=int(m.group(1))) if m else today()


def _workday_description(api, path):
    def load():
        info = get_json(f"{api}{path}").get("jobPostingInfo") or {}
        return html_to_text(info.get("jobDescription", ""))
    return load


# ---------- Amazon ----------

def amazon(c):
    jobs, offset = [], 0
    while offset < 600:
        data = get_json("https://www.amazon.jobs/en/search.json?base_query=software"
                        f"&normalized_country_code%5B%5D=IND&result_limit=100&offset={offset}&sort=recent")
        page = data.get("jobs", [])
        for j in page:
            desc = "\n".join(html_to_text(j.get(k, "")) for k in
                             ("description", "basic_qualifications", "preferred_qualifications"))
            jobs.append(Job("Amazon Jobs", c["name"], j["title"], j.get("normalized_location", "India"),
                            "https://www.amazon.jobs" + j["job_path"],
                            posted=parse_date(j.get("posted_date")), description=desc))
        offset += 100
        if not page or offset >= data.get("hits", 0):
            break
    return jobs


# ---------- Microsoft ----------

MS_API = "https://apply.careers.microsoft.com/api/pcsx"


def microsoft(c):
    jobs, start, seen = [], 0, set()
    while start < 500:
        data = get_json(f"{MS_API}/search?domain=microsoft.com&query={quote('software')}"
                        f"&location=India&start={start}").get("data") or {}
        page = data.get("positions") or []
        for p in page:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            loc = "; ".join(p.get("locations") or [])
            if not is_india(loc):
                continue
            jobs.append(Job("Microsoft Careers", c["name"], p["name"], loc,
                            f"https://apply.careers.microsoft.com/careers/job/{p['id']}",
                            posted=parse_date(p.get("postedTs")),
                            load_description=_ms_description(p["id"])))
        start += len(page)
        if not page or start >= data.get("count", 0):
            break
    return jobs


def _ms_description(pid):
    def load():
        d = get_json(f"{MS_API}/position_details?position_id={pid}&domain=microsoft.com&hl=en")
        return html_to_text((d.get("data") or {}).get("jobDescription", ""))
    return load


READERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
           "smartrecruiters": smartrecruiters, "workday": workday}
CUSTOM = {"amazon": amazon, "microsoft": microsoft}

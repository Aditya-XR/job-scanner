"""Readers for company career feeds (hiring systems with open JSON APIs).

Each reader takes one entry from companies.yaml and returns (jobs, complete):
  jobs      Jobs located in India (or remote-India), each carrying the hiring system's own
            posting ID (see core.posting_id). Descriptions are loaded lazily where the list
            endpoint lacks them.
  complete  True only if the whole feed was read (no page limit hit). Jobs are marked
            "Closed" only when they are missing from a complete read.
"""
import re
from datetime import timedelta
from urllib.parse import quote

from ..core import Job, get_json, html_to_text, parse_date, post_json, posting_id, today
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
        url = j.get("absolute_url", "")
        jid, fb = posting_id("greenhouse", c["slug"], j.get("id"), url, c["name"], j.get("title"), loc)
        internal = j.get("internal_job_id")
        jobs.append(Job("Greenhouse", c["name"], j.get("title", ""), loc or offices, url, job_id=jid, id_fallback=fb,
                        group_id=f"greenhouse:{c['slug']}:job:{internal}" if internal else "",
                        posted=parse_date(j.get("first_published") or j.get("updated_at")),
                        description=html_to_text(j.get("content", ""))))
    return jobs, True


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
        url = j.get("hostedUrl", "")
        jid, fb = posting_id("lever", c["slug"], j.get("id"), url, c["name"], j.get("text"), cat.get("location"))
        jobs.append(Job("Lever", c["name"], j.get("text", ""), cat.get("location", ""), url, job_id=jid,
                        id_fallback=fb, posted=parse_date(j.get("createdAt")), description=desc))
    return jobs, True


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
        url = j.get("jobUrl", "")
        jid, fb = posting_id("ashby", c["slug"], j.get("id"), url, c["name"], j.get("title"), j.get("location"))
        jobs.append(Job("Ashby", c["name"], j.get("title", ""), j.get("location", ""), url, job_id=jid,
                        id_fallback=fb, posted=parse_date(j.get("publishedAt")),
                        description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))))
    return jobs, True


# ---------- SmartRecruiters ----------

SR_MAX = 2000


def smartrecruiters(c):
    jobs, offset, total = [], 0, 0
    while offset < SR_MAX:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings"
                        f"?limit=100&offset={offset}&country=in")
        page = data.get("content", [])
        total = data.get("totalFound", 0)
        for j in page:
            company_id = (j.get("company") or {}).get("identifier", c["slug"])
            url = f"https://jobs.smartrecruiters.com/{company_id}/{j['id']}" if j.get("id") else ""
            loc = (j.get("location") or {}).get("fullLocation", "India")
            jid, fb = posting_id("smartrecruiters", c["slug"], j.get("id"), url, c["name"], j.get("name"), loc)
            jobs.append(Job("SmartRecruiters", c["name"], j.get("name", ""), loc, url, job_id=jid, id_fallback=fb,
                            posted=parse_date(j.get("releasedDate")),
                            load_description=_sr_description(j["ref"]) if j.get("ref") else None))
        offset += 100
        if offset >= total or not page:
            break
    return jobs, offset >= total


def _sr_description(ref):
    def load():
        sections = (get_json(ref).get("jobAd") or {}).get("sections") or {}
        return "\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                         for k in ("jobDescription", "qualifications", "additionalInformation"))
    return load


# ---------- Workday ----------

WD_MAX = 3000


def workday(c):
    base = f"https://{c['tenant']}.{c['wd']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    first = post_json(f"{api}/jobs", body)
    facets = _workday_india_facets(first.get("facets") or [])
    body["appliedFacets"] = facets

    jobs, total = [], None
    while body["offset"] < WD_MAX:
        page = post_json(f"{api}/jobs", body)
        if total is None:
            total = page.get("total", 0)  # Workday only reports the total on the first page
        for p in page.get("jobPostings") or []:
            if not p.get("title"):
                continue
            loc = p.get("locationsText", "")
            if not facets and not is_india(loc):
                continue
            path = p.get("externalPath") or ""
            url = f"{base}/{c['site']}{path}" if path else ""
            # The posting path is the ID: Workday gives a reposted requisition a new path
            # ("..._25877329-1"), so a repost is correctly seen as a new posting.
            jid, fb = posting_id("workday", c["tenant"], path, url, c["name"], p["title"], loc)
            jobs.append(Job("Workday", c["name"], p["title"], loc if is_india(loc) else f"India ({loc})", url,
                            job_id=jid, id_fallback=fb, posted=_workday_posted(p.get("postedOn", "")),
                            load_description=_workday_description(api, path) if path else None))
        body["offset"] += 20
        if body["offset"] >= total or not page.get("jobPostings"):
            break
    return jobs, body["offset"] >= (total or 0)


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
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'.
    30+ days is returned as 31 days ago: still tracked (so it isn't wrongly marked closed),
    but filtered out as too old for new rows."""
    t = text.lower()
    if "30+" in t:
        return today() - timedelta(days=31)
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

AMAZON_MAX = 3000


def amazon(c):
    jobs, offset, hits = [], 0, 0
    while offset < AMAZON_MAX:
        data = get_json("https://www.amazon.jobs/en/search.json?base_query=software"
                        f"&normalized_country_code%5B%5D=IND&result_limit=100&offset={offset}&sort=recent")
        page = data.get("jobs", [])
        hits = data.get("hits", 0)
        for j in page:
            desc = "\n".join(html_to_text(j.get(k, "")) for k in
                             ("description", "basic_qualifications", "preferred_qualifications"))
            url = "https://www.amazon.jobs" + j["job_path"] if j.get("job_path") else ""
            jid, fb = posting_id("amazon", "", j.get("id_icims") or j.get("id"), url,
                                 c["name"], j.get("title"), j.get("normalized_location"))
            jobs.append(Job("Amazon Jobs", c["name"], j.get("title", ""), j.get("normalized_location", "India"), url,
                            job_id=jid, id_fallback=fb, posted=parse_date(j.get("posted_date")), description=desc))
        offset += 100
        if not page or offset >= hits:
            break
    return jobs, offset >= hits


# ---------- Microsoft ----------

MS_API = "https://apply.careers.microsoft.com/api/pcsx"
MS_MAX = 2000


def microsoft(c):
    jobs, start, seen, count = [], 0, set(), 0
    while start < MS_MAX:
        data = get_json(f"{MS_API}/search?domain=microsoft.com&query={quote('software')}"
                        f"&location=India&start={start}").get("data") or {}
        page = data.get("positions") or []
        count = data.get("count", 0)
        for p in page:
            pid = p.get("id")
            if pid in seen:
                continue
            seen.add(pid)
            loc = "; ".join(p.get("locations") or [])
            if not is_india(loc):
                continue
            url = f"https://apply.careers.microsoft.com/careers/job/{pid}" if pid else ""
            jid, fb = posting_id("microsoft", "", pid, url, c["name"], p.get("name"), loc)
            jobs.append(Job("Microsoft Careers", c["name"], p.get("name", ""), loc, url, job_id=jid,
                            id_fallback=fb, posted=parse_date(p.get("postedTs")),
                            load_description=_ms_description(pid) if pid else None))
        start += len(page)
        if not page or start >= count:
            break
    return jobs, start >= count


def _ms_description(pid):
    def load():
        d = get_json(f"{MS_API}/position_details?position_id={pid}&domain=microsoft.com&hl=en")
        return html_to_text((d.get("data") or {}).get("jobDescription", ""))
    return load


READERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
           "smartrecruiters": smartrecruiters, "workday": workday}
CUSTOM = {"amazon": amazon, "microsoft": microsoft}

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

from bs4 import BeautifulSoup

from ..core import (Job, get_json, get_text, html_to_text, json_ld_description, parse_date, post_json,
                    posting_id, today)
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


# ---------- Eightfold (Microsoft, Qualcomm) ----------

EF_MAX = 2000


def _careers_id(c):
    """ID system for a company's own career site: "microsoft.com" -> "microsoft"."""
    return c["domain"].split(".")[0] if c.get("domain") else c["name"].lower().replace(" ", "")


def eightfold(c):
    """Eightfold's "PCSX" search API, which pages 10 postings at a time."""
    api, system = f"https://{c['host']}/api/pcsx", _careers_id(c)
    source = c.get("source") or f"{c['name']} Careers"
    jobs, start, seen, count = [], 0, set(), 0
    while start < EF_MAX:
        data = get_json(f"{api}/search?domain={c['domain']}&query={quote(c.get('query', 'software'))}"
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
            url = f"https://{c['host']}/careers/job/{pid}" if pid else ""
            jid, fb = posting_id(system, "", pid, url, c["name"], p.get("name"), loc)
            jobs.append(Job(source, c["name"], p.get("name", ""), loc, url, job_id=jid,
                            id_fallback=fb, posted=parse_date(p.get("postedTs")),
                            load_description=_eightfold_description(api, c["domain"], pid) if pid else None))
        start += len(page)
        if not page or start >= count:
            break
    return jobs, start >= count


def _eightfold_description(api, domain, pid):
    def load():
        d = get_json(f"{api}/position_details?position_id={pid}&domain={domain}&hl=en")
        return html_to_text((d.get("data") or {}).get("jobDescription", ""))
    return load


EF2_PAGE = 10   # the older API ignores larger page sizes


def eightfold_v2(c):
    """Eightfold's older "apply/v2" API (NetApp)."""
    api, system = f"https://{c['host']}/api/apply/v2/jobs", _careers_id(c)
    jobs, start, count = [], 0, 0
    while start < EF_MAX:
        data = get_json(f"{api}?domain={c['domain']}&location=India&start={start}&num={EF2_PAGE}")
        page = data.get("positions") or []
        count = data.get("count", 0)
        for p in page:
            loc = "; ".join(p.get("locations") or [p.get("location") or ""])
            if not is_india(loc):
                continue
            pid = p.get("id")
            url = p.get("canonicalPositionUrl") or (f"https://{c['host']}/careers/job/{pid}" if pid else "")
            jid, fb = posting_id(system, "", pid, url, c["name"], p.get("name"), loc)
            jobs.append(Job(f"{c['name']} Careers", c["name"], p.get("name", ""), loc, url, job_id=jid,
                            id_fallback=fb, posted=parse_date(p.get("t_create")),
                            load_description=_eightfold_v2_description(api, c["domain"], pid) if pid else None))
        start += len(page)
        if not page or start >= count:
            break
    return jobs, start >= count


def _eightfold_v2_description(api, domain, pid):
    def load():
        return html_to_text(get_json(f"{api}/{pid}?domain={domain}").get("job_description", ""))
    return load


# ---------- Jibe (AMD) ----------

JIBE_PAGE, JIBE_MAX = 100, 3000


def jibe(c):
    """Jibe (iCIMS) career sites: <host>/api/jobs, with descriptions in the list."""
    system, jobs, page_no, total, read = _careers_id(c), [], 1, 0, 0
    while read < JIBE_MAX:
        data = get_json(f"https://{c['host']}/api/jobs?location=India&page={page_no}&limit={JIBE_PAGE}")
        page = data.get("jobs") or []
        total = data.get("totalCount", 0)
        read += len(page)
        for item in page:
            j = item.get("data") or {}
            loc = j.get("full_location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
            if j.get("country_code") != "IN" and not is_india(loc):
                continue
            slug = j.get("slug") or j.get("req_id")
            url = f"https://{c['host']}/careers-home/jobs/{slug}?lang=en-us" if slug else j.get("apply_url", "")
            jid, fb = posting_id(system, "", j.get("req_id") or slug, url, c["name"], j.get("title"), loc)
            desc = j.get("description") or "\n".join(j.get(k) or "" for k in ("responsibilities", "qualifications"))
            jobs.append(Job(f"{c['name']} Careers", c["name"], j.get("title", ""), loc, url, job_id=jid,
                            id_fallback=fb, posted=parse_date(j.get("posted_date")), description=html_to_text(desc)))
        if len(page) < JIBE_PAGE:     # the reported total can overstate what is served
            return jobs, True
        page_no += 1
    return jobs, read >= total


# ---------- TalentBrew / Radancy (Synopsys) ----------

TB_PAGE, TB_MAX_PAGES = 100, 30


def talentbrew(c):
    """Radancy "TalentBrew" career sites: the search returns an HTML fragment inside JSON."""
    base, system, jobs = f"https://{c['host']}", _careers_id(c), []
    page, pages = 1, 1
    while page <= pages and page <= TB_MAX_PAGES:
        data = get_json(f"{base}/search-jobs/results?CurrentPage={page}&RecordsPerPage={TB_PAGE}&SearchType=5"
                        f"&SearchResultsModuleName=Search+Results&FacetFilters%5B0%5D.ID={c['country_facet']}"
                        f"&FacetFilters%5B0%5D.FacetType=2&FacetFilters%5B0%5D.IsApplied=true",
                        headers={"X-Requested-With": "XMLHttpRequest"})
        soup = BeautifulSoup(data.get("results", ""), "html.parser")
        box = soup.select_one("#search-results")
        pages = int(box.get("data-total-pages", 1)) if box else 1
        for a in soup.select("a.sr-job-link[data-job-id]"):
            text = lambda sel: (a.select_one(sel).get_text(" ", strip=True) if a.select_one(sel) else "")
            title = a.h2.get_text(" ", strip=True) if a.h2 else ""
            loc = text(".job-location")
            url = base + a["href"] if a.get("href") else ""
            jid, fb = posting_id(system, "", a.get("data-job-id"), url, c["name"], title, loc)
            jobs.append(Job(f"{c['name']} Careers", c["name"], title, loc if is_india(loc) else f"India ({loc})", url,
                            job_id=jid, id_fallback=fb, posted=_us_date(text(".job-date-posted")),
                            load_description=_json_ld_description(url) if url else None))
        page += 1
    return jobs, page > pages


def _us_date(text):
    """"Posted: 09/28/2026" -> date"""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    return parse_date(f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}") if m else None


def _json_ld_description(url):
    def load():
        return json_ld_description(get_text(url, headers={"Accept": "text/html"}))
    return load


# ---------- Oracle Recruiting Cloud (Dell) ----------

ORACLE_PAGE, ORACLE_MAX = 25, 2000


def oracle(c):
    """Oracle Recruiting Cloud "Candidate Experience" sites. The finder's ; and , must not be encoded."""
    api, system = f"https://{c['api']}/hcmRestApi/resources/latest", _careers_id(c)
    jobs, offset, total = [], 0, 0
    while offset < ORACLE_MAX:
        finder = (f"findReqs;siteNumber={c['site']},facetsList=LOCATIONS,limit={ORACLE_PAGE},offset={offset},"
                  f"selectedLocationsFacet={c['country_facet']},sortBy=POSTING_DATES_DESC")
        items = get_json(f"{api}/recruitingCEJobRequisitions?onlyData=true"
                         f"&expand=requisitionList.secondaryLocations&finder={finder}").get("items") or [{}]
        page = items[0].get("requisitionList") or []
        total = items[0].get("TotalJobsCount", 0)
        for r in page:
            loc = r.get("PrimaryLocation", "")
            rid = r.get("Id")
            url = f"https://{c['jobs_page']}/job/{rid}" if rid else ""
            jid, fb = posting_id(system, "", rid, url, c["name"], r.get("Title"), loc)
            jobs.append(Job(f"{c['name']} Careers", c["name"], r.get("Title", ""),
                            loc if is_india(loc) else f"India ({loc})", url, job_id=jid, id_fallback=fb,
                            posted=parse_date(r.get("PostedDate")),
                            load_description=_oracle_description(api, c["site"], rid) if rid else None))
        offset += len(page)
        if not page or offset >= total:
            break
    return jobs, offset >= total


def _oracle_description(api, site, rid):
    def load():
        d = (get_json(f'{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true'
                      f'&finder=ById;Id="{rid}",siteNumber={site}').get("items") or [{}])[0]
        return "\n".join(html_to_text(d.get(k) or "") for k in
                         ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
    return load


# ---------- Goldman Sachs ----------

GS_API = "https://api-higher.gs.com/gateway/api/v1/graphql"
GS_QUERY = """query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { jobTitle corporateTitle jobFunction lastPostedDate descriptionHtml
            locations { city country } externalSource { sourceId } } } }"""
GS_PAGE = 100


def goldman(c):
    """higher.gs.com's own GraphQL search: experienced and campus roles are searched separately."""
    jobs, complete = [], True
    for levels in (["EARLY_CAREER", "PROFESSIONAL"], ["CAMPUS"]):
        page, read, total = 0, 0, 0
        while page * GS_PAGE < 3000:
            variables = {"searchQueryInput": {
                "page": {"pageSize": GS_PAGE, "pageNumber": page},
                "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
                "filters": [{"filterCategoryType": "LOCATION", "filters": [{"filter": "India", "subFilters": []}]}],
                "experiences": levels, "searchTerm": ""}}
            data = post_json(GS_API, {"operationName": "GetRoles", "query": GS_QUERY, "variables": variables},
                             headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"})
            if data.get("errors"):
                raise RuntimeError(str(data["errors"])[:150])
            found = (data.get("data") or {}).get("roleSearch") or {}
            items, total = found.get("items") or [], found.get("totalCount", 0)
            read += len(items)
            for r in items:
                loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x)
                                for l in r.get("locations") or [])
                if not is_india(loc):
                    continue
                sid = (r.get("externalSource") or {}).get("sourceId")
                url = f"https://higher.gs.com/roles/{sid}" if sid else ""
                # Titles name the team and rank ("Payments Tech - Associate - Hyderabad"); the job
                # function says what the role is.
                title, function = r.get("jobTitle", ""), r.get("jobFunction") or ""
                if function and function.lower() not in title.lower():
                    title = f"{title} ({function})"
                jid, fb = posting_id("goldman", "", sid, url, c["name"], title, loc)
                jobs.append(Job("Goldman Sachs Careers", c["name"], title, loc, url, job_id=jid, id_fallback=fb,
                                posted=parse_date(r.get("lastPostedDate")),
                                description=html_to_text(r.get("descriptionHtml") or "")))
            page += 1
            if not items or read >= total:
                break
        complete = complete and read >= total
    return jobs, complete


READERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
           "workday": workday, "eightfold": eightfold, "eightfold_v2": eightfold_v2, "jibe": jibe,
           "talentbrew": talentbrew, "oracle": oracle}
CUSTOM = {"amazon": amazon, "goldman": goldman}

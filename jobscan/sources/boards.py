"""Readers for open job boards (no login). Same contract as feeds.py: each returns (jobs, complete).

Unlike a company feed, a board lists many companies, so its Run log line is the board itself.
Each reader asks the board for fresher / 0-1 year tech jobs in India where the board can filter
that way, and puts any experience range the board states at the top of the description, where
the normal experience check reads it.

complete=True only when the board's whole fresher listing was read (so a missing posting
really was taken down). Foundit and Cutshort are read as a window of recent days, so they are
never complete and never close rows.
"""
import json
import re
from datetime import timedelta

import requests
from bs4 import BeautifulSoup

from ..core import (Job, Throttle, get_json, get_text, html_to_text, json_ld_description, parse_date,
                    posting_id, today)
from ..filters import is_india

REMOTE = "Remote (India)"
_VAGUE_PLACE = re.compile(r"remote|work from home|wfh|anywhere|multiple|pan india|^others?$", re.I)
_FOREIGN = re.compile(
    r"singapore|saudi|riyadh|jeddah|dubai|abu dhabi|\buae\b|qatar|doha|kuwait|oman|bahrain|overseas|"
    r"international|united states|\busa?\b|canada|united kingdom|\buk\b|london|germany|europe|australia|"
    r"malaysia|japan|philippines|nepal|sri lanka|bangladesh", re.I)


def _place(names):
    """Board locations -> one location string, or None if the job is outside India.
    These boards are Indian: "Remote" or "Anywhere in India" means anywhere in India, and a
    city missing from filters.INDIA_PLACES (Bhopal, Surat...) is still taken to be Indian."""
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return "India"
    if any(is_india(n) for n in names):
        return ", ".join(names)
    if all(_VAGUE_PLACE.search(n) for n in names):
        return REMOTE
    if all(_FOREIGN.search(n) or _VAGUE_PLACE.search(n) for n in names):
        return None
    return ", ".join(names) + ", India"


def _years(lo, hi) -> str:
    """The board's own experience range, as a line the experience check understands."""
    if lo is None and hi is None:
        return ""
    return f"Experience: {lo or 0}-{hi} years\n" if hi is not None else f"Experience: {lo}+ years\n"


# ---------- Unstop ----------

UNSTOP_API = "https://unstop.com/api/public/opportunity/search-result"
# Role slugs from unstop.com/api/workrelationship/workfunction/getAll. Unstop tags roles with
# AI, so the list is broad on purpose; the title check drops what isn't software.
UNSTOP_ROLES = (
    "software-development,frontend-development,backend-development,full-stack-development,"
    "web-development,mobile-app-development,android-development,ios-development,api-development,"
    "systems-programming,software-testing,qa-engineering,automation-testing,devops,cloud-engineering,"
    "platform-engineering,data-engineering,data-science,machine-learning-engineering,ai-engineering,"
    "applied-ai,nlp-engineering,security-engineering,embedded-systems-engineer")
UNSTOP_PAGE = 100


def unstop(c):
    jobs, ids, page, last = [], set(), 1, 1
    while page <= last and page <= 20:
        data = get_json(f"{UNSTOP_API}?opportunity=jobs&oppstatus=open&usertype=fresher"
                        f"&roles={UNSTOP_ROLES}&per_page={UNSTOP_PAGE}&page={page}",
                        headers={"Referer": "https://unstop.com/job"}).get("data") or {}
        last = data.get("last_page", 1)
        for x in data.get("data") or []:
            if x.get("id") in ids:
                continue
            ids.add(x.get("id"))
            detail = x.get("jobDetail") or {}
            places = [l for l in x.get("locations") or [] if (l.get("country") or "India") == "India"]
            if x.get("locations") and not places:
                continue    # every location is outside India
            cities = [l.get("city") for l in places] or detail.get("locations") or []
            place = REMOTE if detail.get("type") == "wfh" else _place(cities)
            if place is None:
                continue
            req = x.get("regnRequirements") or {}
            try:
                sector = json.loads(req.get("eligibility") or "{}").get("sector") or []
            except ValueError:
                sector = []
            head = _years(detail.get("min_experience"), detail.get("max_experience"))
            if "fresher" in sector:
                head += "Unstop lists this job as open to freshers.\n"
            url = x.get("seo_url") or (f"https://unstop.com/{x['public_url']}" if x.get("public_url") else "")
            jid, fb = posting_id("unstop", "", x.get("id"), url, (x.get("organisation") or {}).get("name"), x.get("title"))
            jobs.append(Job("Unstop", (x.get("organisation") or {}).get("name", ""), x.get("title", ""), place, url,
                            job_id=jid, id_fallback=fb,
                            posted=parse_date(x.get("approved_date") or req.get("start_regn_dt")),
                            description=head + html_to_text(x.get("details", ""))))
        page += 1
    # Unstop's total can overstate what it serves, so "complete" means every page was read.
    return jobs, page > last


# ---------- Hirist ----------

HIRIST_API = "https://gladiator.hirist.tech/job"
# Category ids from hirist.tech's own JS: Backend, Frontend, Mobile, DevOps/SRE, Emerging Tech,
# QA, AI/ML, Data Engineering, Full Stack, CyberSecurity. (Others: VLSI, SAP, BI, design, PM.)
HIRIST_CATEGORIES = "1,2,5,8,9,11,14,15,16,17"
HIRIST_HEADERS = {"version": "2", "Origin": "https://www.hirist.tech", "Referer": "https://www.hirist.tech/"}
_HIRIST = Throttle(0.3)


def hirist(c):
    jobs, ids, page, total = [], set(), 0, 0
    while page < 30:
        data = get_json(f"{HIRIST_API}/category/?categoryId={HIRIST_CATEGORIES}&minexp=0&maxexp=1"
                        f"&page={page}&size=100", headers=HIRIST_HEADERS)
        batch = data.get("data") or []
        total = data.get("totalJobs", total)
        for x in batch:
            if x.get("id") in ids:
                continue
            ids.add(x.get("id"))
            place = _place([l.get("name", "") for l in x.get("locations") or []])
            if place is None:
                continue
            company_data = x.get("companyData") or {}
            company = "Confidential" if x.get("confidential") else (
                (company_data.get("ambitionBoxInfo") or {}).get("companyName") or company_data.get("companyName", ""))
            title = _strip_company(x.get("title", ""), company)
            url = _hirist_url(x)
            jid, fb = posting_id("hirist", "", x.get("id"), url, company, title)
            jobs.append(Job("Hirist", company, title, place, url, job_id=jid, id_fallback=fb,
                            posted=parse_date(x.get("createdTimeMs")),
                            load_description=_hirist_description(x.get("id"), _years(x.get("min"), x.get("max")))))
        if not batch or not data.get("hasMore"):
            return jobs, len(ids) >= total
        page += 1
    return jobs, False


def _strip_company(title, company):
    """Hirist titles often start with the company: "Exotel - Integration Engineer I"."""
    head, sep, rest = title.partition(" - ")
    if sep and company and head.strip().lower() in company.lower():
        return rest.strip()
    return title.strip()


def _hirist_url(x):
    slug = re.sub(r"[\s-]+", "-", re.sub(r"[^a-z0-9\s-]", "", (x.get("title") or "").lower())).strip("-")
    return f"https://www.hirist.tech/j/{slug}-{x['id']}" if x.get("id") else ""


def _hirist_description(job_id, head):
    def load():
        _HIRIST.wait()
        data = get_json(f"{HIRIST_API}/detail?jobcode={job_id}", headers=HIRIST_HEADERS).get("data") or {}
        return head + html_to_text(data.get("introText", ""))
    return _keeping(head, load)


# ---------- Foundit ----------

FOUNDIT = "https://www.foundit.in"
# The API answers only requests that look like they come from its own search page.
FOUNDIT_HEADERS = {"Accept": "application/json, text/plain, */*", "Referer": f"{FOUNDIT}/srp/results"}
# Comma = OR. The title check drops the non-software "engineer" roles this brings in.
FOUNDIT_QUERY = ("software,developer,sde,programmer,full stack,backend,frontend,devops,sre,"
                 "machine learning,data engineer,qa automation,android,ios")
FOUNDIT_DAYS = 2      # posted in the last 2 days: a daily run, plus a missed day
FOUNDIT_MAX = 2000
_FOUNDIT = Throttle(0.5)


def foundit(c):
    jobs, ids, start, total = [], set(), 0, 0
    while start < FOUNDIT_MAX:
        resp = get_json(f"{FOUNDIT}/middleware/jobsearch?sort=2&limit=100&start={start}&query={FOUNDIT_QUERY}"
                        f"&experienceRanges=0~1&locations=India&jobFreshness={FOUNDIT_DAYS}",
                        headers=FOUNDIT_HEADERS).get("jobSearchResponse") or {}
        total = ((resp.get("meta") or {}).get("paging") or {}).get("total", 0)
        page = [x for x in resp.get("data") or [] if x.get("jobId")]   # the rest are ad slots
        for x in page:
            if x["jobId"] in ids:
                continue
            ids.add(x["jobId"])
            loc = x.get("locations") or ""
            place = _place(loc.split(",")) or f"India ({loc})"   # the search is limited to India
            lo = (x.get("minimumExperience") or {}).get("years")
            hi = (x.get("maximumExperience") or {}).get("years")
            head = _years(lo, hi) if hi else ""   # 0-0 means the board doesn't know
            path = x.get("seoJdUrl") or x.get("jdUrl")
            url = FOUNDIT + path if path else ""
            jid, fb = posting_id("foundit", "", x["jobId"], url, x.get("companyName"), x.get("title"))
            jobs.append(Job("Foundit", x.get("companyName", ""), x.get("title", ""), place, url,
                            job_id=jid, id_fallback=fb, posted=parse_date(x.get("freshness") or x.get("createdAt")),
                            load_description=_foundit_description(x["jobId"], head)))
        start += 100
        if not page or start >= total:
            break
    return jobs, False   # a window of recent days, never the whole board


def _foundit_description(job_id, head):
    def load():
        _FOUNDIT.wait()
        d = get_json(f"{FOUNDIT}/middleware/jobdetail/{job_id}", headers=FOUNDIT_HEADERS)
        return head + html_to_text((d.get("jobDetailResponse") or {}).get("description", ""))
    return _keeping(head, load)


# ---------- Internshala (fresher jobs) ----------

INTERNSHALA = "https://internshala.com"
# Comma = OR. computer-science already covers software/web/full-stack/backend/frontend.
INTERNSHALA_PATH = ("computer-science,software-development,web-development,software-testing,java,"
                    "mobile-app-development,machine-learning,artificial-intelligence-ai,data-science-jobs/")
_INTERNSHALA = Throttle(0.5)


def internshala(c):
    """The fresher-jobs listing, through the JSON endpoint its infinite scroll uses. Once the
    jobs run out the listing continues with internships, which end the read."""
    jobs, ids, page, done = [], set(), 1, False
    while page <= 30:
        data = get_json(f"{INTERNSHALA}/fresher-jobs_ajax/{INTERNSHALA_PATH}" + (f"page-{page}/" if page > 1 else ""),
                        headers={"X-Requested-With": "XMLHttpRequest"})
        cards = BeautifulSoup(data.get("internship_list_html", ""), "html.parser").select(
            "div.individual_internship[internshipid]")
        for card in cards:
            if card.get("employment_type") != "job":
                done = True
                continue
            job = _internshala_card(card)
            if job and job.job_id not in ids:
                ids.add(job.job_id)
                jobs.append(job)
        if done or data.get("is_last_page") or not cards:
            return jobs, True
        page = max(page + 1, int(data.get("next_page_number") or 0))
    return jobs, False


def _internshala_card(card):
    text = lambda el: el.get_text(" ", strip=True) if el else ""
    link = card.select_one("a.job-title-href")
    href = card.get("data-href") or (link.get("href") if link else "")
    url = INTERNSHALA + href if href else ""
    places = [text(a) for a in card.select("p.locations span a")] or [text(card.select_one("p.locations span"))]
    place = _place(places)
    if place is None:
        return None
    experience = ""
    icon = card.select_one(".detail-row-1 i.ic-16-briefcase")
    if icon:
        experience = text(icon.parent.select_one("span.desktop") or icon.parent.select_one("span"))
    head = f"Experience: {experience}\n" if experience else ""
    company, title = text(card.select_one("p.company-name")), text(link)
    jid, fb = posting_id("internshala", "", card.get("internshipid"), url, company, title)
    return Job("Internshala", company, title, place, url, job_id=jid, id_fallback=fb,
               posted=_internshala_posted(href, text(card.select_one(".detail-row-2 .status-info span"))),
               load_description=_internshala_description(url, head) if url else None)


def _internshala_posted(href, relative):
    """The detail URL ends in the posting's Unix time; the card only says "3 days ago"."""
    m = re.search(r"(\d{10})/?$", href or "")
    if m:
        return parse_date(int(m.group(1)))
    t = (relative or "").lower()
    if "today" in t or "hour" in t or "just" in t:
        return today()
    m = re.search(r"(\d+)\s*(day|week|month)", t)
    if not m:
        return None
    n = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    return today() - timedelta(days=n)


def _internshala_description(url, head):
    def load():
        _INTERNSHALA.wait()
        page = get_text(url, headers={"Accept": "text/html"})
        text = json_ld_description(page)
        if not text:
            body = BeautifulSoup(page, "html.parser").select_one(".detail_view .internship_details .text-container")
            text = body.get_text("\n", strip=True) if body else ""
        return head + text
    return _keeping(head, load)


# ---------- Cutshort ----------

CUTSHORT = "https://cutshort.io/backend-api/webpage/jobs/startup-jobs"
CUTSHORT_DAYS = 10    # every posting on the site, newest first: read back this many days
CUTSHORT_MAX_YEARS = 5  # "0-10 years" ranges come from bulk postings, not fresher roles


def cutshort(c):
    """Cutshort has no fresher filter, so this reads its all-jobs feed (the "startup-jobs"
    listing carries every posting) back CUTSHORT_DAYS and keeps 0-1 year minimums."""
    jobs, cutoff = [], today() - timedelta(days=CUTSHORT_DAYS)
    for page in range(1, 31):
        # Cloudflare can serve a days-old copy of a page; the API ignores the extra parameter.
        try:
            data = get_json(f"{CUTSHORT}?page={page}&_={today().isoformat()}")
        except requests.HTTPError as e:
            if page > 1 and e.response is not None and e.response.status_code == 400:
                return jobs, False      # "jobs_not_found": past the last page
            raise
        batch = ((data.get("data") or {}).get("pageData") or {}).get("jobs") or []
        for x in batch:
            exp = x.get("expRange") or {}
            lo, hi = exp.get("min"), exp.get("max")
            if lo is None or lo > 1 or (hi is not None and hi > CUTSHORT_MAX_YEARS):
                continue
            place = REMOTE if x.get("remoteType") == "remote_only" else _place(x.get("locations") or [])
            if place is None:
                continue
            company = (x.get("companyDetails") or {}).get("name") or (x.get("companyId") or {}).get("name", "")
            url = x.get("publicUrl", "")
            jid, fb = posting_id("cutshort", "", x.get("_id"), url, company, x.get("headline"))
            jobs.append(Job("Cutshort", company, x.get("headline", ""), place, url, job_id=jid, id_fallback=fb,
                            posted=parse_date((x.get("jobFactSummary") or {}).get("postedDate")),
                            description=_years(lo, hi) + html_to_text(x.get("sanitizedComment", ""))))
        dates = [parse_date((x.get("jobFactSummary") or {}).get("postedDate")) for x in batch]
        if not batch or min((d for d in dates if d), default=cutoff) < cutoff:
            break
    return jobs, False   # a window of recent days, never the whole board


def _keeping(head, load):
    """A failed detail request still leaves what the listing said about experience."""
    def safe():
        try:
            return load()
        except Exception:
            return head
    return safe


BOARDS = {"Unstop": unstop, "Internshala": internshala, "Hirist": hirist, "Cutshort": cutshort, "Foundit": foundit}

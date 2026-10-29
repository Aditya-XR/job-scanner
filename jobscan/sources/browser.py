"""Job boards read through a real browser on the laptop.

The installed Chrome is driven through Playwright in a visible window, and each site's own
search page is opened; the reader keeps the API responses that page makes as they arrive.

- Naukri's search API refuses plain requests ("recaptcha required"): its own page gets the
  token. Headless Chrome is refused.
- LinkedIn, signed in as the burner account (LINKEDIN_LI_AT): its public search allows a
  connection only a page or two a day, while the signed-in search page lists hundreds of
  jobs and prefetches their descriptions itself.

Nothing here solves, skips or disguises anything: if a site challenges the browser or asks
the account to sign in again, the read stops and the Run log says so.

Playwright is a laptop-only dependency (requirements-laptop.txt), imported only when a reader
runs.
"""
import logging
import random
from urllib.parse import urlencode

from ..core import ROOT, Job, env, html_to_text, out_of_time, parse_date, posting_id
from .boards import LINKEDIN, LINKEDIN_DAYS, LINKEDIN_QUERIES, _place, _years, linkedin_filters

PROFILE = ROOT / "browser-profile"     # the browser's cookies between runs (git-ignored)
log = logging.getLogger("jobscan")

NAUKRI = "https://www.naukri.com"
# Naukri's own search pages (www.naukri.com/<slug>-jobs), with its experience and job-age filters.
# Naukri serves this browser only the first page of each search (20 jobs; page 2 answers 400,
# even from its own Next button), so the searches are many and narrow instead of deep. Each
# answer also says how many jobs match (noOfJobs): a search with more than its page is read again
# city by city (/<slug>-jobs-in-<city>) plus remote-only, and a city that still has more, once
# per work mode. 2026-10-30, last 2 days: "software engineer" had 159 jobs, Bengaluru 39 of them,
# 33 of those in the office.
NAUKRI_SEARCHES = ("software-engineer", "software-developer", "sde", "frontend-developer", "backend-developer",
                   "full-stack-developer", "java-developer", "python-developer", "react-developer",
                   "node-js-developer", "android-developer", "qa-automation-engineer")
NAUKRI_DAYS = 2                        # posted in the last 2 days: a daily run, plus a missed day
NAUKRI_CITIES = ("bengaluru", "hyderabad", "pune", "chennai", "delhi-ncr", "mumbai")
NAUKRI_WORK_MODES = ("0", "3", "2")    # Naukri's wfhType: office, hybrid, remote
NAUKRI_REMOTE = "2"
NAUKRI_MAX_SPLITS = 60                 # extra searches a run, at most: a person's pace, not a crawl
NAUKRI_PAUSE_MS = (3000, 6000)
NAUKRI_API = "/jobapi/v3/search"


def naukri(c):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(PROFILE / "naukri"), channel="chrome", headless=False,
                                                       viewport={"width": 1280, "height": 860})
        try:
            tab = browser.new_page()
            opened = []

            def search(url):
                if opened:
                    tab.wait_for_timeout(random.randint(*NAUKRI_PAUSE_MS))
                opened.append(url)
                return _naukri_search(tab, url)

            jobs, c["unread"] = read_naukri(search, c)
        finally:
            browser.close()
    return jobs, False   # a window of recent days, never the whole board


def naukri_url(slug, days, city=None, mode=None):
    return (f"{NAUKRI}/{slug}-jobs{f'-in-{city}' if city else ''}?experience=0&jobAge={days}"
            f"{f'&wfhType={mode}' if mode else ''}")


def read_naukri(search, c):
    """Every search in NAUKRI_SEARCHES, split while Naukri says it has more than it showed.
    `search(url)` returns the search API's answer. Returns (jobs, unread): unread is how many
    jobs Naukri counted that no page showed, an upper bound (two searches can count one job)."""
    days = c.get("days", NAUKRI_DAYS)
    jobs, ids, splits_left, unread = [], set(), [NAUKRI_MAX_SPLITS], 0

    def read(slug, city=None, mode=None):
        """Naukri's own IDs of the jobs shown for this search and its splits, and its total."""
        body = search(naukri_url(slug, days, city, mode))
        shown = set()
        for x in body.get("jobDetails") or []:
            shown.add(str(x.get("jobId")))
            job = naukri_job(x)
            if job and job.job_id not in ids:
                ids.add(job.job_id)
                jobs.append(job)
        total = int(body.get("noOfJobs") or 0)
        if total > len(shown):
            if not city and not mode:
                parts = [(place, None) for place in NAUKRI_CITIES] + [(None, NAUKRI_REMOTE)]
            elif city and not mode:
                parts = [(city, m) for m in NAUKRI_WORK_MODES]
            else:
                parts = []
            for part_city, part_mode in parts:
                if not splits_left[0] or out_of_time(c):
                    break
                splits_left[0] -= 1
                shown |= read(slug, part_city, part_mode)[0]
        return shown, total

    try:
        for slug in NAUKRI_SEARCHES:
            if out_of_time(c):
                log.info("Naukri: stopped at the time limit, before %s", slug)
                break
            shown, total = read(slug)
            unread += max(0, total - len(shown))
    except RuntimeError as e:
        if not jobs:
            raise
        log.info("Naukri: stopped after an error, keeping what was read: %s", e)
    return jobs, unread


def _naukri_search(tab, url):
    """Open one search page and return the search API response the page itself makes."""
    try:
        with tab.expect_response(lambda r: NAUKRI_API in r.url and r.request.method == "GET", timeout=45000) as hit:
            tab.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:   # Playwright's TimeoutError: no search call, so the page was refused
        raise RuntimeError(f"Naukri served no search results ({tab.title()[:60]!r}): {e.__class__.__name__}") from None
    resp = hit.value
    if resp.status != 200:
        raise RuntimeError(f"Naukri's search API answered {resp.status}")
    return resp.json()


def naukri_job(x):
    """One entry of Naukri's search API response -> Job (None if the job is outside India)."""
    label = lambda kind: next((p.get("label", "") for p in x.get("placeholders") or [] if p.get("type") == kind), "")
    place = _place(label("location").split(","))
    if place is None:
        return None
    lo, hi = x.get("minimumExperience"), x.get("maximumExperience")
    head = _years(int(lo) if str(lo).isdigit() else None, int(hi) if str(hi).isdigit() else None)
    path = x.get("jdURL") or ""
    url = path if path.startswith("http") else (NAUKRI + path if path else "")
    company, title = x.get("companyName", ""), x.get("title", "")
    jid, fb = posting_id("naukri", "", x.get("jobId"), url, company, title)
    return Job("Naukri", company, title, place, url, job_id=jid, id_fallback=fb, posted=parse_date(x.get("createdDate")),
               description=head + html_to_text(x.get("jobDescription") or ""))


# ---------- LinkedIn, signed in as the burner account ----------

LINKEDIN_PAGES = 4                # 25 jobs a page: up to 100 a search
LINKEDIN_MAX_PAGES = 6            # catching up after days off: a page more a day, but no more than this
LINKEDIN_PAUSE_MS = (5000, 9000)  # on each page, at a person's pace: the account is what's at stake
LINKEDIN_CARDS = "JobCards"       # voyagerJobsDashJobCards: the search results, and the description prefetch
_SIGN_IN_PAGES = ("/checkpoint", "/login", "/authwall", "/uas/")


class SignInNeeded(RuntimeError):
    pass


def linkedin_signed_in(c):
    from playwright.sync_api import sync_playwright

    days = c.get("days", LINKEDIN_DAYS)
    pages, filters = min(LINKEDIN_PAGES + max(0, days - LINKEDIN_DAYS), LINKEDIN_MAX_PAGES), linkedin_filters(days)
    cards, descriptions = {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(PROFILE / "linkedin"), channel="chrome", headless=False,
                                                       viewport={"width": 1366, "height": 900})
        try:
            browser.add_cookies([{"name": "li_at", "value": env("LINKEDIN_LI_AT"), "domain": ".linkedin.com",
                                  "path": "/", "secure": True, "httpOnly": True, "sameSite": "None"}])
            tab = browser.new_page()
            responses = []
            tab.on("response", lambda r: responses.append(r)
                   if "/voyager/api/" in r.url and LINKEDIN_CARDS in r.url else None)
            for query in LINKEDIN_QUERIES:
                for page in range(pages):
                    if out_of_time(c):
                        break
                    responses.clear()
                    params = {"keywords": query, **filters, **({"start": page * 25} if page else {})}
                    tab.goto(f"{LINKEDIN}/jobs/search/?{urlencode(params)}", wait_until="domcontentloaded",
                             timeout=60000)
                    if any(k in tab.url for k in _SIGN_IN_PAGES):
                        raise SignInNeeded("LinkedIn wants the burner account to sign in again or pass a security "
                                           "check: open LinkedIn in Chrome as the burner, then recopy li_at into .env")
                    tab.wait_for_timeout(random.randint(*LINKEDIN_PAUSE_MS))   # its cards, then their descriptions
                    total = read_linkedin([_json(r) for r in responses], cards, descriptions, start=page * 25)
                    if (page + 1) * 25 >= total:
                        break
        finally:
            browser.close()
    return [linkedin_job(card, descriptions.get(native, "")) for native, card in cards.items()], False


def _json(response):
    try:
        return response.json() if response.status == 200 else {}
    except Exception:   # a response the page abandoned on its way to the next one
        return {}


def read_linkedin(bodies, cards, descriptions, start=0):
    """Collect job cards and prefetched descriptions from LinkedIn's API responses (normalized
    JSON: the entities sit in "included"). Returns the search's total, or 0 if none was seen.

    A search page also fetches the next page's cards ahead of time, but prefetches descriptions
    only for its own 25: cards are taken from the result starting at `start` only, and the next
    page's are read, with their descriptions, when that page is opened."""
    total = 0
    for body in bodies:
        paging = (body.get("data") or {}).get("paging") or {}
        total = max(total, paging.get("total") or 0)
        for x in body.get("included") or []:
            kind = x.get("$type", "")
            if (kind.endswith(".JobPostingCard") and x.get("jobPostingTitle") and x.get("jobPostingUrn")
                    and paging.get("start") == start):
                cards.setdefault(x["jobPostingUrn"].rsplit(":", 1)[-1], x)
            elif kind.endswith(".JobDescription") and x.get("entityUrn"):
                text = (x.get("descriptionText") or {}).get("text") or ""
                if text:
                    descriptions[x["entityUrn"].rsplit(":", 1)[-1]] = text
    return total


def linkedin_job(card, description):
    """One signed-in search card (+ its prefetched description) -> Job. Same IDs as the public
    search's, so a job seen both ways stays one row."""
    native = card["jobPostingUrn"].rsplit(":", 1)[-1]
    company = (card.get("primaryDescription") or {}).get("text", "")
    title = card.get("jobPostingTitle", "")
    place = _place([(card.get("secondaryDescription") or {}).get("text", "")])
    listed = next((f.get("timeAt") for f in card.get("footerItems") or [] if f.get("type") == "LISTED_DATE"), None)
    url = f"{LINKEDIN}/jobs/view/{native}/"
    jid, fb = posting_id("linkedin", "", native, url, company, title)
    return Job("LinkedIn", company, title, place or "", url, job_id=jid, id_fallback=fb, posted=parse_date(listed),
               description=description)

"""Job boards that answer only a real browser, read from the laptop.

Naukri's search API refuses plain requests ("recaptcha required"): its own search page gets
the token. So the installed Chrome is driven through Playwright, in a visible window (headless
Chrome is refused), and the page's own search API responses are read as they arrive. Nothing
here solves, skips or disguises anything: if the site challenges the browser, the read fails
and the Run log says so.

Playwright is a laptop-only dependency (requirements-laptop.txt), imported only when a reader
runs.
"""
from ..core import ROOT, Job, html_to_text, parse_date, posting_id
from .boards import _place, _years

PROFILE = ROOT / "browser-profile"     # the browser's cookies between runs (git-ignored)

NAUKRI = "https://www.naukri.com"
# Naukri's own search pages (www.naukri.com/<slug>-jobs), with its experience and job-age filters.
# Naukri serves this browser only the first page of each search (20 jobs; page 2 answers 400,
# even from its own Next button), so the searches are many and narrow instead of deep.
NAUKRI_SEARCHES = ("software-engineer", "software-developer", "sde", "frontend-developer", "backend-developer",
                   "full-stack-developer", "java-developer", "python-developer", "react-developer",
                   "node-js-developer", "android-developer", "qa-automation-engineer")
NAUKRI_FILTERS = "experience=0&jobAge=2"     # 0 years; posted in the last 2 days
NAUKRI_API = "/jobapi/v3/search"


def naukri(c):
    from playwright.sync_api import sync_playwright

    jobs, ids = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(PROFILE / "naukri"), channel="chrome", headless=False,
                                                       viewport={"width": 1280, "height": 860})
        try:
            tab = browser.new_page()
            for slug in NAUKRI_SEARCHES:
                for x in _naukri_search(tab, f"{NAUKRI}/{slug}-jobs?{NAUKRI_FILTERS}").get("jobDetails") or []:
                    job = naukri_job(x)
                    if job and job.job_id not in ids:
                        ids.add(job.job_id)
                        jobs.append(job)
        finally:
            browser.close()
    return jobs, False   # a window of recent days, never the whole board


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

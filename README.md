# Job Scanner

Every morning, collect new **software jobs in India that need less than 1 year of experience**
(or don't say) from company career sites and job boards, and add them to a Google Sheet with
a direct apply link.

```
company career feeds ─┐
open job boards ──────┼─> normalize ─> new? ─> India/remote? ─> software role? ─> < 1 yr exp? ─> Google Sheet ─> Gmail summary
guarded job boards ───┘                                                          (Gemini for unclear JDs)
```

## What it reads

| Group | Sources | Status |
|---|---|---|
| Company career feeds | 73 companies through Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Eightfold, Jibe, Radancy and Oracle career sites, plus Amazon's and Goldman Sachs' own APIs (see [`companies.yaml`](companies.yaml)) | ✅ daily |
| Open job boards | Unstop, Internshala, Hirist, Cutshort, Foundit (see [`boards.py`](jobscan/sources/boards.py)) | ✅ daily |
| Guarded job boards | LinkedIn, Naukri, Indeed, Wellfound, Instahyre (run from a laptop with a real browser) | planned |

Every morning at about 07:30 IST, GitHub Actions ([`daily.yml`](.github/workflows/daily.yml)) reads
the company feeds and the open job boards, updates the sheet and sends the Gmail summary. Your
laptop doesn't need to be on.

### What each job board is asked for

| Board | What is read | Can close rows? |
|---|---|---|
| Unstop | every open job tagged for freshers in tech roles | yes |
| Internshala | the whole fresher-jobs listing for computer-science categories | yes |
| Hirist | every job in the software categories with a 0-1 year range | yes |
| Cutshort | every posting from the last 10 days with a minimum of 0-1 years | no (window) |
| Foundit | software/developer searches, 0-1 years, posted in the last 2 days | no (window) |

A board's own experience range is written at the top of the description, where the normal
experience check reads it. Boards re-list jobs from company sites under their own IDs, so a
board posting with the same company and title as an open row from another site is added to
that row's **Also seen on** instead of becoming a new row. A company's own posting is never
matched this way: it always gets its own row.

## How a job is kept

1. **New:** every posting is identified by its hiring system's own ID (Greenhouse/Lever/Ashby/
   SmartRecruiters ID, Workday posting path, Amazon/Microsoft job ID), never by its title. Two
   openings that share a title are separate rows, and a job that is taken down and posted again
   gets a new ID, so it shows up again.
2. **India or remote-India:** location names an Indian city, India, or remote APAC.
3. **Software role:** title matches software keywords; senior/lead/staff/II/III titles are dropped.
4. **Under 1 year of experience:** the smallest "X years" requirement in the description is 0, or
   none is stated, or the title is intern/graduate/trainee. Mixed signals ("freshers welcome, 2+ years
   preferred") go to Gemini 3.5 Flash-Lite for a keep/drop call. Set `MAX_MIN_YEARS=1` to also keep "1+ years".

The same opening posted once per city in one run becomes one row listing every city. A job
judged and dropped is remembered (hidden **Seen** tab) so it isn't re-checked daily.

## Open? column

| Value | Meaning |
|---|---|
| `Open` | still listed in the company's feed today |
| `Closed 2026-10-05` | missing from a complete read of the feed on that date (row greyed out) |
| `Closed (reposted 2026-10-07)` | the same posting came back after 2+ days; a fresh row was added on top |

A job is only marked closed when its company's feed (or, for a row first found on a job board,
that board) was read in full without errors, so a failed or partial read never closes anything.
A job board that still lists a job the company has taken down doesn't keep it open. A job gone
for a single day is treated as a feed glitch and reopened.

## Safety checks

- Each run counts postings without an ID and IDs shared by two different jobs, per source,
  in the Run log. Either one triggers the Gmail alert. A missing ID falls back to the apply
  link, so the worst case is a duplicate row, never a hidden job.
- A source that returns no jobs or fails on two runs in a row is listed in the Gmail summary.
- `pytest` runs on every push (recorded responses from every hiring system and job board).
- Every Monday, GitHub Actions runs `python -m jobscan.audit`, a live check of every source's IDs.

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Windows
cp .env.example .env                               # fill in the values
.venv\Scripts\python check_setup.py                # verifies every credential, never prints secrets
```

You need a Google Cloud service account with the Sheets and Drive APIs enabled, a Google Sheet
shared with it as Editor, a Gemini API key, and a Gmail app password. See `.env.example`.

## Run

```bash
python -m jobscan --dry-run          # print what would be added
python -m jobscan                    # update the sheet and send the email
python -m jobscan --group boards     # only the job boards (--group feeds: only company sites)
python -m jobscan --only meesho,nvidia,unstop --dry-run
python -m jobscan.audit              # live check that every posting has a unique ID
pytest -q tests                      # needs: pip install -r requirements-dev.txt
```

## Adding a company

Find which hiring system its careers page uses (the apply links usually show it:
`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`,
`jobs.smartrecruiters.com/<slug>`, `<tenant>.wd5.myworkdayjobs.com/<site>`), then add one line
under that system in `companies.yaml`. Workday tenants live on different hosts (`wd1`, `wd5`,
`wd12`, `wd504`...): copy it from a job link.

## Secrets

`.env` and `secrets/` are git-ignored. In GitHub Actions the same values come from repository
secrets: `GOOGLE_SERVICE_ACCOUNT_JSON` (the key file's contents), `SHEET_ID`, `GEMINI_API_KEY`,
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` and `ALERT_TO`; `MAX_MIN_YEARS` and `GEMINI_MODEL` are
optional repository variables. The daily workflow only runs on its schedule or by hand, never
for pull requests, so forks can't reach the secrets. Logs only contain counts, never credentials.

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
| Company career feeds | 65 companies through Greenhouse, Lever, Ashby, SmartRecruiters, Workday, plus Amazon and Microsoft's own APIs (see [`companies.yaml`](companies.yaml)) | ✅ |
| Open job boards | Unstop, Internshala, Cutshort, Foundit, Hirist | planned |
| Guarded job boards | LinkedIn, Naukri, Indeed, Wellfound, Instahyre (run from a laptop with a real browser) | planned |

## How a job is kept

1. **New:** same company + title not added in the last 30 days (also merges duplicates across sources).
2. **India or remote-India:** location names an Indian city, India, or remote APAC.
3. **Software role:** title matches software keywords; senior/lead/staff/II/III titles are dropped.
4. **Under 1 year of experience:** the smallest "X years" requirement in the description is 0, or
   none is stated, or the title is intern/graduate/trainee. Mixed signals ("freshers welcome, 2+ years
   preferred") go to Gemini Flash for a keep/drop call. Set `MAX_MIN_YEARS=1` to also keep "1+ years".

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
python -m jobscan --only meesho,nvidia --dry-run
```

## Adding a company

Find which hiring system its careers page uses (the apply links usually show it:
`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`,
`jobs.smartrecruiters.com/<slug>`, `<tenant>.wd5.myworkdayjobs.com/<site>`), then add one line
under that system in `companies.yaml`.

## Secrets

`.env` and `secrets/` are git-ignored. In GitHub Actions the same values come from repository
secrets. Logs only contain counts, never credentials.

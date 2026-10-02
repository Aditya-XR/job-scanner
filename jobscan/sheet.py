"""Google Sheet: the Jobs tab (newest first, your Status/Notes never overwritten) and the Run log."""
import re
from datetime import date, timedelta

import gspread

from .core import ROOT, env, today

HEADERS = ["Found on", "Company", "Title", "Location", "Posted", "Experience", "Why kept",
           "Source", "Apply link", "Also seen on", "Status", "Notes", "Key"]
COL = {h: i for i, h in enumerate(HEADERS)}
STATUS_OPTIONS = ["To apply", "Applied", "OA", "Interview", "Offer", "Rejected", "Skip"]
LOG_HEADERS = ["Run at (IST)", "Group", "Source", "Fetched", "India + software", "Kept",
               "New in sheet", "Error"]
REPEAT_WINDOW_DAYS = 30  # same company + title within this window counts as a duplicate


def job_key(company: str, title: str) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return f"{norm(company)}|{norm(title)}"


class Sheet:
    def __init__(self):
        gc = gspread.service_account(filename=str(ROOT / env("GOOGLE_SERVICE_ACCOUNT_FILE")))
        self.book = gc.open_by_key(env("SHEET_ID"))
        self.jobs = self._tab("Jobs", HEADERS)
        self.log = self._tab("Run log", LOG_HEADERS)
        # Jobs already judged and dropped, so they aren't re-checked (or sent to Gemini) daily.
        self.seen = self._tab("Seen", ["Key", "Dropped on"], hidden=True)

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.book.id}/edit"

    def _tab(self, name, headers, hidden=False):
        try:
            ws = self.book.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(name, rows=1000, cols=len(headers))
            if hidden:
                ws.hide()
        if ws.row_values(1) != headers:
            ws.update([headers], "A1", value_input_option="RAW")
            self._format(ws, headers)
        return ws

    def _format(self, ws, headers):
        sid = ws.id
        requests = [
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                                       "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                           "backgroundColor": {"red": .9, "green": .93, "blue": .98}}},
                            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        ]
        if headers is HEADERS:
            widths = [95, 140, 300, 170, 95, 105, 230, 120, 320, 140, 105, 200, 100]
            requests += [
                {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                         "startIndex": i, "endIndex": i + 1},
                                               "properties": {"pixelSize": w}, "fields": "pixelSize"}}
                for i, w in enumerate(widths)]
            requests += [
                # Status dropdown
                {"setDataValidation": {
                    "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 10000,
                              "startColumnIndex": COL["Status"], "endColumnIndex": COL["Status"] + 1},
                    "rule": {"condition": {"type": "ONE_OF_LIST",
                                           "values": [{"userEnteredValue": v} for v in STATUS_OPTIONS]},
                             "showCustomUi": True, "strict": False}}},
                # Yellow when the job description didn't state experience: check before applying
                {"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": sid, "startRowIndex": 1, "endRowIndex": 10000,
                                "startColumnIndex": COL["Experience"], "endColumnIndex": COL["Experience"] + 1}],
                    "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Not mentioned"}]},
                                    "format": {"backgroundColor": {"red": 1, "green": .95, "blue": .7}}}}}},
                # Hide the internal Key column
                {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                         "startIndex": COL["Key"], "endIndex": COL["Key"] + 1},
                                               "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
            ]
        self.book.batch_update({"requests": requests})

    def recent_keys(self) -> set:
        """Keys of jobs added in the last REPEAT_WINDOW_DAYS days."""
        rows = self.jobs.get_values("A2:M")
        cutoff = today() - timedelta(days=REPEAT_WINDOW_DAYS)
        keys = set()
        for r in rows:
            if len(r) <= COL["Key"] or not r[COL["Key"]]:
                continue
            try:
                found = date.fromisoformat(r[0])
            except ValueError:
                found = today()
            if found >= cutoff:
                keys.add(r[COL["Key"]])
        for r in self.seen.get_values("A2:B"):
            if len(r) >= 2 and r[1] >= cutoff.isoformat():
                keys.add(r[0])
        return keys

    def add_seen(self, keys) -> None:
        if keys:
            self.seen.append_rows([[k, today().isoformat()] for k in sorted(keys)],
                                  value_input_option="RAW")

    def add_jobs(self, jobs) -> None:
        if not jobs:
            return
        rows = []
        for j in jobs:
            rows.append([
                today().isoformat(), _safe(j.company), _safe(j.title), _safe(j.location),
                j.posted.isoformat() if j.posted else "", j.experience, _safe(j.why_kept), j.source,
                j.url, ", ".join(j.also_seen_on), "", "", job_key(j.company, j.title)])
        # Insert at the top so the newest jobs come first; existing rows (and your
        # Status/Notes on them) move down together.
        self.jobs.insert_rows(rows, row=2, value_input_option="USER_ENTERED")

    def add_log(self, rows) -> None:
        if rows:
            self.log.append_rows(rows, value_input_option="USER_ENTERED")

    def previous_zero_sources(self, group: str) -> set:
        """Sources that fetched 0 jobs (or errored) on the previous run of this group."""
        rows = self.log.get_values("A2:H")
        runs = [r for r in rows if len(r) > 3 and r[1] == group]
        if not runs:
            return set()
        last_run = runs[-1][0]
        return {r[2] for r in runs if r[0] == last_run and (r[3] in ("0", "") or (len(r) > 7 and r[7]))}


def _safe(s: str) -> str:
    s = (s or "").strip()
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s

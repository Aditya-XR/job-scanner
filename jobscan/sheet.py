"""Google Sheet: the Jobs tab (newest first; your Status/Notes are never overwritten), the Run log,
and a hidden Seen tab of postings already judged and dropped."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import gspread

from .core import ROOT, env, fingerprint, posting_id, today
from .sources.boards import BOARDS
from .state import Row, Tracker

HEADERS = ["Found on", "Company", "Title", "Location", "Posted", "Experience", "Why kept",
           "Source", "Apply link", "Open?", "Also seen on", "Status", "Notes", "Job ID", "Fingerprint"]
COL = {h: i for i, h in enumerate(HEADERS)}
HIDDEN = ("Job ID", "Fingerprint")
# Layout of 2026-10-04 (first ID version), before the description fingerprint was added.
HEADERS_V2 = HEADERS[:-1]
# Layout used until 2026-10-04: identified jobs by company + title in a "Key" column.
HEADERS_V1 = ["Found on", "Company", "Title", "Location", "Posted", "Experience", "Why kept",
              "Source", "Apply link", "Also seen on", "Status", "Notes", "Key"]
STATUS_OPTIONS = ["To apply", "Applied", "OA", "Interview", "Offer", "Rejected", "Skip"]
LOG_HEADERS = ["Run at (IST)", "Group", "Source", "Fetched", "India + software", "Kept",
               "New in sheet", "Error", "Complete read", "ID fallbacks", "ID conflicts", "Closed today"]
SEEN_HEADERS = ["Job ID", "Dropped on", "Company", "Title"]
SEEN_KEEP_DAYS = 120
ID_SEP = " | "            # a row merged from identical copies lists all their IDs
APPLY_LABEL = "Apply ↗"


def _col(i: int) -> str:
    return chr(ord("A") + i)


class Sheet:
    def __init__(self):
        gc = gspread.service_account(filename=str(ROOT / env("GOOGLE_SERVICE_ACCOUNT_FILE")))
        self.book = gc.open_by_key(env("SHEET_ID"))
        self.jobs = self._worksheet("Jobs")
        self.log = self._worksheet("Run log")
        self.seen = self._worksheet("Seen", hidden=True)

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.book.id}/edit"

    def _worksheet(self, name, hidden=False):
        try:
            return self.book.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(name, rows=1000, cols=26)
            if hidden:
                ws.hide()
            return ws

    # ---------- layout ----------

    def prepare(self, fetched_jobs) -> str:
        """Bring every tab to the current layout before reading it. Returns what happened to Jobs."""
        header = self.jobs.row_values(1)
        if header == HEADERS:
            action = "ok"
        elif not header:
            self.jobs.update([HEADERS], "A1", value_input_option="RAW")
            self._format_jobs()
            action = "created"
        elif header == HEADERS_V1:
            self._migrate_v1(fetched_jobs)
            action = "migrated"
        elif header == HEADERS_V2:
            self._add_fingerprints(fetched_jobs)
            action = "added fingerprints"
        else:
            raise RuntimeError(f"Jobs tab has unexpected columns {header}; refusing to modify it")

        log_header = self.log.row_values(1)
        if log_header != LOG_HEADERS:
            if log_header and LOG_HEADERS[:len(log_header)] != log_header:
                raise RuntimeError(f"Run log tab has unexpected columns {log_header}")
            self.log.update([LOG_HEADERS], "A1", value_input_option="RAW")
            self._format_header(self.log)

        if self.seen.row_values(1) != SEEN_HEADERS:
            # Older Seen tabs stored company+title keys; those can't be trusted, so start over.
            self.seen.clear()
            self.seen.update([SEEN_HEADERS], "A1", value_input_option="RAW")
        self._prune_seen()
        return action

    def _migrate_v1(self, fetched_jobs):
        """Insert the Open? column and replace the company+title Key with posting IDs.
        Inserting a column shifts whole cells, so your Status and Notes stay on their rows."""
        n = len(self.jobs.get_values("A2:A"))
        urls = self._links(n)
        by_url = {j.url: j for j in fetched_jobs if j.url}
        ids, matches = [], []
        for url, row in zip(urls, self.jobs.get_values(f"A2:M{n + 1}")):
            match = by_url.get(url)
            matches.append(match)
            if match:
                ids.append(ID_SEP.join(match.all_ids))
            else:  # not in today's feeds (probably closed): keep its link as its identity
                ids.append(posting_id("", "", None, url, row[1], row[2], row[3])[0])
        prints = _fingerprints(matches)

        sid = self.jobs.id
        self.book.batch_update({"requests": [{"insertDimension": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": COL["Open?"],
                      "endIndex": COL["Open?"] + 1}, "inheritFromBefore": False}}]})
        jid, link, fpc = _col(COL["Job ID"]), _col(COL["Apply link"]), _col(COL["Fingerprint"])
        updates = [{"range": "A1", "values": [HEADERS]}]
        if n:
            updates += [
                {"range": f"{jid}2:{jid}{n + 1}", "values": [[i] for i in ids]},
                {"range": f"{fpc}2:{fpc}{n + 1}", "values": [[f] for f in prints]},
                {"range": f"{link}2:{link}{n + 1}", "values": [[_hyperlink(u)] for u in urls]},
            ]
        self.jobs.batch_update(updates, value_input_option="USER_ENTERED")
        self._format_jobs()

    def _add_fingerprints(self, fetched_jobs):
        """Layout v2 -> current: append the hidden Fingerprint column (description of each row's
        posting, from today's feeds; rows no longer listed get none and never match)."""
        by_id = {j.job_id: j for j in fetched_jobs}
        ids = [r[0] if r else "" for r in self.jobs.get_values(f"{_col(COL['Job ID'])}2:{_col(COL['Job ID'])}")]
        matches = [next((by_id[i] for i in cell.split(ID_SEP) if i in by_id), None) for cell in ids]
        prints = _fingerprints(matches)
        fpc = _col(COL["Fingerprint"])
        updates = [{"range": "A1", "values": [HEADERS]}]
        if ids:
            updates.append({"range": f"{fpc}2:{fpc}{len(ids) + 1}", "values": [[f] for f in prints]})
        self.jobs.batch_update(updates, value_input_option="RAW")
        self._format_jobs()

    def _links(self, n):
        """The real URL behind each Apply link cell (Sheets may display a page title instead)."""
        if not n:
            return []
        c = _col(HEADERS_V1.index("Apply link"))
        meta = self.book.fetch_sheet_metadata({
            "ranges": [f"'Jobs'!{c}2:{c}{n + 1}"], "includeGridData": True,
            "fields": "sheets(data(rowData(values(hyperlink,userEnteredValue))))"})
        out = []
        for rd in meta["sheets"][0]["data"][0].get("rowData", []) + [{}] * n:
            v = (rd.get("values") or [{}])[0]
            out.append(v.get("hyperlink") or (v.get("userEnteredValue") or {}).get("stringValue", ""))
        return out[:n]

    def _format_header(self, ws):
        self.book.batch_update({"requests": [
            {"updateSheetProperties": {"properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                                       "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                           "backgroundColor": {"red": .9, "green": .93, "blue": .98}}},
                            "fields": "userEnteredFormat(textFormat,backgroundColor)"}}]})

    def _format_jobs(self):
        """(Re)apply all Jobs-tab formatting. Safe to run repeatedly."""
        self._format_header(self.jobs)
        sid = self.jobs.id
        meta = self.book.fetch_sheet_metadata({"fields": "sheets(properties(sheetId),conditionalFormats)"})
        existing = next((len(s.get("conditionalFormats", [])) for s in meta["sheets"]
                         if s["properties"]["sheetId"] == sid), 0)
        rows = lambda c0, c1: {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 10000,
                               "startColumnIndex": c0, "endColumnIndex": c1}
        widths = [95, 140, 300, 170, 95, 105, 230, 120, 90, 150, 120, 105, 200, 120, 120]
        requests = [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in range(existing)]
        requests += [
            {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                                                     "startIndex": i, "endIndex": i + 1},
                                           "properties": {"pixelSize": w, "hiddenByUser": HEADERS[i] in HIDDEN},
                                           "fields": "pixelSize,hiddenByUser"}}
            for i, w in enumerate(widths)]
        requests += [
            {"setDataValidation": {
                "range": rows(COL["Status"], COL["Status"] + 1),
                "rule": {"condition": {"type": "ONE_OF_LIST",
                                       "values": [{"userEnteredValue": v} for v in STATUS_OPTIONS]},
                         "showCustomUi": True, "strict": False}}},
            # Yellow: the description didn't state experience, so check it before applying
            {"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [rows(COL["Experience"], COL["Experience"] + 1)],
                "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Not mentioned"}]},
                                "format": {"backgroundColor": {"red": 1, "green": .95, "blue": .7}}}}}},
            # Grey, struck-through title: the posting has been taken down
            {"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [rows(COL["Title"], COL["Title"] + 1)],
                "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                              "values": [{"userEnteredValue": f'=LEFT(${_col(COL["Open?"])}2,6)="Closed"'}]},
                                "format": {"textFormat": {"strikethrough": True,
                                                          "foregroundColor": {"red": .55, "green": .55, "blue": .55}}}}}}},
            {"addConditionalFormatRule": {"index": 1, "rule": {
                "ranges": [rows(0, COL["Job ID"])],
                "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                              "values": [{"userEnteredValue": f'=LEFT(${_col(COL["Open?"])}2,6)="Closed"'}]},
                                "format": {"textFormat": {"foregroundColor": {"red": .55, "green": .55, "blue": .55}}}}}}},
        ]
        self.book.batch_update({"requests": requests})

    # ---------- reading ----------

    def tracker(self) -> Tracker:
        rows = []
        for n, r in enumerate(self.jobs.get_values(f"A2:{_col(len(HEADERS) - 1)}"), start=2):
            r = r + [""] * (len(HEADERS) - len(r))
            if not r[COL["Job ID"]]:
                continue  # a row you typed in yourself: leave it alone
            source = r[COL["Source"]]
            rows.append(Row(number=n, ids=r[COL["Job ID"]].split(ID_SEP), company=r[COL["Company"]],
                            status=r[COL["Open?"]], title=r[COL["Title"]], user_status=r[COL["Status"]],
                            fingerprint=r[COL["Fingerprint"]], location=r[COL["Location"]], source=source,
                            feed=source if source in BOARDS else r[COL["Company"]],
                            also_seen_on=r[COL["Also seen on"]]))
        dropped = [r[0] for r in self.seen.get_values("A2:A") if r]
        return Tracker(rows, dropped, today())

    def _prune_seen(self):
        rows = self.seen.get_values("A2:D")
        cutoff = (today() - timedelta(days=SEEN_KEEP_DAYS)).isoformat()
        keep = [r for r in rows if len(r) > 1 and r[1] >= cutoff]
        if len(keep) < len(rows):
            self.seen.clear()
            self.seen.update([SEEN_HEADERS] + keep, "A1", value_input_option="RAW")

    # ---------- writing ----------

    def write_statuses(self, tracker: Tracker) -> int:
        """Write Open? changes and IDs of attached identical postings. Returns the number of Open? changes."""
        c = _col(COL["Open?"])
        changes = [{"range": f"{c}{r.number}", "values": [[r.new_status]]}
                   for r in tracker.rows if r.new_status is not None and r.new_status != r.status]
        for r in tracker.rows:
            if r.added:   # postings of an opening already in the sheet: identical copies, or job boards
                changes.append({"range": f"{_col(COL['Job ID'])}{r.number}", "values": [[ID_SEP.join(r.all_ids)]]})
                changes.append({"range": f"{_col(COL['Location'])}{r.number}", "values": [[_safe(r.location)]]})
                changes.append({"range": f"{_col(COL['Also seen on'])}{r.number}", "values": [[_safe(r.also_seen_on)]]})
        if changes:
            self.jobs.batch_update(changes, value_input_option="RAW")
        return sum(1 for r in tracker.rows if r.new_status is not None and r.new_status != r.status)

    def add_jobs(self, jobs) -> None:
        """Insert at the top so the newest jobs come first; existing rows (and your Status/Notes
        on them) move down together. Call write_statuses() first: inserting shifts row numbers."""
        if not jobs:
            return
        rows = [[
            today().isoformat(), _safe(j.company), _safe(j.title), _safe(j.location),
            j.posted.isoformat() if j.posted else "", j.experience, _safe(j.why_kept), j.source,
            _hyperlink(j.url), "Open", ", ".join(j.also_seen_on), "", "", ID_SEP.join(j.all_ids),
            fingerprint(j.description)]
            for j in jobs]
        self.jobs.insert_rows(rows, row=2, value_input_option="USER_ENTERED")

    def add_seen(self, jobs) -> None:
        rows = [[i, today().isoformat(), j.company, j.title] for j in jobs for i in j.all_ids]
        if rows:
            self.seen.append_rows(rows, value_input_option="RAW")

    def add_log(self, rows) -> None:
        if rows:
            self.log.append_rows(rows, value_input_option="USER_ENTERED")

    def previous_zero_sources(self) -> set:
        """Sources that fetched 0 jobs (or errored) the last time they were read, in any run group."""
        last = {}
        for r in self.log.get_values("A2:H"):
            if len(r) > 3:
                last[r[2]] = r    # the log is in run order: later rows win
        return {name for name, r in last.items() if r[3] in ("0", "") or (len(r) > 7 and r[7])}


def _fingerprints(jobs):
    """Description fingerprints for a list of Jobs (None -> ""), loading descriptions in parallel."""
    live = [j for j in jobs if j]
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda j: j.ensure_description(), live))
    return [fingerprint(j.description) if j else "" for j in jobs]


def _hyperlink(url: str) -> str:
    # A formula keeps the link intact: Sheets can't swap a plain URL for its page title.
    return f'=HYPERLINK("{url.replace(chr(34), "%22")}", "{APPLY_LABEL}")' if url else ""


def _safe(s: str) -> str:
    s = (s or "").strip()
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s

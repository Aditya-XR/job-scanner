"""Daily summary + alerts through Gmail SMTP (app password)."""
import html
import smtplib
from email.mime.text import MIMEText

from .core import env, today

E = html.escape


def send_summary(group: str, new_jobs, sheet_url: str, broken_sources,
                 closed_rows=(), id_problems=None, copies=0) -> bool:
    addr, pw = env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD").replace(" ", "")
    if not addr or not pw:
        return False
    to = env("ALERT_TO") or addr
    id_problems = id_problems or {}

    subject = f"Job Scanner: {len(new_jobs)} new jobs ({today():%d %b})"
    if broken_sources or id_problems:
        subject += f" · {len(set(broken_sources) | set(id_problems))} source(s) need attention"

    rows = "".join(
        f"<tr><td>{E(j.company)}</td><td><a href=\"{E(j.url)}\">{E(j.title)}</a>"
        f"{' <b>(reposted)</b>' if j.reposted else ''}</td>"
        f"<td>{E(j.location)}</td><td>{E(j.experience)}</td><td>{E(j.source)}</td></tr>"
        for j in new_jobs[:40])
    more = f"<p>…and {len(new_jobs) - 40} more in the sheet.</p>" if len(new_jobs) > 40 else ""
    table = (f"<table cellpadding='6' style='border-collapse:collapse' border='1'>"
             f"<tr><th>Company</th><th>Role</th><th>Location</th><th>Experience</th><th>Found on</th></tr>{rows}</table>{more}"
             if new_jobs else "<p>No new matching jobs this run.</p>")

    closed = ""
    # Only the ones you haven't acted on: an Applied/Skip job closing is not news.
    pending = [r for r in closed_rows if r.user_status.strip() in ("", "To apply")]
    if pending:
        items = "".join(f"<li>{E(r.company)}: {E(r.title)}</li>" for r in pending)
        closed = f"<p><b>Taken down before you applied</b> (marked Closed in the sheet):</p><ul>{items}</ul>"

    alert = ""
    if broken_sources:
        items = "".join(f"<li>{E(s)}</li>" for s in sorted(broken_sources))
        alert += (f"<p><b>Returned no jobs or failed on 2 runs in a row</b> (the site may have changed "
                  f"or blocked us):</p><ul>{items}</ul>")
    if id_problems:
        items = "".join(f"<li>{E(n)}: {fb} posting(s) without an ID, {cf} ID conflict(s)</li>"
                        for n, (fb, cf) in sorted(id_problems.items()))
        alert += (f"<p><b>Posting IDs looked wrong</b> (the feed may have changed; affected jobs were "
                  f"still added, possibly as duplicates):</p><ul>{items}</ul>")

    dup = (f"<p>{copies} more posting(s) were identical copies of jobs already in your sheet "
           f"(same opening posted again); they were added to those rows, not as new ones.</p>" if copies else "")
    body = (f"<p><a href=\"{sheet_url}\"><b>Open the Job Scanner sheet</b></a> · run: {group}</p>"
            f"{alert}{table}{dup}{closed}")
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, addr, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(addr, pw)
        s.sendmail(addr, [to], msg.as_string())
    return True

"""Daily summary + source alerts through Gmail SMTP (app password)."""
import html
import smtplib
from email.mime.text import MIMEText

from .core import env, today


def send_summary(group: str, new_jobs, sheet_url: str, broken_sources) -> bool:
    addr, pw = env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD").replace(" ", "")
    if not addr or not pw:
        return False
    to = env("ALERT_TO") or addr

    subject = f"Job Scanner: {len(new_jobs)} new jobs ({today():%d %b})"
    if broken_sources:
        subject += f" · {len(broken_sources)} source(s) need attention"

    rows = "".join(
        f"<tr><td>{html.escape(j.company)}</td><td><a href=\"{html.escape(j.url)}\">{html.escape(j.title)}</a></td>"
        f"<td>{html.escape(j.location)}</td><td>{html.escape(j.experience)}</td></tr>"
        for j in new_jobs[:40])
    more = f"<p>…and {len(new_jobs) - 40} more in the sheet.</p>" if len(new_jobs) > 40 else ""
    table = (f"<table cellpadding='6' style='border-collapse:collapse' border='1'>"
             f"<tr><th>Company</th><th>Role</th><th>Location</th><th>Experience</th></tr>{rows}</table>{more}"
             if new_jobs else "<p>No new matching jobs this run.</p>")
    alert = ""
    if broken_sources:
        items = "".join(f"<li>{html.escape(s)}</li>" for s in sorted(broken_sources))
        alert = (f"<p><b>Returned no jobs or failed on 2 runs in a row</b> (the site may have changed "
                 f"or blocked us):</p><ul>{items}</ul>")

    body = (f"<p><a href=\"{sheet_url}\"><b>Open the Job Scanner sheet</b></a> · run: {group}</p>"
            f"{alert}{table}")
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, addr, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(addr, pw)
        s.sendmail(addr, [to], msg.as_string())
    return True

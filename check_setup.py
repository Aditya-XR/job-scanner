"""Verify every credential in .env without ever printing a secret.

Run:  .venv\\Scripts\\python check_setup.py            (all checks)
      .venv\\Scripts\\python check_setup.py sheet      (one check: sheet | linkedin | gemini | gmail | optional)
"""
import json
import os
import smtplib
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env", override=True)


def env(name):
    return (os.getenv(name) or "").strip()


def ok(msg):
    print(f"  [OK]   {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")
    return False


def check_sheet():
    print("Google Sheet")
    key_path = ROOT / env("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not env("GOOGLE_SERVICE_ACCOUNT_FILE") or not key_path.is_file():
        return fail(f"key file not found at {key_path}")
    try:
        info = json.loads(key_path.read_text(encoding="utf-8"))
        sa_email = info["client_email"]
    except Exception as e:
        return fail(f"key file is not a valid service-account JSON ({type(e).__name__})")
    ok(f"key file found; service account = {sa_email}")
    if not env("SHEET_ID"):
        return fail("SHEET_ID is empty")
    import gspread

    try:
        sh = gspread.service_account(filename=str(key_path)).open_by_key(env("SHEET_ID"))
        ok(f"opened sheet '{sh.title}'")
    except gspread.exceptions.APIError as e:
        code = e.response.status_code
        if code == 403:
            return fail(f"403: share the sheet with {sa_email} as Editor, and enable the "
                        "Google Sheets API + Google Drive API in the Cloud project")
        if code == 404:
            return fail("404: SHEET_ID is wrong (copy it from the sheet URL)")
        return fail(f"Sheets API error {code}")
    try:
        ws = sh.sheet1
        ws.update_acell("Z1", "setup-check")
        ws.update_acell("Z1", "")
        ok("write access confirmed")
    except Exception:
        return fail(f"can read but not write: give {sa_email} Editor access")
    return True


def check_linkedin():
    print("LinkedIn burner")
    li_at = env("LINKEDIN_LI_AT")
    if not li_at:
        return fail("LINKEDIN_LI_AT is empty")
    if li_at.startswith('"') or " " in li_at:
        return fail("LINKEDIN_LI_AT has quotes or spaces; paste only the raw cookie value")
    r = requests.get(
        "https://www.linkedin.com/feed/",
        cookies={"li_at": li_at},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"},
        allow_redirects=False,
        timeout=20,
    )
    if r.status_code == 200:
        ok("cookie is valid (logged-in feed loaded)")
        return True
    loc = r.headers.get("location", "")
    if "checkpoint" in loc:
        return fail("LinkedIn wants a security check: log in to the burner in Chrome, clear it, then recopy li_at")
    return fail(f"cookie rejected (HTTP {r.status_code}); log in again and recopy li_at")


def check_gemini():
    print("Gemini")
    key = env("GEMINI_API_KEY")
    if not key:
        return fail("GEMINI_API_KEY is empty")
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                     headers={"x-goog-api-key": key}, timeout=20)
    if r.status_code == 200:
        flash = [m["name"] for m in r.json().get("models", []) if "flash" in m["name"]]
        ok(f"key valid; {len(flash)} Flash models available")
        return True
    return fail(f"key rejected (HTTP {r.status_code})")


def check_gmail():
    print("Gmail alerts")
    addr, pw = env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD").replace(" ", "")
    if not addr or not pw:
        return fail("GMAIL_ADDRESS or GMAIL_APP_PASSWORD is empty")
    if len(pw) != 16:
        return fail("app password should be 16 letters; your normal Gmail password won't work")
    to = env("ALERT_TO") or addr
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(addr, pw)
            s.sendmail(addr, [to], f"From: {addr}\r\nTo: {to}\r\nSubject: jobscan setup check\r\n\r\n"
                                   "If you can read this, Gmail alerts are working.\r\n")
    except smtplib.SMTPAuthenticationError:
        return fail("Gmail rejected the login: check the address and regenerate the app password")
    ok(f"logged in and sent a test email to {to}")
    return True


def check_optional():
    print("Optional logins")
    for site in ("NAUKRI", "INSTAHYRE"):
        e, p = env(f"{site}_EMAIL"), env(f"{site}_PASSWORD")
        if e and p:
            ok(f"{site.title()} credentials present (tested on first real run)")
        elif e or p:
            fail(f"{site.title()}: fill both EMAIL and PASSWORD, or leave both blank")
        else:
            print(f"  [SKIP] {site.title()} not set")
    return True


CHECKS = {"sheet": check_sheet, "linkedin": check_linkedin, "gemini": check_gemini,
          "gmail": check_gmail, "optional": check_optional}

if __name__ == "__main__":
    names = sys.argv[1:] or list(CHECKS)
    results = {}
    for n in names:
        try:
            results[n] = CHECKS[n]()
        except requests.RequestException as e:
            results[n] = fail(f"network error ({type(e).__name__})")
        print()
    bad = [n for n, r in results.items() if r is False]
    print("ALL GOOD" if not bad else f"Needs attention: {', '.join(bad)}")

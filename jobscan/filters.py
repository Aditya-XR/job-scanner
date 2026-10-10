"""The checks every job goes through: India/remote, software role, and experience."""
import json
import re
import time

import requests

from .core import env

# ---------- 2. India or remote-India ----------

INDIA_PLACES = (
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "navi mumbai", "thane",
    "gurgaon", "gurugram", "noida", "greater noida", "delhi", "new delhi", "chennai", "kolkata",
    "ahmedabad", "jaipur", "kochi", "cochin", "trivandrum", "thiruvananthapuram", "coimbatore",
    "indore", "chandigarh", "mohali", "bhubaneswar", "nagpur", "mysore", "mysuru", "vadodara",
    "lucknow", "visakhapatnam", "vizag", "mangalore", "gandhinagar", "goa", "ranchi", "patna",
)
_INDIA_RE = re.compile(r"\b(" + "|".join(map(re.escape, INDIA_PLACES)) + r")\b", re.I)
_IND_CODE = re.compile(r"\bIND\b")  # Amazon writes "Hyderabad, Telangana, IND"
_REMOTE_INDIA = re.compile(r"\bremote\b.*\b(apac|asia)\b|\b(apac|asia)\b.*\bremote\b", re.I)


def is_india(location: str) -> bool:
    loc = location or ""
    return bool(_INDIA_RE.search(loc) or _IND_CODE.search(loc) or _REMOTE_INDIA.search(loc))


# ---------- 3. Software role (title only) ----------

_SOFTWARE = re.compile(
    r"\b(software|sde|sdet|developer|programmer|full[ -]?stack|(front|back)[ -]?end (engineer|developer|dev)|"
    r"android|ios|mobile engineer|devops|sre|site reliability|machine learning|ml engineer|"
    r"ai engineer|data engineer|cloud engineer|platform engineer|infrastructure engineer|"
    r"qa automation|test automation|automation engineer|application engineer|web engineer|"
    r"member of technical staff|mts|(associate|graduate|trainee|junior) engineer|engineer trainee|"
    r"applied scientist|research engineer|security engineer|systems engineer|backend|frontend)\b",
    re.I)
_SENIOR = re.compile(
    r"\b(senior|sr|leads?|staff|principal|managers?|directors?|heads?|vp|vice president|architects?|"
    r"distinguished|fellow|ii|iii|iv|expert|specialist|leader|intermediate|experienced|mid[ -]level|"
    r"dir|mgr|(engineering|engrg|engg|development|software) (mgmt|management))\b|"
    r"\b(sde|engineer|developer|mts|scientist)[\s-]*([2-9])\b|\b\d{1,2}\s*\+?\s*(years?|yrs)\b",
    re.I)
_NOT_SOFTWARE = re.compile(
    r"\b(sales|marketing|mechanical|civil|hardware|asic|rtl|dft|analog|silicon|physical design|"
    r"layout|customer success|support engineer|technical support|recruit\w*|accountant|"
    r"account executive|legal|finance|hr|clerk|cashier|warehouse|driver|business development|"
    r"pre[ -]?sales|demo executive|content (developer|writer)|curriculum)\b", re.I)
ENTRY_TITLE = re.compile(r"\b(intern|internship|new grad|graduate|trainee|fresher|entry[ -]level|"
                         r"university|campus|apprentice|associate software engineer)\b", re.I)


def is_software(title: str) -> bool:
    return bool(_SOFTWARE.search(title) and not _SENIOR.search(title)
                and not _NOT_SOFTWARE.search(title))


# ---------- 4. Experience under 1 year ----------

# Experience label of a job kept although its description asks for years of experience
# (Gemini read it as open to freshers): "Stretch: 2+ yrs".
STRETCH = "Stretch: "

_WORD_NUM = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_WORDS_RE = re.compile(r"\b(" + "|".join(_WORD_NUM) + r")\b(?=\s*(\(\d+\)\s*)?\+?\s*(-|–|to|or more|plus|\+)?\s*(\d+\s*)?(years?|yrs?))", re.I)
_YEARS = re.compile(
    r"(?<![\d.])(\d{1,2})(?:\.\d+)?(?:\s*(?:\+|plus|or more|or above))*\s*"
    r"(?:(?:-|–|—|to)\s*(\d{1,2})(?:\.\d+)?\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
# Words that make "N years" a requirement rather than, say, company history. Seen in real JDs:
# "2 to 5 years of Python engineering expertise", "5-8 years in an Apps Development role".
_EXP_CONTEXT = re.compile(
    r"experience|exp\b|expertise|work(ed|ing)?\b|industry|professional|hands[- ]on|\brole\b|"
    r"development|programming|coding|engineering|software|relevant|related", re.I)
# "Bachelors + 2 years OR Masters + 0 years": only the bachelor's figure applies to a BTech.
_HIGHER_DEGREE = re.compile(r"(master'?s?|\bms\b|m\.?\s?tech|m\.?\s?s\.|\bme\b|ph\.?\s?d|doctora\w*)[^.;\n]{0,12}$", re.I)
_NOT_EXP = re.compile(r"years? (old|of age)|age\b|warranty|history|anniversary|founded|since", re.I)
# Company-history phrasing around a year count: "founded 15 years ago", "for over 20 years we have"
_HISTORY = re.compile(r"\bago\b|founded|since \d|history|anniversary|"
                      r"\b(we|our company|the company)\b[^.]{0,40}$", re.I)


def _same_sentence_before(text: str, start: int, span: int = 50) -> str:
    """Up to `span` characters before `start`, cut back to the start of that sentence/line."""
    before = text[max(0, start - span): start]
    cut = max(before.rfind(c) for c in ".\n;•")
    return before[cut + 1:]
FRESHER = re.compile(
    r"\b(freshers?|new[ -]?grad(uate)?s?|entry[ -]level|recent (college )?graduates?|"
    r"(20(25|26|27))\s*(batch|graduates?|pass[ -]?outs?|grads?)|no (prior )?(work )?experience (is )?"
    r"(required|needed|necessary)|campus hir\w*|early[ -]career)\b", re.I)
_SOFT = re.compile(r"prefer|nice[ -]to[ -]have|is a plus|good[ -]to[ -]have|bonus|desirable|ideally", re.I)


def _requirements(text: str):
    """Every 'N years' in text that reads as an experience requirement."""
    found = []
    for m in _YEARS.finditer(text):
        lo, hi = int(m.group(1)), m.group(2)
        window = text[max(0, m.start() - 90): m.end() + 90]
        if lo > 20 or _NOT_EXP.search(text[m.start(): m.end() + 25]) or not _EXP_CONTEXT.search(window):
            continue
        after = re.split(r"[.\n;•]", text[m.end(): m.end() + 15], maxsplit=1)[0]   # same sentence only
        if _HISTORY.search(after) or _HISTORY.search(_same_sentence_before(text, m.start())):
            continue
        if _HIGHER_DEGREE.search(text[max(0, m.start() - 30): m.start()]):
            continue
        found.append((lo, int(hi) if hi else None, bool(_SOFT.search(window))))
    return found


def experience_check(title: str, description: str):
    """Returns (decision, label, reason) where decision is 'keep', 'drop' or 'unclear'."""
    max_min = int(env("MAX_MIN_YEARS", "0") or 0)
    if ENTRY_TITLE.search(title):
        return "keep", "Entry title", "title says intern/graduate/trainee"
    numbers = lambda s: _WORDS_RE.sub(lambda m: _WORD_NUM[m.group(1).lower()], s or "")
    text = numbers(description)
    # Title and description are scanned separately so words in the title ("Software Engineer")
    # never count as context for a year figure in the description.
    found = _requirements(numbers(title)) + _requirements(text)  # (min_years, max_years_or_None, soft)
    fresher = FRESHER.search(text)

    if not found:
        if fresher:
            return "keep", "Fresher", f"says \"{fresher.group(0)}\""
        if not description:
            return "keep", "Not mentioned", "description not available"
        return "keep", "Not mentioned", "no experience requirement in description"

    lo, hi, soft = min(found, key=lambda f: f[0])
    label = f"{lo}-{hi} yrs" if hi is not None else f"{lo}+ yrs"
    if lo <= max_min:
        return "keep", label, f"minimum is {lo} years"
    if fresher or soft:
        return "unclear", label, ("mentions freshers" if fresher else "years marked as preferred")
    return "drop", label, f"needs {lo}+ years"


# ---------- Gemini for unclear cases ----------

_PROMPT = """You screen job descriptions for a candidate with LESS THAN 1 YEAR of professional
software experience: a BTech (bachelor's) in Computer Science, graduating 2026-2027, no master's or PhD.
Decide whether this candidate can realistically apply: keep it if the role accepts freshers,
new graduates, or 0 years of experience with a bachelor's degree, or if the stated years are only
"preferred". Drop it if a bachelor's holder needs 1 or more years, even when a master's/PhD needs fewer.

Job title: {title}
Job description:
{desc}

Reply as JSON: {{"keep": true or false, "reason": "at most 12 words"}}"""


class Gemini:
    def __init__(self, max_calls: int = 60):
        self.key = env("GEMINI_API_KEY")
        # Pinned rather than a "-latest" alias, which can silently move to a model with a
        # smaller free quota (gemini-3.8-flash allows only 20 requests/day; this one 500).
        self.model = env("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.calls_left = max_calls
        self._last = 0.0

    def available(self) -> bool:
        return bool(self.key) and self.calls_left > 0

    def classify(self, title: str, description: str):
        """Returns (keep, reason) or None if the call failed."""
        self.calls_left -= 1
        wait = 4.5 - (time.monotonic() - self._last)  # stay under the free-tier rate limit
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        body = {
            "contents": [{"parts": [{"text": _PROMPT.format(title=title, desc=description[:7000])}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        for attempt in range(2):
            try:
                r = requests.post(url, json=body, headers={"x-goog-api-key": self.key}, timeout=30)
                if r.status_code in (429, 503):
                    if attempt == 0:
                        time.sleep(30)    # per-minute limit or a brief overload
                        continue
                    self.calls_left = 0   # daily quota gone: stop asking for this run
                    return None
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                out = json.loads(text)
                if isinstance(out, list):  # the model sometimes wraps the object in a list
                    out = out[0] if out and isinstance(out[0], dict) else {}
                return bool(out.get("keep")), str(out.get("reason", ""))[:120]
            except (requests.RequestException, KeyError, IndexError, ValueError):
                time.sleep(3)
        return None

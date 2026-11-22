"""Matching rules: which titles, places and experience levels count.

Everything here is pure functions so it can be tested without the internet.
"""
import html
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(raw):
    """Turn job description HTML (sometimes HTML-escaped twice) into plain text."""
    if not raw:
        return ""
    text = html.unescape(html.unescape(raw))
    text = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _phrase_regex(phrase):
    """Word-boundary regex for a phrase like 'sr.' or ' vp' or 'analyst i'."""
    p = phrase.strip().lower()
    body = re.escape(p)
    start = r"\b" if p[:1].isalnum() else ""
    end = r"\b" if p[-1:].isalnum() else ""
    return re.compile(start + body + end)


class RoleMatcher:
    def __init__(self, roles_cfg):
        self.families = []
        for fam in roles_cfg["families"]:
            pats = [_phrase_regex(t) for t in fam["titles"]]
            self.families.append((fam["id"], pats))
        self.exclude = [_phrase_regex(t) for t in roles_cfg.get("exclude_titles", [])]

    def is_excluded(self, title):
        t = " " + title.lower() + " "
        return any(p.search(t) for p in self.exclude)

    def family(self, title):
        """Return the family id for a title, or None. First family listed wins."""
        t = title.lower()
        if self.is_excluded(title):
            return None
        for fid, pats in self.families:
            if any(p.search(t) for p in pats):
                return fid
        return None


class LocationMatcher:
    def __init__(self, loc_cfg):
        self.regions = [(r["id"], [p.lower() for p in r["places"]]) for r in loc_cfg["regions"]]
        self.non_us = [m.lower() for m in loc_cfg.get("non_us_markers", [])]

    def region(self, location, remote_flag=False):
        """Return the first matching region id, or None if the job is out of area."""
        loc = " " + (location or "").lower() + " "
        for rid, places in self.regions:
            if rid == "remote":
                is_remote = remote_flag or any(p in loc for p in places)
                if not is_remote:
                    continue
                us = any(k in loc for k in ("united states", " us", "usa", "u.s."))
                foreign = any(m in loc for m in self.non_us)
                if foreign and not us:
                    continue
                return rid
            if any(p in loc for p in places):
                return rid
        return None


# "3+ years", "3-5 years", "2 to 4 years", "minimum of 3 years", "three years"
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YEARS = re.compile(
    r"(?P<a>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(?P<b>\d{1,2})\s*\+?)?"
    r"\s*(?:\(\d+\)\s*)?years?",
    re.I,
)


def min_years(text):
    """Smallest years-of-experience number mentioned near 'experience'.

    Returns None when the description never asks for a number of years.
    Ignores numbers that are clearly not experience (e.g. '401k', company age).
    """
    if not text:
        return None
    found = []
    for m in _YEARS.finditer(text):
        window = text[m.end(): m.end() + 80].lower() + text[max(0, m.start() - 40): m.start()].lower()
        if "experience" not in window and "exp." not in window:
            continue
        a = m.group("a").lower()
        n = _WORDNUM.get(a, None) if not a.isdigit() else int(a)
        if n is not None and 0 <= n <= 15:
            found.append(n)
    return min(found) if found else None


_PAY = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3}|\.\d+)?\s?[kK]?"
    r"(?:\s*(?:-|–|to)\s*\$?\s?\d{2,3}(?:,\d{3}|\.\d+)?\s?[kK]?)?"
    r"(?:\s*(?:per|/|an?)\s*(?:year|yr|hour|hr|annually))?"
)


def extract_pay(text):
    """First salary-looking range in the text, like '$55,000 - $65,000'. None if absent."""
    if not text:
        return None
    for m in _PAY.finditer(text):
        s = m.group(0).strip()
        low = s.lower()
        first = re.split(r"-|–|\bto\b", s)[0]
        num = re.sub(r"[^\d.]", "", first.replace(",", ""))
        try:
            val = float(num)
        except ValueError:
            continue
        hourly = "hour" in low or "hr" in low
        if "k" in first.lower() and 20 <= val <= 400:
            return s
        if val >= 20000:
            return s
        if hourly and 10 <= val <= 150:
            return s
    return None


_TOOLS = [
    ("SQL", re.compile(r"\b(?:t-sql|pl/sql|my\s?sql|postgre\s?sql|sql)\b", re.I)),
    ("Python", re.compile(r"\bpython\b", re.I)),
    ("Tableau", re.compile(r"\btableau\b", re.I)),
    ("Power BI", re.compile(r"\bpower\s?bi\b", re.I)),
    ("Salesforce", re.compile(r"\bsalesforce\b", re.I)),
]


def tools_mentioned(text):
    """Which common tools a posting asks about, e.g. ['SQL', 'Tableau']."""
    if not text:
        return []
    return [name for name, rx in _TOOLS if rx.search(text)]


# ---------------------------------------------------------------- entry level signals
_ENTRY_TEXT = re.compile(
    r"\b(entry[\s-]level|new grad(?:uate)?s?|recent (?:college )?grad(?:uate)?s?|early[\s-]career|"
    r"graduating (?:seniors?|students?)|class of 202\d|no (?:prior )?experience (?:required|necessary|needed)|"
    r"0\s*(?:-|–|to)\s*[12]\s*years?|will train|paid training|training program)\b",
    re.I,
)
_ENTRY_TITLE = re.compile(
    r"\b(entry[\s-]level|junior|jr\.?|associate|new grad|graduate|trainee|apprentice|"
    r"analyst i|specialist i|coordinator i|level i|tier 1|early career)\b",
    re.I,
)
# Generic office titles worth keeping when the posting says it's entry level.
_OFFICE_TITLE = re.compile(
    r"\b(analyst|coordinator|specialist|associate|administrator|consultant|assistant|"
    r"operations|support|program|planner|technician|representative|advisor|"
    r"generalist|processor|scheduler|dispatcher|buyer|auditor|examiner)\b",
    re.I,
)


def entry_signal(title, text):
    """True when the title or description says this is meant for new or early career people."""
    return bool(_ENTRY_TITLE.search(title or "") or _ENTRY_TEXT.search(text or ""))


def office_title(title):
    return bool(_OFFICE_TITLE.search(title or ""))


CORE_FAMILIES = {"revops", "systems", "implementation", "analyst"}


def fit_score(job, today_ord):
    """0 to 100: how likely this is a real entry level job worth Micah's time."""
    s = 50
    if job.get("entry"):
        s += 20
    y = job.get("min_years")
    s += {None: 5, 0: 15, 1: 10, 2: 0}.get(y, -40)
    if job.get("family") in CORE_FAMILIES:
        s += 10
    elif job.get("family") == "other":
        s -= 5
    if "SQL" in (job.get("tools") or []):
        s -= 10
    try:
        age = today_ord - __import__("datetime").date.fromisoformat(job["first_seen"]).toordinal()
        s += 10 if age <= 3 else 5 if age <= 7 else 0
    except (KeyError, ValueError):
        pass
    if job.get("pay"):
        s += 3
    return max(0, min(100, s))

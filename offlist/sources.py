"""Fetchers for the applicant tracking systems (ATS) behind company careers pages.

Each fetcher returns a list of jobs in one shape:
    {title, location, remote, url, posted, description, pay, ats}
`description` is plain text (used only for filtering, never published).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .filters import html_to_text

UA = "offlist/1.0 (+https://github.com/mmosle28/offlist)"
TIMEOUT = 25


class FetchError(Exception):
    pass


def _get(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise FetchError(type(e).__name__ + ": " + str(e)[:80]) from None


def _iso(value):
    """Accept ms epoch, ISO strings, or None; return YYYY-MM-DD or None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        return str(value)[:10]
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------- Greenhouse
def parse_greenhouse(payload):
    out = []
    for j in payload.get("jobs", []):
        desc = html_to_text(j.get("content", ""))
        loc = (j.get("location") or {}).get("name", "")
        out.append({
            "title": j.get("title", "").strip(),
            "location": loc,
            "remote": "remote" in loc.lower(),
            "url": j.get("absolute_url"),
            "posted": _iso(j.get("first_published") or j.get("updated_at")),
            "description": desc,
            "pay": None,
            "ats": "greenhouse",
        })
    return out


def fetch_greenhouse(slug):
    return parse_greenhouse(_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"))


# ---------------------------------------------------------------- Lever
def parse_lever(payload):
    out = []
    for j in payload if isinstance(payload, list) else []:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists") or []:
            parts.append(lst.get("text", "") + " " + html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        sal = j.get("salaryRange") or {}
        pay = None
        if sal.get("min") and sal.get("max"):
            pay = f"${int(sal['min']):,} - ${int(sal['max']):,}"
        out.append({
            "title": j.get("text", "").strip(),
            "location": loc,
            "remote": (j.get("workplaceType") == "remote") or "remote" in loc.lower(),
            "url": j.get("hostedUrl"),
            "posted": _iso(j.get("createdAt")),
            "description": " ".join(p for p in parts if p),
            "pay": pay,
            "ats": "lever",
        })
    return out


def fetch_lever(slug):
    return parse_lever(_get(f"https://api.lever.co/v0/postings/{slug}?mode=json"))


# ---------------------------------------------------------------- Ashby
def parse_ashby(payload):
    out = []
    for j in payload.get("jobs", []):
        locs = [j.get("location") or ""] + [
            (s.get("location") or "") for s in (j.get("secondaryLocations") or [])
        ]
        loc = "; ".join(l for l in locs if l)
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append({
            "title": j.get("title", "").strip(),
            "location": loc,
            "remote": bool(j.get("isRemote")) or "remote" in loc.lower(),
            "url": j.get("jobUrl"),
            "posted": _iso(j.get("publishedAt")),
            "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
            "pay": comp,
            "ats": "ashby",
        })
    return out


def fetch_ashby(slug):
    return parse_ashby(_get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"))


# ---------------------------------------------------------------- SmartRecruiters
def parse_smartrecruiters(payload, slug):
    out = []
    for j in payload.get("content", []):
        l = j.get("location") or {}
        loc = ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
        out.append({
            "title": j.get("name", "").strip(),
            "location": loc + (" (Remote)" if l.get("remote") else ""),
            "remote": bool(l.get("remote")),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
            "posted": _iso(j.get("releasedDate")),
            "description": "",  # filled in later only for matching titles
            "pay": None,
            "ats": "smartrecruiters",
            "_detail": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j.get('id')}",
        })
    return out


def fetch_smartrecruiters(slug):
    jobs, offset = [], 0
    while offset < 1000:
        page = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        jobs += parse_smartrecruiters(page, slug)
        if offset + 100 >= page.get("totalFound", 0):
            break
        offset += 100
    return jobs


def smartrecruiters_detail(job):
    d = _get(job["_detail"])
    secs = ((d.get("jobAd") or {}).get("sections") or {})
    text = " ".join(html_to_text((secs.get(k) or {}).get("text", ""))
                    for k in ("jobDescription", "qualifications", "additionalInformation"))
    return text


# ---------------------------------------------------------------- Workday
_WD = re.compile(r"https?://(?P<tenant>[\w-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[\w-]+)")


def parse_workday_url(url):
    m = _WD.match(url or "")
    if not m:
        return None
    return m.group("tenant"), m.group("wd"), m.group("site")


def fetch_workday(url, search_terms, max_pages=2):
    parsed = parse_workday_url(url)
    if not parsed:
        raise FetchError("not a Workday link")
    tenant, wd, site = parsed
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for term in search_terms:
        for page in range(max_pages):
            data = _get(api + "/jobs", data={"appliedFacets": {}, "limit": 20,
                                              "offset": page * 20, "searchText": term})
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                out.append({
                    "title": (p.get("title") or "").strip(),
                    "location": p.get("locationsText") or "",
                    "remote": "remote" in (p.get("locationsText") or "").lower(),
                    "url": f"{host}/{site}{path}",
                    "posted": None,
                    "posted_text": p.get("postedOn"),
                    "description": "",
                    "pay": None,
                    "ats": "workday",
                    "_detail": api + path,
                })
            if len(posts) < 20:
                break
    return out


def workday_detail(job):
    d = _get(job["_detail"])
    info = d.get("jobPostingInfo") or {}
    return html_to_text(info.get("jobDescription", "")), _iso(info.get("startDate"))


# ---------------------------------------------------------------- detection
def detect_from_url(url):
    """Figure out the ATS and board name from a pasted careers link."""
    u = (url or "").strip()
    if parse_workday_url(u):
        return "workday", u
    pats = [
        ("greenhouse", r"greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
        ("lever", r"jobs\.(?:eu\.)?lever\.co/([\w-]+)"),
        ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
        ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ]
    for ats, p in pats:
        m = re.search(p, u)
        if m and m.group(1) not in ("v1", "boards"):
            return ats, m.group(1)
    return None, None


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
}
GUESS_ORDER = ["greenhouse", "lever", "ashby", "smartrecruiters"]


# ---------------------------------------------------------------- discovery feeds
# These search across thousands of employers at once. Jobs found here are shown
# like any other, and their companies are added to the daily watch list.

MUSE_LOCATIONS = ["Atlanta, GA", "Flexible / Remote"]


def parse_muse(payload):
    out = []
    for j in payload.get("results", []):
        locs = "; ".join(l.get("name", "") for l in j.get("locations") or [])
        levels = [l.get("name", "") for l in j.get("levels") or []]
        out.append({
            "title": (j.get("name") or "").strip(),
            "company": ((j.get("company") or {}).get("name") or "").strip(),
            "location": locs,
            "remote": "remote" in locs.lower() or "flexible" in locs.lower(),
            "url": (j.get("refs") or {}).get("landing_page"),
            "posted": _iso(j.get("publication_date")),
            "description": html_to_text(j.get("contents", "")),
            "pay": None,
            "ats": "themuse",
            "entry_hint": any("entry" in l.lower() for l in levels),
        })
    return out


MUSE_ENDPOINTS = ["https://www.themuse.com/api/public/jobs",
                  "https://www.themuse.com/api/public/v2/jobs"]


def fetch_muse(max_pages=15, api_key=None):
    """Entry level jobs in Atlanta or remote. Tries the known endpoint and page styles in turn."""
    headers = {"X-Muse-Api-Key": api_key} if api_key else None
    attempts = [(url, start, locs) for url in MUSE_ENDPOINTS for start in (1, 0)
                for locs in (MUSE_LOCATIONS, MUSE_LOCATIONS[:1])]
    last = None
    for url, start, locs in attempts:
        out = []
        try:
            for page in range(start, start + max_pages):
                params = [("page", page), ("level", "Entry Level")] + [("location", l) for l in locs]
                if api_key:
                    params.append(("api_key", api_key))
                data = _get(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
                out += parse_muse(data)
                if page + 1 - start >= data.get("page_count", 0):
                    break
            return out
        except FetchError as e:
            if out:
                return out
            last = e
    raise last


ADZUNA_QUERIES = [
    {"what": "entry level", "where": "Atlanta, GA"},
    {"what": "new grad", "where": "Atlanta, GA"},
    {"what_or": "analyst coordinator specialist associate", "what": "junior", "where": "Atlanta, GA"},
    {"what": "implementation specialist", "where": "Atlanta, GA"},
]


def parse_adzuna(payload):
    out = []
    for j in payload.get("results", []):
        lo, hi = j.get("salary_min"), j.get("salary_max")
        pay = None
        if lo and hi and not j.get("salary_is_predicted") in ("1", 1, True):
            pay = f"${int(lo):,} - ${int(hi):,}" if lo != hi else f"${int(lo):,}"
        loc = (j.get("location") or {}).get("display_name", "")
        out.append({
            "title": html_to_text(j.get("title", "")),
            "company": ((j.get("company") or {}).get("display_name") or "").strip(),
            "location": loc,
            "remote": "remote" in (j.get("title", "") + loc).lower(),
            "url": j.get("redirect_url"),
            "posted": _iso(j.get("created")),
            "description": html_to_text(j.get("description", "")),
            "pay": pay,
            "ats": "adzuna",
            "entry_hint": False,
        })
    return out


def fetch_adzuna(app_id, app_key, max_calls=4):
    out = []
    for q in ADZUNA_QUERIES[:max_calls]:
        params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50,
                  "max_days_old": 21, "distance": 40, "content-type": "application/json"}
        params.update(q)
        out += parse_adzuna(_get("https://api.adzuna.com/v1/api/jobs/us/search/1?" +
                                 urllib.parse.urlencode(params)))
    return out


def slug_guesses(company):
    """Likely board names for a company, e.g. 'Flock Safety' -> ['flocksafety', 'flock-safety', 'flock']."""
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|group|holdings|the)\b\.?", "",
                  company.lower())
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    cands = ["".join(words), "-".join(words), words[0]]
    seen, out = set(), []
    for c in cands:
        if len(c) >= 3 and c not in seen:
            seen.add(c)
            out.append(c)
    return out

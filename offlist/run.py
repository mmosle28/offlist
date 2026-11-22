"""Daily run: fetch every company, keep matching jobs, write the site's data files.

    python -m offlist.run            # live run (needs internet)
    python -m offlist.run --dry      # use tests/fixtures instead of the internet
"""
import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import sources
from .filters import (LocationMatcher, RoleMatcher, entry_signal, extract_pay, fit_score,
                      min_years, office_title, tools_mentioned)

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"
DATA = ROOT / "docs" / "data"
DETAIL_CAP = 60          # most job pages opened per company per run (Workday / SmartRecruiters)
FORGET_AFTER_DAYS = 45   # drop "first seen" memory for jobs gone this long
DISCOVER_PROBES = 25     # new or retry companies probed per run
RETRY_AFTER_DAYS = 14    # retry a discovered company whose board wasn't found
FEED_ATS = {"themuse", "adzuna"}
OTHER_FAMILY = {"id": "other", "name": "Other entry level",
                "what": "Jobs with less common titles where the posting itself says it is meant for "
                        "new grads or entry level candidates.",
                "why": "Unusual titles are often the least crowded openings, because people don't "
                       "think to search for them.",
                "titles": []}


def load_yaml(name):
    return yaml.safe_load((CFG / name).read_text())


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def job_id(url):
    return hashlib.sha1((url or "").encode()).hexdigest()[:12]


def resolve(entry, cache):
    """Return (ats, key) for a company entry, or (None, reason)."""
    if entry.get("manual"):
        return "manual", entry["manual"]
    for ats in sources.FETCHERS:
        if entry.get(ats):
            return ats, entry[ats]
    if entry.get("url"):
        ats, key = sources.detect_from_url(entry["url"])
        return (ats, key) if ats else (None, "unrecognized careers link")
    if entry.get("guess"):
        hit = cache.get(entry["name"])
        if hit:
            return hit["ats"], hit["key"]
        return "guess", entry["guess"]
    return None, "no url, guess, or board name"


def fetch_company(entry, cache, search_terms):
    """Fetch raw jobs for one company. Returns (status dict, jobs list)."""
    name = entry["name"]
    ats, key = resolve(entry, cache)
    status = {"name": name, "ats": ats, "key": key, "ok": False, "fetched": 0, "error": None,
              "discovered": bool(entry.get("discovered"))}
    if ats is None:
        status["error"] = key
        return status, []
    if ats == "manual":
        status.update(manual=True, url=key)
        return status, []
    try:
        if ats == "guess":
            last = "not found on Greenhouse, Lever, Ashby or SmartRecruiters"
            slugs = key if isinstance(key, list) else [key]
            jobs, found = [], False
            for slug in slugs:
                for cand in sources.GUESS_ORDER:
                    try:
                        jobs = sources.FETCHERS[cand](slug)
                    except sources.FetchError:
                        continue
                    if jobs:
                        cache[name] = {"ats": cand, "key": slug}
                        status.update(ats=cand, key=slug)
                        found = True
                        break
                if found:
                    break
            if not found:
                status["error"] = last + " (tried: " + ", ".join(slugs) + ")"
                return status, []
        elif ats == "workday":
            jobs = sources.fetch_workday(key, search_terms)
        else:
            jobs = sources.FETCHERS[ats](key)
    except sources.FetchError as e:
        status["error"] = str(e)
        return status, []
    status.update(ok=True, fetched=len(jobs))
    for j in jobs:
        j["company"] = name
    return status, jobs


def enrich(job):
    """Open the job's own page when the list didn't include the description."""
    if not job.get("_detail"):
        return job
    try:
        if job["ats"] == "workday":
            job["description"], start = sources.workday_detail(job)
            job["posted"] = job.get("posted") or start
        elif job["ats"] == "smartrecruiters":
            job["description"] = sources.smartrecruiters_detail(job)
    except sources.FetchError:
        pass
    return job


def select(jobs, roles, places):
    """Keep jobs whose title is a target role and whose location is in an allowed region."""
    kept = []
    for j in jobs:
        title, desc = j["title"], j.get("description", "")
        entry = bool(j.get("entry_hint")) or entry_signal(title, desc)
        fam = roles.family(title)
        if not fam:
            strong = bool(j.get("entry_hint")) or entry_signal(title, desc, strong_only=True)
            if strong and office_title(title) and not roles.is_excluded(title):
                fam = "other"
            else:
                continue
        reg = places.region(j["location"], j.get("remote"))
        if not reg:
            continue
        j["family"], j["region"], j["entry"] = fam, reg, entry
        kept.append(j)
    return kept


def finalize(j, seen, today):
    jid = job_id(j["url"])
    first = seen.get(jid, {}).get("first_seen", today)
    seen[jid] = {"first_seen": first, "last_seen": today}
    desc = j.get("description", "")
    rec = {
        "id": jid,
        "title": j["title"],
        "company": j["company"],
        "family": j["family"],
        "region": j["region"],
        "location": j["location"][:120],
        "remote": bool(j.get("remote")),
        "url": j["url"],
        "ats": j["ats"],
        "posted": j.get("posted"),
        "posted_text": j.get("posted_text"),
        "first_seen": first,
        "min_years": min_years(desc),
        "pay": j.get("pay") or extract_pay(desc),
        "tools": tools_mentioned(desc),
        "entry": bool(j.get("entry")) or entry_signal(j["title"], desc),
    }
    rec["score"] = fit_score(rec, date.fromisoformat(today).toordinal())
    return rec


def fetch_feeds():
    """Search feeds that span thousands of employers. Returns (statuses, jobs)."""
    statuses, jobs = [], []
    try:
        got = sources.fetch_muse()
        statuses.append({"name": "The Muse (search)", "ats": "themuse", "key": None, "ok": True,
                         "fetched": len(got), "error": None, "feed": True})
        jobs += got
    except sources.FetchError as e:
        statuses.append({"name": "The Muse (search)", "ats": "themuse", "key": None, "ok": False,
                         "fetched": 0, "error": str(e), "feed": True})
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if app_id and app_key:
        try:
            got = sources.fetch_adzuna(app_id, app_key)
            statuses.append({"name": "Adzuna (search)", "ats": "adzuna", "key": None, "ok": True,
                             "fetched": len(got), "error": None, "feed": True})
            jobs += got
        except sources.FetchError as e:
            statuses.append({"name": "Adzuna (search)", "ats": "adzuna", "key": None, "ok": False,
                             "fetched": 0, "error": str(e), "feed": True})
    return statuses, jobs


def discovered_entries(disc, known, today):
    """Company entries to probe this run from the auto-discovered list."""
    t = date.fromisoformat(today).toordinal()
    found, retry = [], []
    for name, d in disc.items():
        if name.lower() in known:
            continue
        if d.get("found"):
            found.append({"name": name, "guess": d["slugs"], "discovered": True})
        elif t - date.fromisoformat(d.get("last_try", "2000-01-01")).toordinal() >= RETRY_AFTER_DAYS:
            retry.append({"name": name, "guess": d["slugs"], "discovered": True})
    return found + retry[:DISCOVER_PROBES]


def run(fetch=None, today=None, companies=None, out_dir=DATA, feeds=None):
    roles_cfg = load_yaml("roles.yaml")
    roles = RoleMatcher(roles_cfg)
    places = LocationMatcher(load_yaml("locations.yaml"))
    companies = companies or load_yaml("companies.yaml")["companies"]
    cache = load_json(out_dir / "ats_cache.json", {})
    seen = load_json(out_dir / "seen.json", {})
    today = today or date.today().isoformat()
    search_terms = sorted({t for f in roles_cfg["families"] for t in f.get("search", [])})
    live = fetch is None
    fetch = fetch or (lambda e: fetch_company(e, cache, search_terms))
    disc = load_json(out_dir / "discovered.json", {})
    known = {c["name"].lower() for c in companies}
    if live:
        companies = companies + discovered_entries(disc, known, today)
    if feeds is None:
        feeds = fetch_feeds if live else (lambda: ([], []))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, companies))
    feed_status, feed_jobs = feeds()
    results += [(st, []) for st in feed_status]
    results.append(({"name": "_feeds", "ok": True}, feed_jobs))

    statuses, kept = [], []
    for status, jobs in results:
        matched = select(jobs, roles, places)
        needs = [j for j in matched if j.get("_detail")][:DETAIL_CAP]
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(enrich, needs))
        if status["name"] == "_feeds":
            for st in statuses:
                if st.get("feed"):
                    st["matched"] = sum(1 for j in matched if j["ats"] == st["ats"])
        else:
            status["matched"] = len(matched)
            statuses.append(status)
        kept += matched

    # remember companies the feeds found, so their own careers sites get watched
    for j in kept:
        name = j.get("company", "").strip()
        if j["ats"] in FEED_ATS and name and name.lower() not in known and name not in disc:
            slugs = sources.slug_guesses(name)
            if slugs:
                disc[name] = {"slugs": slugs, "first": today, "last_try": "2000-01-01", "found": False}
    for st in statuses:
        if st["name"] in disc:
            disc[st["name"]]["last_try"] = today
            disc[st["name"]]["found"] = bool(st["ok"])

    # one posting can show up twice: same link, or the same job on a feed and the company site
    kept.sort(key=lambda j: j["ats"] in FEED_ATS)  # company sites first
    out, urls, pairs = [], set(), set()
    for j in kept:
        pair = (j.get("company", "").lower(), j["title"].lower())
        if j["url"] in urls or pair in pairs or not j.get("url"):
            continue
        urls.add(j["url"])
        pairs.add(pair)
        out.append(finalize(j, seen, today))
    out.sort(key=lambda r: (r["score"], r["first_seen"]), reverse=True)

    # forget jobs that have been gone a long time
    cutoff = date.fromisoformat(today).toordinal() - FORGET_AFTER_DAYS
    seen = {k: v for k, v in seen.items() if date.fromisoformat(v["last_seen"]).toordinal() >= cutoff}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    (out_dir / "jobs.json").write_text(json.dumps({"updated": stamp, "jobs": out}, indent=1))
    (out_dir / "status.json").write_text(json.dumps({"updated": stamp, "companies": sorted(
        statuses, key=lambda s: (s["ok"], bool(s.get("manual")), s["name"]))}, indent=1))
    (out_dir / "discovered.json").write_text(json.dumps(disc, indent=1, sort_keys=True))
    (out_dir / "roles.json").write_text(json.dumps({
        "families": [{k: f[k] for k in ("id", "name", "what", "why", "titles")}
                     for f in roles_cfg["families"]] + [OTHER_FAMILY],
        "max_years_ok": roles_cfg.get("max_years_ok", 2)}, indent=1))
    (out_dir / "seen.json").write_text(json.dumps(seen, indent=0, sort_keys=True))
    (out_dir / "ats_cache.json").write_text(json.dumps(cache, indent=1, sort_keys=True))

    ok = sum(1 for s in statuses if s["ok"])
    print(f"{len(out)} jobs kept from {ok}/{len(statuses)} sources; "
          f"{sum(1 for d in disc.values() if d.get('found'))} discovered companies watched")
    for s in statuses:
        if not s["ok"] and not s.get("manual"):
            print(f"  ! {s['name']}: {s['error']}")
    return out, statuses


def _fixture_fetch(entry):
    """Offline stand-in used by --dry and tests."""
    fx = ROOT / "tests" / "fixtures"
    name = entry["name"]
    m = {"Fixture GH": ("greenhouse.json", sources.parse_greenhouse),
         "Fixture Lever": ("lever.json", sources.parse_lever),
         "Fixture Ashby": ("ashby.json", sources.parse_ashby)}
    if name not in m:
        return {"name": name, "ats": None, "key": None, "ok": False, "fetched": 0,
                "error": "offline"}, []
    fname, parser = m[name]
    jobs = parser(json.loads((fx / fname).read_text()))
    for j in jobs:
        j["company"] = name
    return {"name": name, "ats": jobs[0]["ats"] if jobs else None, "key": "fixture",
            "ok": True, "fetched": len(jobs), "error": None}, jobs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="offline run using test fixtures")
    args = ap.parse_args()
    if args.dry:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="offlist-dry-"))
        fixture_cos = [{"name": n} for n in ("Fixture GH", "Fixture Lever", "Fixture Ashby")]
        run(fetch=_fixture_fetch, companies=fixture_cos, out_dir=tmp)
        print(f"Dry run: fixtures only, nothing fetched. Output in {tmp} (site data untouched).")
    else:
        run()
    sys.exit(0)

"""Offline tests. Run with: python -m unittest -v"""
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from offlist import run as runner
from offlist import sources
from offlist.filters import office_title, LocationMatcher, RoleMatcher, extract_pay, html_to_text, min_years, tools_mentioned

CFG = Path(__file__).resolve().parent.parent / "config"
ROLES = RoleMatcher(yaml.safe_load((CFG / "roles.yaml").read_text()))
PLACES = LocationMatcher(yaml.safe_load((CFG / "locations.yaml").read_text()))


class TestTitles(unittest.TestCase):
    def test_hidden_roles_match(self):
        cases = {
            "Sales Operations Analyst": "revops",
            "Revenue Operations Associate": "revops",
            "Salesforce Administrator": "systems",
            "Business Systems Analyst": "systems",
            "Implementation Specialist, Restaurant Systems": "implementation",
            "Customer Success Associate": "success",
            "Technical Support Representative": "success",
            "Operations Analyst": "analyst",
            "Management Trainee": "programs",
            "Leadership Development Program Associate": "programs",
        }
        for title, fam in cases.items():
            self.assertEqual(ROLES.family(title), fam, title)

    def test_senior_and_unrelated_dropped(self):
        for title in ["Senior Sales Operations Analyst", "Sr. Business Analyst",
                      "Sales Operations Manager", "Software Engineer", "Data Analyst II",
                      "Implementation Lead", "Customer Success Intern", "Marketing Manager",
                      "Product Owner, Agile - Sales Enablement / Salesforce",
                      "Barista", "Outside Sales Representative Trainee - Atlanta, GA",
                      "Sales Rotational Development Associate", "Account Executive",
                      "Business Development Representative", "Inside Sales Specialist"]:
            self.assertIsNone(ROLES.family(title), title)

    def test_sales_ops_survives_sales_filter(self):
        self.assertEqual(ROLES.family("Sales Operations Analyst"), "revops")
        self.assertEqual(ROLES.family("Sales Support Specialist"), "revops")
        self.assertEqual(ROLES.family("Sales Enablement Coordinator"), "revops")

    def test_leadership_is_not_lead(self):
        self.assertEqual(ROLES.family("Leadership Development Program"), "programs")


class TestPlaces(unittest.TestCase):
    def test_regions(self):
        self.assertEqual(PLACES.region("Kennesaw, GA"), "atl")
        self.assertEqual(PLACES.region("Alpharetta, Georgia"), "atl")
        self.assertEqual(PLACES.region("Augusta, GA"), "aug")
        self.assertEqual(PLACES.region("Savannah, GA"), "ga")
        self.assertEqual(PLACES.region("Remote - United States"), "remote")
        self.assertEqual(PLACES.region("Anywhere", remote_flag=True), "remote")

    def test_out_of_area(self):
        self.assertIsNone(PLACES.region("Chicago, IL"))
        self.assertIsNone(PLACES.region("Remote - Canada", remote_flag=True))
        self.assertIsNone(PLACES.region("London, United Kingdom"))


class TestText(unittest.TestCase):
    def test_years(self):
        self.assertEqual(min_years("Requires 3+ years of experience in SaaS"), 3)
        self.assertEqual(min_years("1-3 years of relevant experience"), 1)
        self.assertEqual(min_years("Two years of experience preferred"), 2)
        self.assertIsNone(min_years("Founded 25 years ago. Great team."))
        self.assertIsNone(min_years(""))

    def test_pay(self):
        self.assertEqual(extract_pay("Pay: $60,000 - $72,000 per year."), "$60,000 - $72,000 per year")
        self.assertEqual(extract_pay("Starting at $55K a year"), "$55K a year")
        self.assertIsNone(extract_pay("Get a $5 coffee card"))
        self.assertEqual(extract_pay("$22.50 per hour"), "$22.50 per hour")

    def test_tools(self):
        self.assertEqual(tools_mentioned("Advanced SQL and Tableau; Excel"), ["SQL", "Tableau"])
        self.assertEqual(tools_mentioned("Experience with PostgreSQL and Power BI"), ["SQL", "Power BI"])
        self.assertEqual(tools_mentioned("Strong Excel skills"), [])

    def test_html(self):
        self.assertEqual(html_to_text("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;"), "Hi & bye")


class TestLinks(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(sources.detect_from_url("https://job-boards.greenhouse.io/salesloft"), ("greenhouse", "salesloft"))
        self.assertEqual(sources.detect_from_url("https://boards.greenhouse.io/salesloft/jobs/123"), ("greenhouse", "salesloft"))
        self.assertEqual(sources.detect_from_url("https://jobs.lever.co/calendly/abc"), ("lever", "calendly"))
        self.assertEqual(sources.detect_from_url("https://jobs.ashbyhq.com/ramp"), ("ashby", "ramp"))
        self.assertEqual(sources.detect_from_url("https://homedepot.wd5.myworkdayjobs.com/en-US/CareerDepot/job/x")[0], "workday")
        self.assertEqual(sources.parse_workday_url("https://homedepot.wd5.myworkdayjobs.com/en-US/CareerDepot/job/x"),
                         ("homedepot", "wd5", "CareerDepot"))
        self.assertEqual(sources.detect_from_url("https://example.com/careers"), (None, None))


class TestPipeline(unittest.TestCase):
    def test_dry_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            cos = [{"name": n} for n in ("Fixture GH", "Fixture Lever", "Fixture Ashby")]
            jobs, statuses = runner.run(fetch=runner._fixture_fetch, today="2026-11-21",
                                        companies=cos, out_dir=Path(tmp))
            titles = {j["title"] for j in jobs}
            self.assertEqual(titles, {"Sales Operations Analyst", "Implementation Specialist",
                                      "Business Systems Analyst", "Management Trainee",
                                      "Revenue Operations Associate"})
            by = {j["title"]: j for j in jobs}
            self.assertEqual(by["Implementation Specialist"]["min_years"], 3)
            self.assertEqual(by["Sales Operations Analyst"]["min_years"], 0)
            self.assertEqual(by["Business Systems Analyst"]["pay"], "$65,000 - $75,000")
            self.assertEqual(by["Revenue Operations Associate"]["region"], "remote")
            self.assertEqual(by["Management Trainee"]["region"], "aug")
            data = json.loads((Path(tmp) / "jobs.json").read_text())
            self.assertEqual(len(data["jobs"]), 5)
            # second run keeps the original first_seen date
            jobs2, _ = runner.run(fetch=runner._fixture_fetch, today="2026-11-25",
                                  companies=cos, out_dir=Path(tmp))
            self.assertTrue(all(j["first_seen"] == "2026-11-21" for j in jobs2))


class TestDiscovery(unittest.TestCase):
    def test_feeds_and_other_family(self):
        fx = Path(__file__).parent / "fixtures"
        muse = sources.parse_muse(json.loads((fx / "muse.json").read_text()))
        adz = sources.parse_adzuna(json.loads((fx / "adzuna.json").read_text()))
        self.assertEqual(adz[0]["title"], "Junior Operations Coordinator")
        self.assertEqual(adz[0]["pay"], "$45,000")
        feeds = lambda: ([{"name": "The Muse (search)", "ats": "themuse", "ok": True,
                           "fetched": 3, "error": None, "feed": True}], muse + adz)
        with tempfile.TemporaryDirectory() as tmp:
            cos = [{"name": n} for n in ("Fixture GH", "Fixture Lever", "Fixture Ashby")]
            jobs, st = runner.run(fetch=runner._fixture_fetch, today="2026-11-22",
                                  companies=cos, out_dir=Path(tmp), feeds=feeds)
            by = {j["title"]: j for j in jobs}
            # odd title kept because the posting says it's for new grads
            self.assertEqual(by["Claims Processor"]["family"], "other")
            self.assertTrue(by["Claims Processor"]["entry"])
            self.assertNotIn("Barista", by)
            self.assertEqual(by["Junior Operations Coordinator"]["region"], "atl")
            # the feed copy of a job the company site already listed is dropped
            self.assertEqual(sum(1 for j in jobs if j["title"] == "Implementation Specialist"), 1)
            self.assertEqual(by["Implementation Specialist"]["ats"], "greenhouse")
            # best fits come first
            scores = [j["score"] for j in jobs]
            self.assertEqual(scores, sorted(scores, reverse=True))
            self.assertLess(by["Implementation Specialist"]["score"], by["Sales Operations Analyst"]["score"])
            disc = json.loads((Path(tmp) / "discovered.json").read_text())
            self.assertIn("Acme Freight Co", disc)
            self.assertEqual(disc["Acme Freight Co"]["slugs"][0], "acmefreight")
            self.assertEqual(st[-1]["matched"], 2)

    def test_other_skips_trade_jobs(self):
        from offlist.filters import office_title, entry_signal
        for t in ["Auto Body Repair Technician", "Logistics Associate - 2nd Shift",
                  "Warehouse Associate", "Brake and Tire Technician"]:
            self.assertFalse(office_title(t), t)
        for t in ["Fraud Specialist I", "Claims Processor", "Logistics Coordinator"]:
            self.assertTrue(office_title(t), t)
        self.assertFalse(entry_signal("Logistics Coordinator", "Paid training provided", strong_only=True))
        self.assertTrue(entry_signal("Logistics Coordinator", "Great for recent graduates", strong_only=True))

    def test_slug_guesses(self):
        self.assertEqual(sources.slug_guesses("Flock Safety, Inc."), ["flocksafety", "flock-safety", "flock"])
        self.assertEqual(sources.slug_guesses("The Home Depot"), ["homedepot", "home-depot"])

    def test_push_run_keeps_last_adzuna_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            row = {"id": "a1", "title": "Junior Data Analyst", "company": "Mindray", "family": "analyst",
                   "region": "atl", "location": "Atlanta", "remote": False, "url": "https://x/1",
                   "ats": "adzuna", "posted": None, "posted_text": None, "first_seen": "2026-11-20",
                   "min_years": None, "pay": None, "tools": [], "entry": True, "score": 0}
            bad = dict(row, id="a2", title="Associate Counsel", url="https://x/2")
            (d / "jobs.json").write_text(json.dumps({"jobs": [row, bad]}))
            runner.run(fetch=lambda e: ({"name": e["name"], "ok": True, "ats": "x"}, []),
                       today="2026-11-22", companies=[{"name": "Acme"}], out_dir=d, keep_adzuna=True)
            jobs = json.loads((d / "jobs.json").read_text())["jobs"]
            self.assertEqual([j["id"] for j in jobs], ["a1"])
            self.assertGreater(jobs[0]["score"], 0)
            company_site = runner.fit_score(dict(row, ats="greenhouse"), 0)
            self.assertLess(runner.fit_score(row, 0), company_site)

    def test_feed_company_cleanup(self):
        self.assertEqual(runner.company_key("The Home Depot"), runner.company_key("Home Depot"))
        self.assertEqual(runner.company_key("Ferguson Enterprises, LLC."), "ferguson enterprises")
        self.assertFalse(runner.worth_watching("Atlanta GA"))
        self.assertFalse(runner.worth_watching("Robert Half"))
        self.assertTrue(runner.worth_watching("Mindray North America"))
        roles = RoleMatcher(runner.load_yaml("roles.yaml"))
        self.assertTrue(roles.is_excluded("Associate Counsel, Product and Marketing"))
        self.assertTrue(roles.is_excluded("Security Operations Analyst Fixed Term"))
        self.assertFalse(office_title("Fabrication Associate - Entry Level"))


if __name__ == "__main__":
    unittest.main()

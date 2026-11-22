"""Offline tests. Run with: python -m unittest -v"""
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from offlist import run as runner
from offlist import sources
from offlist.filters import LocationMatcher, RoleMatcher, extract_pay, html_to_text, min_years, tools_mentioned

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


if __name__ == "__main__":
    unittest.main()

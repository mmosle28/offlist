# offlist

**Entry level jobs most people never search for, pulled daily from company careers sites.**

Search "information systems jobs" and you get software engineering roles you can't do and help desk roles that pay $40K. Meanwhile there are whole families of jobs that fit an IS or business degree almost perfectly (Sales Operations Analyst, Revenue Operations, Implementation Specialist, Business Systems Analyst, Salesforce Admin) that students rarely hear about and rarely search for. Many of them are also posted only on the company's own careers page, never on Handshake or LinkedIn.

offlist does two things about that:

1. **Names the hidden roles.** A plain-English guide to job families an IS or business grad fits, what each job actually is, and the titles to search for.
2. **Finds them at the source.** Every morning a GitHub Action reads the public job feeds behind company careers pages (Greenhouse, Lever, Ashby, SmartRecruiters and Workday), keeps only those role families in metro Atlanta, Augusta, Georgia or remote, drops senior titles, and flags postings that ask for 3+ years of experience. Results publish to a phone-friendly site on GitHub Pages.

## How it works

```
config/companies.yaml ─┐
config/roles.yaml ─────┼─> offlist/run.py ──> docs/data/jobs.json ──> docs/index.html (GitHub Pages)
config/locations.yaml ─┘        │
                                └─ offlist/sources.py   one fetcher per applicant tracking system
                                   offlist/filters.py   title, location, years and pay rules
```

- **Auto detection.** Give a company a `guess` name and offlist tries it on each system, then caches whichever works in `docs/data/ats_cache.json`. Or paste any careers link as `url` and it figures out the system from the link.
- **Experience flag.** Reads each description for phrases like "3+ years of experience" and badges the job green (0 to 2 years), red (3+), or grey (not stated).
- **Pay.** Uses the posted salary range when the system provides one, otherwise the first salary-looking range in the description.
- **Memory.** `docs/data/seen.json` remembers when each job first appeared, so the site can mark what's new.
- **Status.** The Companies tab shows which careers sites were read and which links need fixing.

No API keys, no database, no server. Python standard library plus PyYAML.

## Use it

- **Browse:** the GitHub Pages site for this repo.
- **Refresh now:** Actions tab → *Update jobs* → *Run workflow*.
- **Add a company:** add one line to `config/companies.yaml`, for example
  `- {name: Example Co, url: "https://jobs.lever.co/exampleco"}`
- **Add a role or title:** add a phrase under the right family in `config/roles.yaml`.
- **Change the area:** edit `config/locations.yaml`.

## Run it locally

```bash
pip install -r requirements.txt
python -m unittest -v          # offline tests
python -m offlist.run --dry    # offline run on test fixtures, writes to a temp folder
python -m offlist.run          # live run, updates docs/data/
```

## Notes

- Only public job feeds that companies publish for their own careers pages are read. Each job links back to the company's posting, where you apply.
- Pay and experience are parsed from free text and can be missed. Always read the posting.
- Built by Micah Mosley (Information Systems, Kennesaw State University) with Claude.

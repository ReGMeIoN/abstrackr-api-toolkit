# abstrackr API toolkit (unofficial)

Reverse-engineered JSON API client for [abstrackr](https://abstrackr.com) — the free
web-based citation-screening tool for systematic reviews.

abstrackr publishes **no API documentation**, but its web app talks to a plain
JSON API under `/api/*`. This toolkit drives that API end to end:

```
login → import citations → bulk-submit screening decisions → tag exclusion reasons
      → unblind → download the full-project JSONL → compute PRISMA counts and Cohen's kappa
```

Everything here was verified against the production site (see `docs/ENDPOINTS.md`
for the endpoint table and `docs/PITFALLS.md` for the traps).

---

## ⚠️ Read this first

* **Unofficial.** This is not affiliated with or endorsed by abstrackr.
* **Terms of service.** Automated access may conflict with the site's terms. Use it
  on your own account, at your own risk; a wrong kind of automation can get an
  account suspended. The authors of this toolkit take no responsibility.
* **No credentials, no review data.** This repository contains only code and
  documentation. Credentials are read from local files that are git-ignored.
* **Recommendation.** Use a **dedicated account** for automation (invite it as a
  Member of the project) rather than your own primary account. Members can screen;
  only Leaders can unblind or export.
* **Be polite.** The client is single-threaded with a ~1 s delay between calls.
  Please do not remove that.

---

## Contents

| File | What it does |
|---|---|
| `abstrackr_client.py` | Minimal session client: login (cookie jar), read any endpoint, guarded writes |
| `abstrackr_screen.py` | The main driver: `login / whoami / import-ris / fetch-citations / map / submit / unblind / reblind / mode / export` |
| `abstrackr_tag.py` | Bulk-apply tags (e.g. exclusion-reason codes) from a mapping CSV |
| `abstrackr_jsonl_report.py` | Parse a full-project JSONL export into PRISMA counts, per-reviewer activity, conflicts and **Cohen's kappa** |
| `probes/probe_ris_fields.py` | Demonstrates that RIS import loses the PMID and that de-duplication does not fire |
| `probes/probe_csv_fields.py` | Demonstrates that CSV import populates `pmid` correctly |
| **`pipeline/`** | **The screening pipeline: batch → screen → validate → Hold pool → submit → verify → PRISMA.** See below |
| **`prompts/`** | **Screening prompt templates (title/abstract and full text), the method written down** |
| **`docs/PIPELINE.md`** | **The pipeline SOP: stages, quality gates, orchestration discipline, research integrity** |

---

## The screening pipeline

`abstrackr_screen.py` moves citations; `pipeline/` runs a **review**. It is the
reusable form of a workflow that screened 8,491 unique records in 85 batches with
0 failed validations, then wrote 9,438 labels and 41,275 tags to the platform and
verified every one of them by crawling the project back.

```
workbook → s1 batch → screen → s2 validate → s3 merge → s4 normalise
        → s5 Hold pool → s6 audit sample → s7 submit → s8 verify → s9 PRISMA
```

Everything review-specific lives in one `protocol.json` (codes, vocabularies,
conditional output schema, column mapping, platform tag plan); the code stays
generic. Four ideas carry most of the value:

* **One central validator.** Screeners read an input, write one file, return one
  line. Batch that 100 records at a time and you pay 85 agent startups for
  8,500 records — 200–300 per batch is the fix.
* **Only definitional normalisations are automatic.** Ambiguous code boundaries
  are exported to CSV for a human; auto-fixing one of them once rewrote 373
  *correct* decisions.
* **A write is not done until it is verified.** The platform has no tag filter,
  so `s8` crawls every citation and compares the complete per-citation tag set,
  not just totals.
* **PRISMA numbers are derived and checked**, never hand-typed; anything unknown
  is left `null` and listed as "to fill by hand".

Start at [`pipeline/README.md`](pipeline/README.md); the reasoning is in
[`docs/PIPELINE.md`](docs/PIPELINE.md).

---

## Install / requirements

Python 3.9+ and the standard library only. No third-party packages.

```bash
git clone <this repo> && cd abstrackr-api-toolkit
```

## Credentials

Create one JSON file per account **outside version control** (both are git-ignored):

```json
// credentials.json       (the account that will do the automated pass)
{ "email": "bot@example.com", "password": "…" }

// credentials_bot.json   (optional second account, used with --as-bot)
{ "email": "second@example.com", "password": "…" }
```

The client never prints passwords: only a masked email and the password length.

## Quick start

```bash
py=python                       # or python3

# 1. log in (caches a cookie jar next to the script)
$py abstrackr_client.py login

# 2. import a citation file — CSV is strongly preferred, see docs/PITFALLS.md
$py abstrackr_screen.py --project 12345 import-ris citations.csv --execute

# 3. build an idx -> citation_id mapping for your own decision table
$py abstrackr_screen.py --project 12345 fetch-citations
$py abstrackr_screen.py --project 12345 map

# 4. submit decisions in bulk (dry run first!)
$py abstrackr_screen.py --project 12345 submit --limit 5
$py abstrackr_screen.py --project 12345 submit --execute

# 5. unblind, export, and turn the JSONL into PRISMA numbers + kappa
$py abstrackr_screen.py --project 12345 unblind --reason-category "QC Audit" --duration 15
$py abstrackr_screen.py --project 12345 export --report
$py abstrackr_screen.py --project 12345 reblind
```

`ABSTRACKR_PROJECT=<id>` can be set instead of passing `--project` everywhere.

### Two-account (double screening) pattern

```bash
$py abstrackr_screen.py --as-bot login        # separate cookie jar per account
$py abstrackr_screen.py --project 12345 --as-bot submit --execute
# …the human reviewer screens in the web UI (mode = Double, blinding on)…
# …then the Leader unblinds and exports; the JSONL contains both label sets,
#    which abstrackr_jsonl_report.py turns into conflicts + Cohen's kappa.
```

---

## What the JSONL report gives you

```bash
$py abstrackr_jsonl_report.py project.jsonl --outdir report/
```

```
prisma_numbers.md/.csv   status and final-decision counts
reviewer_activity.csv    labels, includes, excludes, exclude rate per reviewer
agreement.csv            pairwise observed/expected agreement + Cohen's kappa
conflicts.csv            citations still in conflict
included.csv  excluded.csv  maybe.csv  unscreened.csv
```

---

## Endpoint reference

See **`docs/ENDPOINTS.md`** — table of every verified route with method, body and
semantics (projects, import, citations, labels, tags, notes, resolution, screening
sets, duplicates, blinding, export), plus the **Leader vs Member permission matrix**.

## Traps that will cost you an afternoon

See **`docs/PITFALLS.md`**. The short version:

1. `?page=` is **0-indexed** and ordered by **id descending** — `page=1` skips the newest 100 rows.
2. RIS import **loses the PMID** (`AN  - x` → `accession_number`), and an unknown RIS
   tag is appended to the *previous* field. Use **CSV**.
3. **Automatic de-duplication did not fire** in any test (across uploads, or within
   one file). De-duplicate upstream and never double-import.
4. The full-project export is a **ZIP** (binary), requires the project to be
   **unblinded**, and is **Leader-only** (Members get 403).

## License

MIT — see `LICENSE`.

# abstrackr API toolkit

An unofficial Python client and screening pipeline for
[abstrackr](https://abstrackr.com), the free web-based citation-screening
application for systematic reviews.

abstrackr publishes no API documentation, but its web application is built on a
plain JSON API under `/api/*`. This toolkit makes that API usable from a script:
it imports citations, submits screening decisions in bulk, applies exclusion-reason
tags, manages blinding and exports, and adds a complete **screening pipeline** for
running an AI-assisted title/abstract and full-text pass with a central validator,
a verified platform write-back and PRISMA accounting.

**Python 3.9+ · standard library only · no third-party dependencies.**

---

## Overview

The toolkit is built in two layers.

| Layer | Purpose |
|---|---|
| **API client** | A read-first client for the abstrackr JSON API. Every route was exercised against the production site; the verified table is in [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md). |
| **Screening pipeline** (`pipeline/`) | A reproducible workflow around that client: batch a de-duplicated workbook, screen it, validate centrally, build a Hold pool, submit to the platform, verify the write, and account for PRISMA numbers. |

## Repository layout

| Path | Contents |
|---|---|
| `abstrackr_client.py` | Session client: cookie-based login, guarded reads and writes |
| `abstrackr_screen.py` | Main driver: `login`, `whoami`, `import-ris`, `fetch-citations`, `map`, `submit`, `unblind`, `reblind`, `mode`, `export` |
| `abstrackr_tag.py` | Bulk tag application from a mapping CSV |
| `abstrackr_jsonl_report.py` | Turns a full-project JSONL export into PRISMA counts, reviewer activity, conflicts and Cohen's kappa |
| `pipeline/` | The screening pipeline (stages `s1`–`s11`) |
| `prompts/` | Screening prompt templates for the title/abstract and full-text stages |
| `docs/` | Endpoint reference, platform notes and the pipeline SOP |
| `probes/` | Small scripts that demonstrate citation-import behaviour |

## Requirements

Python 3.9 or newer. No third-party packages — everything uses the standard library.

## Installation

```bash
git clone https://github.com/ReGMeIoN/abstrackr-api-toolkit.git
cd abstrackr-api-toolkit
```

## Credentials

Credentials are read from local JSON files that are **git-ignored** and never
printed:

```json
{ "email": "bot@example.com", "password": "..." }
```

Place the file in an `abstrackr/` directory alongside the toolkit, or point the
`ABSTRACKR_HOME` environment variable at the directory holding `credentials.json`
and `.session_cookies.txt`. Passwords are never echoed: only a masked email address
and the password length are shown.

## Getting started

```bash
py=python                       # or python3

# 1. sign in (caches a cookie jar locally)
$py abstrackr_client.py login

# 2. import a citation file (CSV is strongly preferred, see docs/PITFALLS.md)
$py abstrackr_screen.py --project <id> import-ris citations.csv --execute

# 3. build an index -> citation id mapping for your own decision table
$py abstrackr_screen.py --project <id> fetch-citations
$py abstrackr_screen.py --project <id> map

# 4. submit screening decisions in bulk (dry run first)
$py abstrackr_screen.py --project <id> submit --limit 5
$py abstrackr_screen.py --project <id> submit --execute

# 5. unblind, export, then turn the JSONL into PRISMA numbers and kappa
$py abstrackr_screen.py --project <id> unblind --reason-category "QC Audit" --duration 15
$py abstrackr_screen.py --project <id> export --report
$py abstrackr_screen.py --project <id> reblind
```

`ABSTRACKR_PROJECT=<id>` may be set instead of passing `--project` on every call.

### Double screening with two accounts

```bash
$py abstrackr_screen.py --as-bot login
$py abstrackr_screen.py --project <id> --as-bot submit --execute
# the human reviewer screens in the web UI (mode = Double, blinding on);
# the Leader then unblinds and exports. The JSONL carries both label sets,
# which abstrackr_jsonl_report.py turns into conflicts and Cohen's kappa.
```

## What the JSONL report contains

```bash
$py abstrackr_jsonl_report.py project.jsonl --outdir report/
```

| Output | Contents |
|---|---|
| `prisma_numbers.md` / `.csv` | Status and final-decision counts |
| `reviewer_activity.csv` | Labels, includes, excludes and exclusion rate per reviewer |
| `agreement.csv` | Pairwise observed and expected agreement, plus Cohen's kappa |
| `conflicts.csv` | Citations still in conflict |
| `included.csv`, `excluded.csv`, `maybe.csv`, `unscreened.csv` | Per-status exports |

## The screening pipeline

`abstrackr_screen.py` moves citations; `pipeline/` runs a review. The pipeline takes
a de-duplicated workbook through batching, screening, central validation,
definitional-only normalisation, a Hold pool, a reproducible audit sample, a guarded
platform write-back, read-back verification, PRISMA accounting and queue export:

```
workbook → s1 batches  → screen       → s2 validate → s3 merge
         → s4 normalise → s5 Hold pool → s6 audit sample
         → s7 submit    → s8 verify    → s9 PRISMA
         → s10 export queue            → s11 release labels for human review
```

Everything review-specific — decision codes, controlled vocabularies, the
conditional output schema, workbook columns and the platform tag plan — lives in a
single `protocol.json`. The stage code stays generic, so a new review means a new
protocol file rather than a forked pipeline.

Start with [`pipeline/README.md`](pipeline/README.md); the method and the reasoning
behind each rule are in [`docs/PIPELINE.md`](docs/PIPELINE.md).

## Documentation

| Document | Contents |
|---|---|
| [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md) | Every verified route with method, request body and semantics, plus the Leader/Member permission matrix |
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | Screening pipeline SOP: stages, quality gates, orchestration discipline and reporting integrity |
| [`docs/PITFALLS.md`](docs/PITFALLS.md) | Platform behaviour that must be handled explicitly: paging, import field mapping, export preconditions |
| [`pipeline/README.md`](pipeline/README.md) | Pipeline quick start and stage map |
| [`prompts/README.md`](prompts/README.md) | How the screening prompts are structured |

## Scope and disclaimer

* **Unofficial.** This project is not affiliated with or endorsed by abstrackr.
* **Terms of service.** Automated access may conflict with the site's terms. Use it
  on your own account and at your own risk.
* **No credentials, no review data.** The repository contains code and documentation
  only; credentials are read from local, git-ignored files.
* **Use a dedicated account** for automation and invite it to the project as a
  Member. Members can screen; only Leaders can unblind or export.
* **Rate limits.** The client is single-threaded with a short delay between calls.
  Please keep it that way.

## License

MIT — see [`LICENSE`](LICENSE).

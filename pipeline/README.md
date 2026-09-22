# `pipeline/` — the screening pipeline

The toolkit's client (`abstrackr_client.py`, `abstrackr_screen.py`) drives the
**API**. This directory drives a **review**: batch the pool, screen it, validate
it centrally, build the Hold pool, submit, verify, and account for PRISMA.

The method and the reasoning behind every rule are in
[`../docs/PIPELINE.md`](../docs/PIPELINE.md). This file is the practical entry
point.

## Quick start

```bash
py=python

# 1. describe your review -- copy the template and edit it
cp pipeline/protocol.example.json /path/to/workdir/protocol.json

# 2. put your de-duplicated workbook in the workdir as records_enriched.csv
#    (needs at least: idx, citation_id, title, abstract, pmid, language, has_abstract
#     -- rename in protocol.json's `workbook` section if yours differ)

# 3. batch it (200-300 per batch)
$py pipeline/s1_prep_batches.py --dir /path/to/workdir --size 250

# 4. screen each batch -- hand the screener the prompt file path + batch name
#    prompts/screen_tiab.zh-CN.md  or  prompts/screen_tiab.en.md
#    -> writes decisions/<batch>.decisions.json

# 5. validate (the only validator), merge, normalise, build the Hold pool
$py pipeline/s2_validate.py     --dir /path/to/workdir --fuse
$py pipeline/s3_merge.py        --dir /path/to/workdir
$py pipeline/s4_normalize.py    --dir /path/to/workdir --apply
$py pipeline/s5_hold_pool.py    --dir /path/to/workdir
$py pipeline/s6_audit_sample.py --dir /path/to/workdir --n 50 --seed 500

# 6. submit to the platform (dry run first) and then PROVE it landed
$py pipeline/s7_submit.py --dir /path/to/workdir --project 12345
$py pipeline/s7_submit.py --dir /path/to/workdir --project 12345 --execute
$py pipeline/s8_verify.py --dir /path/to/workdir --project 12345

# 7. PRISMA accounting
$py pipeline/s9_prisma.py --dir /path/to/workdir --topical-reviews 710 --included 215
```

`--protocol` is optional when `protocol.json` sits in the workdir. Every stage
resolves its paths and vocabularies through `protocol.py`.

## Stage map

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 1 | `s1_prep_batches.py` | workbook | `batches/*.json`, `manifest.csv`, `_INDEX.json` |
| 2 | `s2_validate.py` | batches + decisions | verdict per batch; `all_decisions.json` with `--fuse` |
| 3 | `s3_merge.py` | decisions | `all_decisions.json`, `screening_decisions.csv`, `merge_summary.json` |
| 4 | `s4_normalize.py` | `all_decisions.json` | `all_decisions_normalized.json` + audit CSVs (`--apply`) |
| 5 | `s5_hold_pool.py` | decisions + workbook | `hold_fulltext.json/.csv`, `hold_summary.md` |
| 6 | `s6_audit_sample.py` | decisions + workbook | `sample_N.csv` |
| 7 | `s7_submit.py` | decisions + all records | platform labels + tags (dry run by default) |
| 8 | `s8_verify.py` | platform + local plan | discrepancy report |
| 9 | `s9_prisma.py` | decisions + pool | `prisma_values.json`, `prisma_numbers.md` |
| 10 | `s10_export_queue.py` | platform screening set / filter | `queue_*.csv` (cross-checked against local decisions) |
| 11 | `s11_release_labels.py` | automation-written labels | released back to `unscreened` so a human can screen (the AI verdict stays in the tags) |

A platform **screening set** is a saved *filter* (`{"labeled_by_decision":"maybe"}`),
re-evaluated live by the server — so a review queue is **created on the platform**,
not uploaded, and `s10` only reads it back and proves its size and contents.

## Files

| File | Role |
|---|---|
| `protocol.py` | protocol loader, validation, vocabularies, de-duplication, and the shared tag-plan builder |
| `protocol.example.json` | The template to copy into your workdir and fill in |
| `s1_…` … `s11_…` | The stages |

## Two things that are deliberate, not incidental

**The fast lane.** `schema.fast_lane` lets one clearly design-excluded code
(e.g. `X4` case report / n<10 / animal / in-vitro) carry only `i/d/c/r` instead
of the full field set. It saves real tokens across thousands of records. The cost
is that those records carry no `form:` tag, so they cannot be subdivided in the
PRISMA reason split. The protocol records the choice; the validator enforces it.

**Audit exports are not fixes.** Anything ambiguous is written to CSV for a human
and left untouched. See `docs/PIPELINE.md` §6 — automatic normalisation of an
ambiguous boundary once rewrote 373 correct decisions.

## Requirements

Python 3.9+, standard library only (same as the rest of this toolkit). The
platform stages need a logged-in session: run `python abstrackr_client.py login`
first, or point `ABSTRACKR_HOME` at the directory holding
`credentials.json` / `.session_cookies.txt`.

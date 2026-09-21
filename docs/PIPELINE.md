# The screening pipeline — standard operating procedure

A reproducible **LLM-assisted screening pipeline** for systematic reviews: from a
de-duplicated workbook to a verified platform write-back, with one central
validator, one reviewed protocol file, and a PRISMA account that is checked
rather than hand-typed.

It was extracted from a real review (cholecystectomy and cholangiocarcinoma,
8,491 unique records, 85 batches, 0 failed validations, ~41k platform tag
writes) where every rule below was paid for. The numbers in that review are used
throughout as worked examples.

```
   workbook            s1  split into batches        (200-300 records each)
   ─────────►  batches/  ─►  screening pass  ─►  decisions/*.decisions.json
                                                          │
                          s2  validate every batch ◄──────┘   ← the ONLY validator
                                                          │
                          s3  merge + coverage check  ────►  all_decisions.json
                                                          │
                          s4  definitional normalisation only (the rest goes to a human)
                                                          │
                          s5  Hold pool ──────────────────►  hold_fulltext.json
                          s9  PRISMA accounting ──────────►  prisma_values.json
                                                          │
                          s7  submit labels + tags ───────►  abstrackr project
                          s8  crawl it all back and compare  ← proof, not hope
```

---

## 1. Design principles

1. **One protocol file, zero review-specific code.** Codes, vocabularies, the
   conditional output schema, workbook column names and the entire platform tag
   plan live in `protocol.json`. A new review means a new protocol file, not a
   forked pipeline.
2. **One central validator.** Screeners read an input, write one output file and
   return one line of receipt. They never write their own generator or checker.
   (See §5 — this rule exists because violating it cost 2-4x the token budget of
   the data itself.)
3. **The machine only auto-fixes what is definitional.** Anything ambiguous is
   exported to CSV for a human. Machine normalisation of an ambiguous code
   boundary once rewrote 373 *correct* decisions.
4. **A write is not done until it is verified by crawling it back.** The platform
   has no tag-filter endpoint, so verification means reading every citation.
5. **Every number that goes into the paper is either derived by a script or
   explicitly marked as "to fill by hand".** Nothing is guessed.

---

## 2. Stages

| # | Script | Input → Output | Notes |
|---|---|---|---|
| 1 | `s1_prep_batches.py` | workbook → `batches/*.json` + `manifest.csv` + `_INDEX.json` | default **250** records/batch; `--only-missing` resumes |
| 2 | *(screening pass)* | batch → `decisions/<batch>.decisions.json` | human or LLM; prompt in `prompts/` |
| 3 | `s2_validate.py` | batches + decisions → verdict per batch | the central validator; non-zero exit gates the run; `--fuse` also writes `all_decisions.json` |
| 4 | `s3_merge.py` | decisions → `all_decisions.json`, `screening_decisions.csv`, `merge_summary.json` | refuses to look complete when records are missing |
| 5 | `s4_normalize.py` | `all_decisions.json` → `all_decisions_normalized.json` + audit CSVs | `--apply` to write; audit exports are never auto-applied |
| 6 | `s5_hold_pool.py` | decisions → `hold_fulltext.json/.csv` + `hold_summary.md` | the full-text worklist; `need` drives it |
| 7 | `s6_audit_sample.py` | decisions → `sample_N.csv` | `--seed` makes the QC sample reproducible; `--stratified` for rare codes |
| 8 | `s7_submit.py` | decisions + all records → platform labels + tags | **dry run by default**; `--execute`; resumable; refuses partial passes |
| 9 | `s8_verify.py` | platform ↔ local plan → discrepancy report | crawls every citation; exit 0 only when it fully agrees |
| 10 | `s9_prisma.py` | decisions + pool → `prisma_values.json`, `prisma_numbers.md` | checks the PRISMA identities; leaves unknown boxes `null` |
| 11 | `s10_export_queue.py` | a platform screening set → local queue CSV | read-only; cross-checks the queue against the local decisions |

> **Screening sets are saved filters, not hand-curated lists.** `POST
> /screening_sets {"name":…}` creates a queue defined by `filter_json` (e.g.
> `{"labeled_by_decision":"maybe"}`), and the server re-evaluates it live as
> labels change; `citations?set_id=N` then filters by it. So "give me the 258
> Maybe records" needs no re-import and no second project — but the queue must
> still be *verified*, because an ignored filter silently returns the whole
> project. `s10` takes `--expect N` for exactly that reason.

Run them in order. Stages 4, 6 and 9 are the ones people skip and regret.

---

## 3. The protocol file

`protocol.json` sits in the workdir and is the authoritative definition of:

| Section | What it decides |
|---|---|
| `decision_families` | the family letters (`d`), their codes, and which families are **Hold** |
| `code_meanings` | the human-readable meaning of each code (used in every report) |
| `vocabularies` | controlled lists for `form`, `exp`, `out`, `need` |
| `schema` | conditional output rules: the fast lane, `need` requirement, field names, length caps |
| `workbook` | CSV names, the id column, and the title/abstract column mapping |
| `dedup` | which fields identify a duplicate cluster |
| `normalize_rules` | normalisations that are definitional enough to auto-apply |
| `audit_exports` | ambiguous boundaries to export for a human, with a written rationale |
| `platform` | label value per family, tag prefix, role/meta/duplicate tag plan |

Two reference files ship with the pipeline:

* `pipeline/protocol.example.json` — a neutral template to copy.
* `pipeline/examples/cca-cholangiocarcinoma.protocol.json` — the real worked
  instantiation, including the full decision ladder it encodes.

`protocol.py` validates the file when a stage starts, so a typo fails
immediately rather than halfway through a 9,000-record submission.

---

## 4. Counting: unique records vs platform ids

**This is the single most common source of wrong numbers.** Every review that
imports from more than one source has duplicate copies on the platform.

| | unit | used for |
|---|---|---|
| unique count | one per de-duplicated record | **PRISMA**, screening statistics, kappa |
| id count | one per platform citation id | platform reports, tag counts, Maybe/Exclude totals |

Worked example: 11,925 imported → 3,434 redundant → **8,491 unique**, which the
platform holds as **9,438 citation ids** (947 extra copies). The same pool is
"211 Hold records" locally and **258 Maybe citations** on the platform. Both are
correct; they answer different questions. `s7` fans one decision out to every
copy in a cluster so no two copies can disagree.

---

## 5. Orchestration discipline

Screening 8,491 records with subagents once meant **104 agent startups, 10 of
them wasted**, with the coordination overhead pushing total consumption to 2-4x
the token cost of the data itself (12.6 MB ≈ 3.1 M tokens). These rules are the
fix; treat them as part of the pipeline, not as advice.

| # | Rule | Why |
|---|---|---|
| 1 | **200-300 records per batch** | 100/batch turned 8,500 records into 85 startups; 300/batch needs 29 |
| 2 | **Screeners never build generator or validator scripts** | the central validator exists; self-checking is duplicated work |
| 3 | **The prompt is a file**, referenced by path + batch name | guarantees identical wording across 100 batches and keeps long prompts out of the orchestrator's context |
| 4 | **Verify preconditions before starting** (pool frozen, permissions, input quality) | a pool that changes upstream wastes the *entire* pipeline run |
| 5 | **One pass by default**; a second pass needs a stated benefit and a human decision | a partial second pass cost ~10% of the budget for 12% coverage |
| 6 | **Concurrency ≤10**, with a written stuck-criterion (e.g. no receipt in 10 min → interrupt, restart, log it) | one run peaked above 40 concurrent agents with 7 silently stuck |
| 7 | **One-line receipts from screeners; milestone reporting only** | 96 long reports ≈ 100k tokens of parent context |
| — | **Delete temp directories only after every batch has finished** | deleting under a running batch is a dangerous operation |

`Screening` itself is embarrassingly parallel and idempotent: rerunning a batch
overwrites one file, so the safe recovery from a stuck agent is always
"interrupt, restart that batch".

---

## 6. Quality gates

| Gate | Mechanism | Where |
|---|---|---|
| **G1 format** | id set exact, code-family consistency, conditional field shape, vocabulary, `need` present | `s2_validate.py` — a failing batch is rerun |
| **G2 reproducibility** | seeded random sample, regenerable by a third party | `s6_audit_sample.py` |
| **G3 calibration** | agreement with a second pass (Cohen's kappa) **or** with a previous pool | `s6` + manual |
| **G4 gold standard** | the previous review's included studies must be recallable | protocol-specific script |
| **G5 pool coverage** | **check that the gold standard is IN the pool before screening** | precondition, §5 rule 4 |
| **G6 boundary audit** | ambiguous code boundaries exported, not auto-fixed | `s4_normalize.py` audit exports |
| **G7 platform truth** | crawl every citation back and compare tag sets and labels | `s8_verify.py` |
| **G8 closure** | every Hold record gets a full-text verdict | full-text stage |

**On G4/G5 — the lesson worth repeating.** In the worked example, a gold-standard
check *inside the pool* reported "0 missed", which was true and also useless: 11
of the 16 previously included studies were never in the pool to begin with. A
recall check proves nothing until you have proved the pool covers the gold
standard. **Check pool coverage before screening, not after.**

**On G6 — why normalisation is dangerous.** Cross-batch drift is real, and the
temptation is to normalise it all mechanically. In the worked example, one
ambiguous boundary (X2 vs X3 for gallbladder-cancer records, 2,008 records) was
run through a `form`-based rule; sampling showed it would have rewritten 373
decisions that were *already correct*, because the model had filled `form` with
the study design rather than the outcome kind. It was reverted and exported for
human review instead. Both codes excluded the record, so inclusion was never at
risk — only the PRISMA reason split. **Auto-fix only what follows from the
definition of a code.**

---

## 7. Platform write-back and verification

`s7_submit.py` is the only writer. Safety properties, all of them non-optional:

* **dry run by default** — every write needs `--execute`
* **refuses partial passes** — if any unique record lacks a decision, it exits
  without writing anything
* **duplicate fan-out** — one decision, applied to every citation id in the
  cluster, representative = smallest id (shared code with the verifier)
* **resumable** — a state file records labelled ids; an interrupted run
  continues
* **throttled and audited** — one request at a time (~1 s), every call appended
  to an audit log
* **Hold maps to Maybe, never to Include** — a title/abstract stage has no basis
  for an inclusion decision

`s8_verify.py` then builds its expectation by importing `s7`'s plan builder, so
writer and verifier cannot drift. It crawls `citations?page=N` and compares, per
citation, the **complete tag set** (not just aggregate counts — aggregates hide a
tag landing on the wrong copy of a duplicate pair) plus `my_label`.

> Note on paging: the listing endpoint is 0-indexed, 100 rows per page, and
> `limit`/`offset`/`per_page` are ignored. Do not compute the page count from a
> total; page until a short page comes back, and de-duplicate by id.

**Order matters between `s4` and `s7`.** In the worked example the local map was
normalised *after* the platform write, so the platform carries 41,275 tag
placements while a fresh plan from the normalised map computes 41,276 — a
one-record difference, exactly the record the normalisation touched. Nothing was
wrong; the verifier simply described the earlier state. The standard is:
**normalise before submitting, then verify, then never re-normalise silently.**

---

## 8. PRISMA accounting

`s9_prisma.py` derives what it can and takes the rest as explicit arguments, then
checks:

```
identified − duplicates_removed          == records_screened
excluded_stage1 + hold + topical_reviews == records_screened
sum(exclusion reasons by code)           == records_screened   (unique counts)
Hold citation ids                        == platform Maybe       (if provided)
```

Unknown boxes are written as `null` and listed under **"to fill by hand"**. A
guessed PRISMA number is a fabricated result.

Worked example:

```
11,925 identified  −  3,434 duplicates  =  8,491 screened
7,570 excluded (stage 1) + 211 hold + 710 topical reviews = 8,491   ✓
```

Two conventions that must be stated in the paper:

* Stage 1 excludes only records that are **entirely off-topic**. Topic-relevant
  reviews/letters stay in the pool and are excluded later on document type, which
  is why "excluded" and "sought for retrieval" both look large.
* Reviews kept for citation chasing are a **separate PRISMA row**, not silently
  dropped, and the previous review's reference list belongs in the
  "other sources" box.

---

## 9. Reporting and research integrity

An LLM-assisted pass changes what the methods section may claim. Non-negotiable:

1. **State that screening was AI-assisted**, with the rubric, batch count and
   model family used.
2. **Report the recall evidence**: gold-standard recall, the QC sample result,
   and kappa if a second pass was run.
3. **Never describe the process as "two independent human reviewers"** unless two
   humans genuinely screened independently and blinded.
4. **Human review must actually happen** — all Hold/included records, plus a
   random sample of exclusions (≥20% or ≥50, whichever is larger), plus every
   record that changed verdict between stages.
5. **If blinding was lifted** on the platform (by any party, for any reason),
   the human review can no longer be called blinded. Record the audit-log entry
   and describe it honestly.

In the worked example the platform's blinding was lifted by the project leader
after the import, so the human review is reported as **AI-assisted first pass +
non-blind human review** — not as a blinded duplicate screen.

---

## 10. Pitfalls

The platform-specific traps (paging, RIS losing PMIDs, no automatic
de-duplication, Leader-only export) are in `docs/PITFALLS.md`. Pipeline-level
ones:

| Trap | Consequence | Guard |
|---|---|---|
| Screening while the pool is still changing upstream | the whole run is invalidated | freeze the import first (§5 rule 4) |
| Partial decision map submitted | part of the pool silently unlabelled | `s3` exit code + `s7` refusal |
| Validating code families only, not conditional shapes | fast-lane and Hold records come out malformed | `s2` checks the shape, not just the code |
| `need` left empty on Hold records | the full-text stage has no instructions | `s2` requires it |
| Normalising after the platform write | platform drifts by exactly the normalised records | normalise → submit → verify |
| Comparing aggregate tag counts only | a tag on the wrong duplicate copy passes | `s8` compares per-citation tag sets |
| Treating a `null` PRISMA box as zero | fabricated box in the flow diagram | `s9` lists "to fill by hand" |
| Assuming the platform state from a handover note | names, blinding and merges change under you | re-read the project state at the start of every session |

---

## 11. Worked example — at a glance

| | value |
|---|---|
| imported | 11,925 (PubMed 3,836 + Embase 8,089) |
| duplicates removed | 3,434 (platform merge 2,487 + local 947) |
| unique records screened | **8,491** |
| batches | 85 × 100 (this review predates the 250 rule) |
| validations | 85 OK / 0 FAIL |
| Hold pool | 211 records (mapped to 258 platform Maybe ids) |
| platform write | 9,438 labels, 41,275 tag placements, 59 distinct tags |
| verification | every citation crawled; 0 discrepancies |
| PRISMA | 11,925 − 3,434 = 8,491; 7,570 + 211 + 710 = 8,491 ✓ |
| known issues | 2,008-record ambiguous boundary, 4 logical contradictions, 1 auto-normalised code |

Reproducing it end to end with this pipeline:

```bash
py="C:/Users/ReG/miniconda3/python.exe"
proto=pipeline/examples/cca-cholangiocarcinoma.protocol.json
dir="D:/deepseek/cholecystectomy-cholangiocarcinoma-meta/screening/project_5691"

$py pipeline/s1_prep_batches.py --dir $dir --protocol $proto --size 100
# ... screening pass writes decisions/ ...
$py pipeline/s2_validate.py     --dir $dir --protocol $proto --fuse
$py pipeline/s3_merge.py        --dir $dir --protocol $proto
$py pipeline/s4_normalize.py    --dir $dir --protocol $proto --apply
$py pipeline/s5_hold_pool.py    --dir $dir --protocol $proto
$py pipeline/s6_audit_sample.py --dir $dir --protocol $proto --n 50 --seed 500
$py pipeline/s7_submit.py       --dir $dir --protocol $proto --project 5691          # dry run
$py pipeline/s7_submit.py       --dir $dir --protocol $proto --project 5691 --execute
$py pipeline/s8_verify.py       --dir $dir --protocol $proto --project 5691
$py pipeline/s9_prisma.py       --dir $dir --protocol $proto --topical-reviews 710 --included 215
```

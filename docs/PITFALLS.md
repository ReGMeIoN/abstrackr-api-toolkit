# Traps (all measured, all cost real time)

## 1. `?page=` is 0-indexed, and the default order is id **descending**

```bash
GET /api/projects/{pid}/citations?page=0   # ← first page (newest 100)
GET /api/projects/{pid}/citations?page=1   # ← second page
```

With 1,480 citations, pages `1..14` return 1,380 rows and look "complete" if you
assume 1-based paging. The first 100 rows are silently missing.

Also note `?offset=` is accepted but **ignored** — it keeps returning page 1, so a
naive offset loop can collect the same 100 rows 200 times and still look plausible.

**Rule:** page from 0; stop when a page returns fewer than 100 rows.

## 2. RIS import loses the PMID, and unknown RIS tags corrupt the previous field

Measured by importing one record per candidate tag (`probes/probe_ris_fields.py`):

| RIS tag | lands in |
|---|---|
| `AN  - 11111111` | `accession_number` (not `pmid`) |
| `PMID- 22222222` | *nothing* — **appended to the previous field** (it ended up inside the DOI value) |
| `M1  - …`, `N1  - …`, `ID  - …`, `UR  - …`, `KW  - …` | ignored / appended |

So after a RIS import the `pmid` field is **empty** for every record: UI search by
PMID does not find them, and the JSONL export carries no PMID.

**Fix:** import **CSV** with these exact headers
(`probes/probe_csv_fields.py` shows `pmid` populated correctly):

```csv
pmid,title,abstract,authors,journal,publication_year,doi
12345678,Title here,Abstract here,Author A,Journal Name,2024,10.1000/xyz
```

## 3. Automatic de-duplication did not fire

The docs state that imports de-duplicate by DOI and PMID. Measured behaviour:

| Scenario | Result |
|---|---|
| Re-import the same 3,687 records into the same project | `inserted: 3687, skipped: 0` |
| One file containing two records with the same DOI | both inserted |

**Consequences**

* de-duplicate **upstream** (PMID → DOI → normalised title+year) and never import
  the same set twice;
* if you do double-import: `DELETE /api/projects/{pid}/upload-history/{upload_id}`
  removes a whole batch (Leader only, irreversible, deletes that batch's labels too).

## 4. The full-project export is a ZIP, needs an unblinded project, and needs Leader

* Response is **binary ZIP** (`PK…`), containing a single `.jsonl`. Decoding it as
  text destroys it — always read bytes.
* While blinding is on: `401 {"error":"Project must be unblinded for full project export."}`
* As a plain Member: `403 {"error":"Forbidden. Only project leaders may perform this action."}`

Unblinding (`PATCH /api/projects/{pid}`) requires a reason category from a fixed list
and a duration; the project re-blinds itself when the duration expires.

## 5. `GET /api/projects/{pid}/config` is not the settings endpoint

It returns only `{"tags":[],"terms":[]}`. Settings live on `PATCH /api/projects/{pid}`.
Sending a config-shaped body to the wrong route yields `400 Invalid request body`.

## 6. Adding a member takes a numeric `user_id`

`POST /api/projects/{pid}/members` with `{"email":"…"}` → `400 {"error":"user_id is required"}`.
Use `{"user_id": 1234}`. (Email invitations exist but need a UI click to accept, so
they cannot be automated.)

## 7. Blinding also limits search

While blinding is active, the UI states that search is limited to your own decisions,
tags and notes. Plan for that if you rely on filtered listing endpoints.

## 8. Screening-mode values are exact

`Single`, `Double`, `Pilot` — anything else (including lowercase) → `500 Invalid screening mode`.

In **Double** mode a citation counts as "screened" only after the second reviewer
votes, so `progress.screened` stays at 0 after the first reviewer finishes even though
`my_labeled` equals the total. That is expected, not a bug.

## 9. Duplicate detection is asynchronous and rate limited

`POST …/detect_duplicates` returns `{"status":"started"}`; poll
`GET …/detect_duplicates` until `completed`. `429` = rate limited, `422` = project too
large.

## 10. Import limits

5 MB per chunk, at most 20 chunks (100 MB), extensions limited to
`.txt .nbib .csv .ris .bib`. A larger pool must be split into several uploads — and
remember de-duplication will not save you (§3).

## 11. Keep the client polite

The bundled client issues one request at a time with a ~1 s delay and never runs
concurrently. Removing that is a good way to get rate limited (`429`) or worse.

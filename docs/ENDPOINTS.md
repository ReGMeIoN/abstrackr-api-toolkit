# Verified endpoint reference

All routes below were exercised against the production site. `{pid}` = project id,
`{cid}` = citation id. Every request needs the session cookie obtained from
`POST /api/auth/web/login`.

Response codes seen in practice: `200/201` success, `400` bad body, `401` not signed
in (also used for "project must be unblinded"), `403` wrong role, `404` unknown route,
`405` wrong method, `429` rate limited, `500` invalid value (e.g. screening mode).

---

## 1. Authentication

| Method | Route | Body | Notes |
|---|---|---|---|
| POST | `/api/auth/web/login` | `{"email","password"}` | returns `{"errors":[...]}`; empty array = success. No CSRF token, no captcha. Session = cookie. |
| POST | `/api/auth/web/logout` | — | |
| POST | `/api/auth/web/register` | `{"email","password"}` | account needs email activation |
| POST | `/api/auth/activate` | `{"token"}` | activation link token |
| POST | `/api/auth/activate/resend` | `{"email"}` | |
| POST | `/api/auth/recover` | `{"email"}` | |
| POST | `/api/auth/reset-password` | `{"recovery_token","password"}` | |
| GET | `/api/user` | — | `{id, email, pending_email, created_at}` |
| GET | `/api/my-memberships` | — | projects the account belongs to |

## 2. Projects

| Method | Route | Body | Notes |
|---|---|---|---|
| GET | `/api/projects` | — | array of projects (with `screening_mode`, `blinding_enabled`, `amProjectLeader`) |
| POST | `/api/projects` | `{"name","description"}` | **201**, returns the new project |
| GET | `/api/projects/{pid}` | — | project detail |
| PATCH | `/api/projects/{pid}` | `{"screening_mode":"Single\|Double\|Pilot"}` | invalid value → `500 Invalid screening mode` |
| PATCH | `/api/projects/{pid}` | `{"sort_order":"…"}` | citation ordering |
| PATCH | `/api/projects/{pid}` | `{"blinding":{"action":"unblinded","reason_category":"Conflict Resolution\|QC Audit\|Technical Troubleshooting\|Other","reason_detail":"…","duration_minutes":15\|60\|240\|null}}` | disables blinding; `null` = indefinite |
| PATCH | `/api/projects/{pid}` | `{"blinding":{"action":"blinded"}}` | re-enables |
| GET | `/api/projects/{pid}/blinding-audit-log?page=N` | — | audit trail of blinding changes |
| DELETE | `/api/projects/{pid}` | — | Leader only |
| GET | `/api/projects/{pid}/config` | — | ⚠️ only `{"tags":[],"terms":[]}` — **not** the settings |
| POST | `/api/projects/{pid}/config` | `{"config":…,"mode":…}` | import of tag/term sets |
| GET | `/api/projects/{pid}/members` | — | `[{id,email,leader}]` |
| POST | `/api/projects/{pid}/members` | `{"user_id":N}` | **201**; email in `user_id` position → `400 user_id is required` |
| GET | `/api/projects/{pid}/my-membership` | — | `{"leader":bool}` |
| POST | `/api/projects/{pid}/invitations` | `{"email":"…"}` | email invitation; invitee must accept in the UI |
| DELETE | `/api/projects/{pid}/invitations` | `{"invitation_id":N}` | |

## 3. Import

Chunked upload; the file is parsed **server-side** after all chunks arrive.

| Method | Route | Body | Notes |
|---|---|---|---|
| POST | `/api/projects/{pid}/chunk` | multipart: `chunk`(blob), `chunkIndex`, `uploadId` | **5 MB per chunk, max 20 chunks (100 MB)** |
| POST | `/api/projects/{pid}/complete-upload` | `{"uploadId","fileName","fileType","fileSize","userId","tags":[],"parserOptions":{}}` | → `{"inserted":N,"skipped":M}` |
| GET | `/api/projects/{pid}/upload-history` | — | `[{id,file_name,citations_inserted,citations_skipped,user_email,reverted_at}]` |
| GET | `/api/projects/{pid}/upload-history/{upload_id}` | — | `{citations_in_upload,citations_with_activity,can_revert}` |
| DELETE | `/api/projects/{pid}/upload-history/{upload_id}` | — | **Leader only**; deletes that batch and its labels/tags/notes; irreversible |

Allowed extensions: `.txt .nbib .csv .ris .bib`.

**Use CSV** — see `PITFALLS.md` §2.

## 4. Citations

| Method | Route | Notes |
|---|---|---|
| GET | `/api/projects/{pid}/citations?page=N` | **`page` is 0-indexed**, default order **id DESC**, 100 rows/page |
| GET | `/api/projects/{pid}/citations_count` | integer |
| GET | `/api/citations/{cid}/batch` | full citation: `status`, `my_label`, `labels[]`, `tags[]`, `notes[]` |
| GET | `/api/citations/{cid}/batch?conflict_context=true` | includes other reviewers' decisions (used by conflict resolution) |

Extra list filters accepted by the client: `status`, `search`, `tag`, `note`,
`imported_after/before`, `labeled_after/before`, `labeled_by_user_id`,
`labeled_by_decision`, `probability_min/max`, `sort_field`, `set_id`,
`not_in_any_set`.

## 5. Screening decisions (labels)

| Method | Route | Body | Semantics |
|---|---|---|---|
| PUT | `/api/citations/{cid}/label` | `{"value":1\|-1\|0,"project_id":pid}` | `1`→`my_included`, `-1`→`my_excluded`, **`0`→`my_maybe`** |
| DELETE | `/api/citations/{cid}/label` | — | removes **your own** label |
| POST | `/api/projects/{pid}/bulk-labels` | `{"citation_ids":[N,…],"value":1\|-1\|0}` | 1000 ids in one call were accepted |
| DELETE | `/api/projects/{pid}/bulk-labels` | `{"citation_ids":[N,…]}` | undoes your own labels → back to `unscreened` |

## 6. Tags (use them for exclusion-reason codes)

| Method | Route | Body |
|---|---|---|
| POST | `/api/citations/{cid}/tags` | `{"name":"E1"}` |
| DELETE | `/api/citations/{cid}/tags?name=…` | — |
| GET | `/api/citations/{cid}/tags` | — |
| POST | `/api/projects/{pid}/bulk-tags` | `{"citation_ids":[…],"tag":"…","action":"add"\|"remove"}` |
| GET/POST/DELETE | `/api/projects/{pid}/project_tags` | `{"name":…}` |
| POST | `/api/projects/{pid}/delete-tag` | `{"name":…}` |
| POST | `/api/projects/{pid}/rename-tag` | `{"oldName","newName","force":false}` |
| GET | `/api/projects/{pid}/tag_conflict_count` | — |
| GET | `/api/projects/{pid}/user_unique_tags` | — |

CJK tag names and tag names up to 200 characters were both accepted.

## 7. Notes, resolution (conflict arbitration), terms

| Method | Route | Body |
|---|---|---|
| PATCH | `/api/citations/{cid}/notes` | `{"general","population","intervention_comparator","outcome"}` |
| GET | `/api/citations/{cid}/notes` | — |
| POST | `/api/citations/{cid}/resolution_labels` | `{"value":1\|-1}` |
| DELETE | `/api/citations/{cid}/resolution_labels` | — |
| POST/DELETE | `/api/citations/{cid}/resolution_tags/bulk` | `{"names":[…]}` |
| GET | `/api/citations/{cid}/resolution_tags` | — |
| GET/POST/PATCH/DELETE | `/api/projects/{pid}/terms`, `/project_terms`, `/terms/reorder` | term highlighting lists |

## 8. Screening queue, progress, PRISMA

| Method | Route | Returns |
|---|---|---|
| GET | `/api/projects/{pid}/next` | `{"citation_id":N\|null}` |
| GET | `/api/projects/{pid}/next?mode=conflict` | next unresolved conflict |
| GET | `/api/projects/{pid}/next_tag_conflict?after_id=N` | next tag conflict |
| GET | `/api/projects/{pid}/progress` | `{"total","screened","my_labeled"}` |
| GET | `/api/projects/{pid}/conflict_count` | `{"count":N}` |
| GET | `/api/projects/{pid}/rejection_tag_stats` | platform-side PRISMA: `screened[3]`, `included[3]`, `excluded[3]`, `resolved_*`, `excluded_tags[]` |
| GET | `/api/projects/{pid}/compare`, `/compare/{other_pid}` | project/agreement comparison |

The three-element arrays in `rejection_tag_stats` correspond to the
`≥1 screener`, `≥2 screeners`, `all screeners` thresholds. In **Double** mode a
citation only counts as screened after the second reviewer votes, which is why
`screened` reads 0 until the human pass is done.

## 9. Screening sets

| Method | Route | Body |
|---|---|---|
| GET/POST | `/api/projects/{pid}/screening_sets` | `{"name":"…"}` → **201** with the new set |
| PUT/DELETE | `/api/projects/{pid}/screening_sets/{set_id}` | `{"name":…}` / — |
| POST | `/api/projects/{pid}/screening_sets/{set_id}/seek` | `{"position":N}` |
| GET | `/api/projects/{pid}/screening_sets/{set_id}/next?advance=true` | — |
| GET/POST/DELETE | `/api/projects/{pid}/screening_sets/{set_id}/users[/{user_id}]` | assignments |

## 10. Duplicate detection

| Method | Route | Notes |
|---|---|---|
| POST | `/api/projects/{pid}/detect_duplicates` | async → `{"status":"started"}`; `429` rate limited, `422` project too large |
| GET | `/api/projects/{pid}/detect_duplicates` | `{"status":"running\|completed\|failed","progress","duplicates_found"}` |
| GET | `/api/projects/{pid}/duplicates?page=&search=&reviewStatus=` | `{duplicates,total,filteredCount,page,per_page}` |
| POST | `/api/projects/{pid}/duplicates/bulk-merge` | `{"selections":[{"pair_id","kept_citation_id","complement_fields"}]}` |
| POST | `/api/projects/{pid}/duplicates/bulk-dismiss` | `{"pair_ids":[…]}` |
| POST | `/api/projects/{pid}/duplicates/{pair_id}/merge\|dismiss\|undo` | single pair |
| POST | `/api/projects/{pid}/bulk-delete` | `{"citation_ids":[…]}` |

## 11. Export

| Method | Route | Notes |
|---|---|---|
| GET | `/api/projects/{pid}/export_project` | **ZIP (binary)** containing `<project name>.jsonl` |

Two preconditions, both enforced server-side:

* the project must be **unblinded** → otherwise `401 {"error":"Project must be unblinded for full project export."}`
* the caller must be a **Leader** → otherwise `403 {"error":"Forbidden. Only project leaders may perform this action."}`

### JSONL record shape

```json
{"id": 42, "title": "…", "abstract": "…", "authors": "…", "doi": "10.…", "pmid": "12345678",
 "status": "included|excluded|conflict|unscreened|…",
 "probability": 0.0,
 "labels": [{"id":1,"user_id":3,"email":"…","value":1,"created_at":"…","updated_at":"…"}],
 "resolution_labels": [{"user_id":3,"email":"…","value":1}],
 "tags": [{"name":"E1","user_id":3,"email":"…"}],
 "notes": [{"general":"…","population":"…","intervention_comparator":"…","outcome":"…","email":"…"}],
 "consolidated_tags": [], "tags_conflict": false, "tags_resolved": false,
 "upload_history_id": 1, "project_id": 42}
```

`labels[].value`: `1` include, `-1` exclude, `0` maybe.

---

## Permission matrix (measured)

| Action | Leader | Member |
|---|---|---|
| read project / citations / progress | ✅ | ✅ |
| submit labels, tags, notes | ✅ | ✅ |
| resolve conflicts | ✅ | ✅ |
| change screening mode / sort order | ✅ | ❌ |
| blind / unblind | ✅ | ❌ |
| add members | ✅ | ❌ |
| revert an upload | ✅ | ❌ |
| **full-project export** | ✅ | ❌ **403** |
| delete project | ✅ | ❌ |

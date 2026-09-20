"""abstrackr screening automation: import the pool, then submit the pass-1 labels.

PROTOCOL (read off the production bundles, see abstrackr/recon/upload_impl.txt)
    upload   POST /api/projects/{pid}/chunk            FormData: chunk, chunkIndex, uploadId
             POST /api/projects/{pid}/complete-upload  JSON: {uploadId, fileName, fileType,
                                                             fileSize, userId, tags, parserOptions}
             -> {inserted, skipped, ...}; chunks are 5 MB (max 20)
    read     GET  /api/projects/{pid}/citations?limit=&offset=
             GET  /api/projects/{pid}/citations_count
    label    POST /api/projects/{pid}/bulk-labels    {"citation_ids":[...], "value":1|-1|0}
             PUT  /api/citations/{cid}/label         {"value":1|-1|0, "project_id":pid}
    tags     POST /api/citations/{cid}/tags          {"name":"E1"}

SAFETY
    * Everything is read-only unless --execute is passed.
    * Writes are idempotent-ish and resumable: a state file records which of our
      records have already been submitted, so a rerun never double-submits.
    * One request at a time with a sleep; the API is someone's production service.
    * Every action is appended to a local audit log.

USAGE
    python abstrackr_screen.py count
    python abstrackr_screen.py import-ris ..\\abstrackr\\import_to_abstrackr.ris --execute
    python abstrackr_screen.py fetch-citations
    python abstrackr_screen.py map
    python abstrackr_screen.py submit --limit 5            # dry run over 5 records
    python abstrackr_screen.py submit --execute            # real submission
    python abstrackr_screen.py progress
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ABST = os.path.join(ROOT, "abstrackr")
S = os.path.join(ROOT, "screening")
COOKIES = os.path.join(ABST, ".session_cookies.txt")
COOKIES_BOT = os.path.join(ABST, ".session_cookies_bot.txt")
CRED_MAIN = os.path.join(ABST, "credentials.json")
CRED_BOT = os.path.join(ABST, "credentials_bot.json")
LOG = os.path.join(ABST, "screen_audit.log")
STATE = os.path.join(ABST, "submit_state.json")
CIT_CACHE = os.path.join(ABST, "citations_dump.json")
MAPPING = os.path.join(ABST, "citation_id_mapping.csv")

import http.cookiejar  # noqa: E402

BASE = "https://abstrackr.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
SLEEP = 1.0
CHUNK = 5 * 1024 * 1024


class Session:
    def __init__(self, cookie_file=None):
        self.jar_path = cookie_file or COOKIES
        self.jar = http.cookiejar.MozillaCookieJar(self.jar_path)
        if os.path.exists(self.jar_path):
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar))
        self._last = 0.0

    def save_cookies(self):
        try:
            self.jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:  # noqa: BLE001
            pass

    def _wait(self):
        dt = time.time() - self._last
        if dt < SLEEP:
            time.sleep(SLEEP - dt)
        self._last = time.time()

    def call(self, method, path, payload=None, form=None, raw=False, binary=False):
        url = path if path.startswith("http") else BASE + path
        headers = {"User-Agent": UA, "Origin": BASE, "Referer": BASE + "/",
                   "Accept": "application/json, */*"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            boundary, body = encode_multipart(form)
            data = body
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        self._wait()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=300) as r:
                body = r.read()
                st = r.status
        except urllib.error.HTTPError as e:
            body, st = e.read(), e.code
        except Exception as exc:  # noqa: BLE001
            return 0, f"{type(exc).__name__}: {exc}"
        if binary:
            # the full-project export is a ZIP; decoding it as text destroys it
            return st, body
        text = body.decode("utf-8", "replace")
        if raw:
            return st, text
        try:
            return st, json.loads(text)
        except Exception:  # noqa: BLE001
            return st, text


def encode_multipart(fields):
    """Minimal multipart/form-data encoder (fields: name -> (filename, bytes))."""
    boundary = "----dsh" + uuid.uuid4().hex
    out = []
    for name, value in fields.items():
        if isinstance(value, tuple):
            fname, content = value
            out.append(f"--{boundary}\r\n".encode())
            out.append(f'Content-Disposition: form-data; name="{name}"; '
                       f'filename="{fname}"\r\n'.encode())
            out.append(b"Content-Type: text/plain\r\n\r\n")
            out.append(content)
            out.append(b"\r\n")
        else:
            out.append(f"--{boundary}\r\n".encode())
            out.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            out.append(str(value).encode())
            out.append(b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(out)


def audit(line):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"submitted": {}}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


# --- commands ---------------------------------------------------------------
def cmd_login(args):
    """Log in as one of the two accounts and cache its own cookie jar."""
    bot = args.as_bot
    cred = CRED_BOT if bot else CRED_MAIN
    jar = COOKIES_BOT if bot else COOKIES
    if not os.path.exists(cred):
        print(f"[!] 缺凭据文件：{cred}")
        print("    bot 账号请把 email/password 写进 abstrackr/credentials_bot.json")
        return 2
    data = json.load(open(cred, encoding="utf-8"))
    email, password = data.get("email", ""), data.get("password", "")
    if not email or not password:
        print(f"[!] {cred} 里 email/password 为空")
        return 2
    s = Session(jar)
    st, body = s.call("POST", "/api/auth/web/login",
                      payload={"email": email, "password": password})
    errors = body.get("errors") if isinstance(body, dict) else body
    if st != 200 or errors:
        print(f"登录失败：HTTP {st} {errors}")
        return 1
    s.save_cookies()
    print(f"✅ 登录成功（{'bot 账号' if bot else '主账号'}） -> {jar}")
    st, me = s.call("GET", "/api/user")
    print(f"   身份：{me}")
    if args.project and args.project != "0":
        st, ms = s.call("GET", f"/api/projects/{args.project}/members")
        if isinstance(ms, list):
            for m in ms:
                role = "Leader" if m.get("leader") else "Member"
                print(f"   项目成员：{m.get('email')} ({role})")
    return 0


def cmd_whoami(args):
    s = Session(COOKIES_BOT if args.as_bot else COOKIES)
    st, me = s.call("GET", "/api/user")
    st2, prog = s.call("GET", f"/api/projects/{args.project}/progress")
    print(f"{'bot' if args.as_bot else 'main'} session: {st} {me}")
    print(f"   progress: {prog}")
    return 0


def cmd_unblind(args):
    """Disable blinding (required before the full-project export).

    PATCH /api/projects/{id}
        {"blinding":{"action":"unblinded","reason_category":<one of the 4 ui values>,
                     "reason_detail":optional,"duration_minutes":15|60|240|null}}
    Re-enable with {"blinding":{"action":"blinded"}}.
    """
    s = Session()
    payload = {"blinding": {"action": "unblinded",
                            "reason_category": args.reason_category}}
    if args.reason_detail:
        payload["blinding"]["reason_detail"] = args.reason_detail
    payload["blinding"]["duration_minutes"] = (None if args.duration == 0
                                               else args.duration)
    st, body = s.call("PATCH", f"/api/projects/{args.project}", payload=payload)
    print(f"PATCH /api/projects/{args.project} (unblind) -> {st} {body}")
    st, proj = s.call("GET", f"/api/projects/{args.project}")
    if isinstance(proj, dict):
        print(f"  blinding_enabled={proj.get('blinding_enabled')} "
              f"unblind_expires_at={proj.get('unblind_expires_at')}")
    audit(f"unblind status={st} category={args.reason_category}")
    return 0 if st in (200, 201) else 1


def cmd_reblind(args):
    s = Session()
    st, body = s.call("PATCH", f"/api/projects/{args.project}",
                      payload={"blinding": {"action": "blinded"}})
    print(f"PATCH /api/projects/{args.project} (reblind) -> {st} {body}")
    audit(f"reblind status={st}")
    return 0 if st in (200, 201) else 1


def cmd_mode(args):
    """Set the screening mode (PATCH /api/projects/{id} {screening_mode})."""
    s = Session()
    st, body = s.call("PATCH", f"/api/projects/{args.project}",
                      payload={"screening_mode": args.value})
    print(f"PATCH /api/projects/{args.project} screening_mode={args.value} -> {st} {body}")
    st, proj = s.call("GET", f"/api/projects/{args.project}")
    if isinstance(proj, dict):
        print(f"  screening_mode now = {proj.get('screening_mode')}")
    audit(f"screening_mode={args.value} status={st}")
    return 0 if st in (200, 201) else 1


def cmd_export(args):
    """Download the Full Project JSONL export (ZIP) and optionally parse it."""
    s = Session()
    st, body = s.call("GET", f"/api/projects/{args.project}/export_project", binary=True)
    if st != 200 or not isinstance(body, bytes):
        print(f"导出失败：HTTP {st} {str(body)[:200]}")
        print("提示：导出要求项目处于**非盲法**状态——先跑 `unblind`。")
        return 1
    out = args.out or os.path.join(ABST, f"project_{args.project}_export.zip")
    with open(out, "wb") as fh:
        fh.write(body)
    print(f"export -> {st}, {len(body):,} bytes -> {out}")
    if body[:2] == b"PK":
        import zipfile
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            print(f"  zip contents: {names}")
            jsonl = [n for n in names if n.lower().endswith(".jsonl")]
            if jsonl:
                dest = os.path.join(ABST, os.path.basename(jsonl[0]))
                with z.open(jsonl[0]) as src, open(dest, "wb") as fh:
                    fh.write(src.read())
                n = sum(1 for _ in open(dest, encoding="utf-8"))
                print(f"  extracted -> {dest} ({n} records)")
                if args.report:
                    import subprocess
                    rp = os.path.join(HERE, "abstrackr_jsonl_report.py")
                    subprocess.run([sys.executable, rp, dest, "--outdir", ABST])
    audit(f"export status={st} bytes={len(body)}")
    return 0


def cmd_count(args):
    s = Session()
    st, body = s.call("GET", f"/api/projects/{args.project}/citations_count")
    print(f"citations_count -> {st}: {body}")
    st, body = s.call("GET", f"/api/projects/{args.project}/progress")
    print(f"progress -> {st}: {json.dumps(body, ensure_ascii=False)[:600] if not isinstance(body, str) else body[:300]}")
    return 0


def cmd_import_ris(args):
    path = os.path.abspath(args.path)
    size = os.path.getsize(path)
    upload_id = str(uuid.uuid4())
    n_chunks = max(1, -(-size // CHUNK))
    print(f"file      : {path}")
    print(f"size      : {size:,} bytes -> {n_chunks} chunk(s) of 5 MB")
    if not args.execute:
        print("(dry run) add --execute to actually upload.")
        return 0
    s = Session()
    with open(path, "rb") as fh:
        for i in range(n_chunks):
            fh.seek(i * CHUNK)
            blob = fh.read(CHUNK)
            st, body = s.call("POST", f"/api/projects/{args.project}/chunk",
                              form={"chunk": (os.path.basename(path), blob),
                                    "chunkIndex": str(i),
                                    "uploadId": upload_id})
            print(f"  chunk {i + 1}/{n_chunks} -> {st} "
                  f"{json.dumps(body, ensure_ascii=False)[:160] if not isinstance(body, str) else body[:160]}")
            audit(f"chunk {i + 1}/{n_chunks} status={st}")
            if st not in (200, 201):
                print("  !! chunk upload failed, aborting")
                return 1
    st, body = s.call("POST", f"/api/projects/{args.project}/complete-upload",
                      payload={"uploadId": upload_id,
                               "fileName": os.path.basename(path),
                               "fileType": {".csv": "text/csv", ".ris": "text/plain",
                                            ".txt": "text/plain", ".bib": "text/plain",
                                            ".nbib": "text/plain"}.get(
                                   os.path.splitext(path)[1].lower(), "text/plain"),
                               "fileSize": size,
                               "userId": args.user_id,
                               "tags": [],
                               "parserOptions": {}})
    print(f"complete-upload -> {st}")
    print(json.dumps(body, ensure_ascii=False, indent=1)[:800]
          if not isinstance(body, str) else body[:800])
    audit(f"complete-upload status={st} body={str(body)[:200]}")
    return 0


def cmd_fetch_citations(args):
    s = Session()
    out, page = [], 0
    # `page` is 0-INDEXED and the default order is id DESCENDING. Starting at 1
    # silently drops the first 100 rows -- that mistake cost us a 100-record gap
    # on the real project before it was caught.
    while len(out) < args.max:
        st, body = s.call("GET", f"/api/projects/{args.project}/citations?page={page}")
        if st != 200 or not isinstance(body, dict):
            print(f"  fetch failed at page {page}: {st} {str(body)[:200]}")
            break
        batch = body.get("citations") or []
        out.extend(batch)
        print(f"  page {page}: +{len(batch)} (total {len(out)})")
        if len(batch) < 100:
            break
        page += 1
    with open(CIT_CACHE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"written: {CIT_CACHE} ({len(out)} citations)")
    if out:
        print("first citation keys:", sorted(out[0].keys()))
    return 0


def sanitise(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())[:120]


def cmd_map(args):
    """Map our workbook rows to abstrackr citation ids.

    Two routes, because the listing endpoint is unreliable at scale:
      1. identifier match (PMID -> DOI -> normalised title) against the listed
         citations;
      2. for whatever is left, the listing has simply hidden a tail block (its
         default order is by ML probability, and every probability is tied, so
         paging drops records). Import assigns ids in file order, so the hidden
         ids are contiguous right after the highest listed id -- but every one of
         them is verified by reading the citation back and comparing titles.
    """
    if not os.path.exists(CIT_CACHE):
        print("run fetch-citations first")
        return 1
    cits = json.load(open(CIT_CACHE, encoding="utf-8"))
    with open(os.path.join(S, "screening_decisions.csv"), encoding="utf-8-sig",
              newline="") as fh:
        ours = list(csv.DictReader(fh))

    by_pmid, by_doi, by_title = {}, {}, {}
    for c in cits:
        p = str(c.get("pmid") or "").strip()
        d = (c.get("doi") or "").strip().lower()
        t = sanitise(c.get("title"))
        if p:
            by_pmid[p] = c
        if d:
            by_doi[d] = c
        if t:
            by_title[t] = c

    rows, missing = [], []
    for o in ours:
        c, how = None, ""
        if o["pmid"].strip() and o["pmid"].strip() in by_pmid:
            c, how = by_pmid[o["pmid"].strip()], "pmid"
        elif o["doi"].strip().lower() and o["doi"].strip().lower() in by_doi:
            c, how = by_doi[o["doi"].strip().lower()], "doi"
        elif sanitise(o["title"]) in by_title:
            c, how = by_title[sanitise(o["title"])], "title"
        if c:
            rows.append({"idx": o["idx"], "citation_id": c.get("id"),
                         "matched_on": how, "decision": o["decision"],
                         "code": o["code"], "pmid": o["pmid"], "title": o["title"]})
        else:
            missing.append(o)

    print(f"identifier match: {len(rows)} / {len(ours)}   still missing: {len(missing)}")

    if missing:
        s = Session()
        start = max(int(c["id"]) for c in cits) + 1
        print(f"probing hidden ids {start}..{start + len(missing) - 1} "
              f"and verifying by title ...")
        recovered = 0
        for k, o in enumerate(sorted(missing, key=lambda x: int(x["idx"]))):
            cid = start + k
            st, body = s.call("GET", f"/api/citations/{cid}/batch")
            cit = (body.get("citation") or body) if isinstance(body, dict) else {}
            if sanitise(cit.get("title")) == sanitise(o["title"]):
                rows.append({"idx": o["idx"], "citation_id": cid,
                             "matched_on": "hidden-id+title", "decision": o["decision"],
                             "code": o["code"], "pmid": o["pmid"], "title": o["title"]})
                recovered += 1
            else:
                print(f"  !! id {cid} title mismatch, leaving idx {o['idx']} unmapped")
        print(f"recovered {recovered} / {len(missing)} by hidden-id verification")

    rows.sort(key=lambda r: int(r["idx"]))
    with open(MAPPING, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["idx", "citation_id", "matched_on",
                                           "decision", "code", "pmid", "title"])
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print(f"mapped {len(rows)} / {len(ours)} -> {MAPPING}")
    print("match routes:", dict(Counter(r["matched_on"] for r in rows)))
    unmapped = [o for o in ours if o["idx"] not in {r["idx"] for r in rows}]
    for m in unmapped[:10]:
        print(f"  UNMAPPED idx={m['idx']} pmid={m['pmid']} {m['title'][:70]}")
    return 0


VALUE = {"F": 1, "M": 1, "X": -1, "R": -1}


def cmd_submit(args):
    if not os.path.exists(MAPPING):
        print("run map first")
        return 1
    rows = list(csv.DictReader(open(MAPPING, encoding="utf-8-sig")))
    state = load_state()
    todo = [r for r in rows if r["idx"] not in state["submitted"]]
    if args.limit:
        todo = todo[:args.limit]
    by_value = {}
    for r in todo:
        by_value.setdefault(VALUE.get(r["decision"], 0), []).append(int(r["citation_id"]))
    print(f"to submit: {len(todo)} records "
          f"({ {k: len(v) for k, v in by_value.items()} } by label value)")
    if not args.execute:
        print("(dry run) add --execute to submit.")
        return 0
    s = Session()
    n = 0
    for value, ids in by_value.items():
        for i in range(0, len(ids), args.batch):
            chunk = ids[i:i + args.batch]
            st, body = s.call("POST", f"/api/projects/{args.project}/bulk-labels",
                              payload={"citation_ids": chunk, "value": value})
            ok = st in (200, 201)
            print(f"  bulk-labels value={value} n={len(chunk)} -> {st} "
                  f"{'' if ok else str(body)[:160]}")
            audit(f"bulk-labels value={value} n={len(chunk)} status={st}")
            if ok:
                for cid in chunk:
                    for r in rows:
                        if int(r["citation_id"]) == cid:
                            state["submitted"][r["idx"]] = value
                            break
                save_state(state)
                n += len(chunk)
    print(f"submitted {n} labels; state -> {STATE}")
    return 0


def cmd_progress(args):
    s = Session()
    for path in (f"/api/projects/{args.project}/citations_count",
                 f"/api/projects/{args.project}/progress",
                 f"/api/projects/{args.project}/conflict_count"):
        st, body = s.call("GET", path)
        txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        print(f"{path} -> {st}: {txt[:400]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("ABSTRACKR_PROJECT", ""),
                    help="abstrackr project id (or set ABSTRACKR_PROJECT)")
    ap.add_argument("--user-id", default="5557")
    ap.add_argument("--as-bot", action="store_true",
                    help="use the bot account (credentials_bot.json + its own cookie jar)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("whoami")
    sub.add_parser("count")
    sub.add_parser("progress")
    sub.add_parser("fetch-citations").add_argument("--max", type=int, default=20000)
    sub.add_parser("map")
    i = sub.add_parser("import-ris"); i.add_argument("path"); i.add_argument("--execute", action="store_true")
    sb = sub.add_parser("submit")
    sb.add_argument("--execute", action="store_true")
    sb.add_argument("--limit", type=int, default=0)
    sb.add_argument("--batch", type=int, default=100)
    u = sub.add_parser("unblind")
    u.add_argument("--reason-category", default="Technical Troubleshooting",
                   choices=["Conflict Resolution", "QC Audit",
                            "Technical Troubleshooting", "Other"])
    u.add_argument("--reason-detail", default="")
    u.add_argument("--duration", type=int, default=15,
                   help="minutes; 0 = indefinite")
    sub.add_parser("reblind")
    md = sub.add_parser("mode"); md.add_argument("value")
    ex = sub.add_parser("export")
    ex.add_argument("--out", default="")
    ex.add_argument("--report", action="store_true",
                    help="also run abstrackr_jsonl_report.py on the extracted jsonl")
    args = ap.parse_args()

    # route the session to the right account BEFORE any command builds one
    global COOKIES
    if getattr(args, "as_bot", False):
        COOKIES = COOKIES_BOT
        if args.user_id == "5557":
            args.user_id = "5574"

    return {"login": cmd_login, "whoami": cmd_whoami, "count": cmd_count,
            "progress": cmd_progress,
            "fetch-citations": cmd_fetch_citations, "map": cmd_map,
            "import-ris": cmd_import_ris, "submit": cmd_submit,
            "unblind": cmd_unblind, "reblind": cmd_reblind, "mode": cmd_mode,
            "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

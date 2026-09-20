"""Which RIS tag populates abstrackr's `pmid` field?

We know `AN  - x` lands in `accession_number`, not `pmid`, and that abstrackr's
cross-upload de-duplication silently failed as a result. This probe imports one
record per candidate tag into a throwaway project and reads back what each stored.

Also checks whether de-duplication happens WITHIN a single upload (same DOI twice).
"""

import importlib.util
import os
import sys
import time
import uuid

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # toolkit root (probes/ lives one level down)
spec = importlib.util.spec_from_file_location("scr", os.path.join(ROOT, "abstrackr_screen.py"))
scr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scr)

PMIDS = ["11111111", "22222222", "33333333", "44444444", "55555555", "66666666", "77777777"]

# one candidate tag per record
VARIANTS = [
    ("AN", "AN  - "),
    ("PMID", "PMID- "),
    ("M1", "M1  - "),
    ("N1", "N1  - "),
    ("ID", "ID  - "),
    ("UR-pubmed", "UR  - https://pubmed.ncbi.nlm.nih.gov/"),
    ("KW", "KW  - pmid:"),
]


def build():
    out = []
    for (name, tag), pmid in zip(VARIANTS, PMIDS):
        body = tag + pmid + "\n"
        out.append(
            "TY  - JOUR\n"
            f"TI  - TAG PROBE {name} field mapping test\n"
            "AU  - Probe P\nJO  - J Probe\nPY  - 2026\n"
            f"DO  - 10.9999/probe.{name.lower()}\n"
            + body +
            "AB  - probe abstract.\nER  - \n\n")
    # plus a deliberate in-file duplicate DOI
    out.append(
        "TY  - JOUR\nTI  - TAG PROBE duplicate-doi copy one\nAU  - Probe P\n"
        "JO  - J Probe\nPY  - 2026\nDO  - 10.9999/probe.dup\nAN  - 88888881\nER  - \n\n"
        "TY  - JOUR\nTI  - TAG PROBE duplicate-doi copy two\nAU  - Probe P\n"
        "JO  - J Probe\nPY  - 2026\nDO  - 10.9999/probe.dup\nAN  - 88888882\nER  - \n\n")
    return "".join(out)


def main():
    s = scr.Session(scr.COOKIES_BOT)
    st, me = s.call("GET", "/api/user")
    name = f"DSH-TAGPROBE-{time.strftime('%H%M%S')}"
    st, proj = s.call("POST", "/api/projects", payload={"name": name, "description": "tag probe"})
    pid = (proj or {}).get("id")
    print(f"probe project {pid}")

    ris = build()
    uid = str(uuid.uuid4())
    s.call("POST", f"/api/projects/{pid}/chunk",
           form={"chunk": ("probe.ris", ris.encode()), "chunkIndex": "0", "uploadId": uid})
    st, body = s.call("POST", f"/api/projects/{pid}/complete-upload",
                      payload={"uploadId": uid, "fileName": "probe.ris",
                               "fileType": "text/plain", "fileSize": len(ris),
                               "userId": str(me["id"]), "tags": [], "parserOptions": {}})
    print(f"import -> {st} {body}   (in-file duplicate DOI test included)")

    st, cnt = s.call("GET", f"/api/projects/{pid}/citations_count")
    print(f"citations stored = {cnt}  (9 records fed: 7 tag probes + 2 same-DOI)")

    st, page = s.call("GET", f"/api/projects/{pid}/citations?page=0")
    cits = (page or {}).get("citations") or []
    print("\n== what each tag populated ==")
    for c in sorted(cits, key=lambda x: str(x.get("title"))):
        print(f"  {str(c.get('title'))[:52]:<54} pmid={c.get('pmid')!r:<12} "
              f"acc={c.get('accession_number')!r:<12} doi={c.get('doi')!r}")

    st, _ = s.call("DELETE", f"/api/projects/{pid}")
    print(f"\nprobe project deleted -> {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

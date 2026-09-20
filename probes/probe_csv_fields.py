"""Does the CSV importer populate `pmid` correctly (unlike the RIS importer)?"""

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

CSV = (
    "pmid,title,abstract,authors,journal,publication_year,doi\n"
    "11111111,CSV PROBE alpha cholecystectomy cholangiocarcinoma,alpha abstract,"
    "Probe A,\"J Probe\",2026,10.9999/csv.a\n"
    "22222222,CSV PROBE beta gallstones bile duct cancer,beta abstract,"
    "Probe B,\"J Probe\",2026,10.9999/csv.b\n"
)


def main():
    s = scr.Session(scr.COOKIES_BOT)
    st, me = s.call("GET", "/api/user")
    name = f"DSH-CSVPROBE-{time.strftime('%H%M%S')}"
    st, proj = s.call("POST", "/api/projects", payload={"name": name, "description": "csv probe"})
    pid = (proj or {}).get("id")
    print(f"probe project {pid}")

    uid = str(uuid.uuid4())
    st, body = s.call("POST", f"/api/projects/{pid}/chunk",
                      form={"chunk": ("probe.csv", CSV.encode()), "chunkIndex": "0",
                            "uploadId": uid})
    print(f"chunk -> {st} {body}")
    st, body = s.call("POST", f"/api/projects/{pid}/complete-upload",
                      payload={"uploadId": uid, "fileName": "probe.csv",
                               "fileType": "text/csv", "fileSize": len(CSV),
                               "userId": str(me["id"]), "tags": [], "parserOptions": {}})
    print(f"complete-upload -> {st} {body}")

    st, page = s.call("GET", f"/api/projects/{pid}/citations?page=0")
    for c in ((page or {}).get("citations") or []):
        print(f"  {str(c.get('title'))[:46]:<48} pmid={c.get('pmid')!r:<12} "
              f"acc={c.get('accession_number')!r:<12} doi={c.get('doi')!r:<18} "
              f"authors={str(c.get('authors'))[:22]!r} year={c.get('publication_year')!r}")

    st, _ = s.call("DELETE", f"/api/projects/{pid}")
    print(f"deleted -> {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

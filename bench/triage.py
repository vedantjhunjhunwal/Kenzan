"""Triage hand-downloaded EDINET filings into dev and a SEALED test set, before anyone looks at them.

For each XBRL zip in <downloads>, only XbrlSearchDlInfo.csv (docID, EDINET code, filer name) is
read. The locked split (bench/split.py, incl. forced_dev.txt) decides:
  dev  -> copied to <out>/dev/<docID>/          (open for development)
  test -> moved to  <out>/sealed/<docID>/       (not opened; sha256 of every file recorded)
The PDF is paired by docID in its file name, or as the only PDF next to the zip.

Sealed files are evaluated exactly once, by bench/eval_real.py, with frozen code whose fingerprint
is logged in bench/FREEZE_LOG.txt first. Raw filings are never committed (see .gitignore).

usage: python bench/triage.py <downloads_dir> <out_dir>    # default out_dir: realdocs
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch_edinet import pair_local  # noqa: E402
from split import load_forced_dev, split_of  # noqa: E402

SEALED_RECORD = HERE / "SEALED_TEST.json"
FREEZE_LOG = HERE / "FREEZE_LOG.txt"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def triage(downloads: str, out: str = "realdocs") -> dict:
    src, dst = Path(downloads), Path(out)
    pairs, notes = pair_local(src)
    forced = load_forced_dev()
    record = json.loads(SEALED_RECORD.read_text(encoding="utf-8")) if SEALED_RECORD.exists() else {"sealed": {}}
    summary = {"dev": [], "test_sealed": [], "notes": notes}
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for z, pdf, ident in pairs:
        code, did = (ident["edinetCode"] or "").upper(), ident["docID"]
        if not code:
            summary["notes"].append(f"{z.name}: no EDINET code in XbrlSearchDlInfo.csv; left in place")
            continue
        side = split_of(code, forced)
        target = dst / ("dev" if side == "dev" else "sealed") / did
        target.mkdir(parents=True, exist_ok=True)
        files = [z] + ([pdf] if pdf else [])
        moved = []
        for f in files:
            name = f"{did}.pdf" if f.suffix.lower() == ".pdf" else f"{did}_xbrl.zip"
            if side == "dev":
                shutil.copy2(f, target / name)
            else:
                shutil.move(str(f), target / name)       # out of the downloads folder: nothing else reads it
            moved.append(target / name)
        entry = {"docID": did, "edinetCode": code, "filer": ident["filerName"], "has_pdf": pdf is not None}
        if side == "dev":
            summary["dev"].append(entry)
        else:
            entry |= {"sha256": {p.name: sha(p) for p in moved}, "sealed_at": stamp}
            record["sealed"][did] = entry
            summary["test_sealed"].append({k: entry[k] for k in ("docID", "edinetCode", "has_pdf")})
    SEALED_RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    if summary["test_sealed"]:
        with open(FREEZE_LOG, "a", encoding="utf-8") as f:
            for e in summary["test_sealed"]:
                h = record["sealed"][e["docID"]]["sha256"]
                f.write(f"SEALED {stamp}  {e['docID']} {e['edinetCode']}  "
                        + "  ".join(f"{k} sha256 {v[:16]}" for k, v in h.items()) + "  (not opened)\n")
    return summary


if __name__ == "__main__":
    print(json.dumps(triage(*sys.argv[1:3]), ensure_ascii=False, indent=1))

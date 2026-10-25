"""Triage hand-downloaded EDINET filings into dev and a SEALED test set, before anyone looks at them.

For each XBRL zip in <downloads>, only XbrlSearchDlInfo.csv (docID, EDINET code, filer name) is
read. The locked split (bench/split.py, incl. forced_dev.txt) decides:
  dev  -> copied to <out>/dev/<docID>/          (open for development)
  test -> moved to  <out>/sealed/<docID>/       (no person opens it; sha256 of every file recorded)
The PDF is paired by docID in its file name, or as the only PDF next to the zip.

After moving, each filing's METADATA is read automatically (filing_metadata): accounting standard
and unit from the XBRL; balance-sheet page count and whether liabilities/equity sit on a page of
their own from the PDF's text structure. Only these fields are recorded and printed, never a label,
a value or an error message from a sealed file, so no person sees a sealed page. A coverage table
then says whether the sealed set includes IFRS, 千円, a two-page balance sheet, and
liabilities/equity on their own page.

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
import pymupdf  # noqa: E402
from fetch_edinet import end_total_texts, extract_xbrl, find_bs_pages, nfkc_nospace, pair_local  # noqa: E402
from split import load_forced_dev, split_of  # noqa: E402

SEALED_RECORD = HERE / "SEALED_TEST.json"
FREEZE_LOG = HERE / "FREEZE_LOG.txt"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


UNITS = {0: "円", 3: "千円", 6: "百万円"}
ASSET_MARKERS = ("資産の部", "流動資産")                       # labels of the assets side
LIAB_MARKERS = ("負債の部", "流動負債", "非流動負債", "負債及び資本", "負債及び純資産")


def filing_metadata(zip_path: Path, pdf_path: Path | None) -> dict:
    """Format metadata only. Errors are recorded by exception TYPE, never by message (a message
    could quote file content)."""
    m = {"standard": None, "unit": None, "bs_in_xbrl": False, "bs_pages": None,
         "liabilities_equity_own_page": None, "pdf_status": "no_pdf" if pdf_path is None else None}
    try:
        x = extract_xbrl(zip_path)
    except Exception as e:                                  # noqa: BLE001
        m["error"] = f"xbrl:{type(e).__name__}"
        return m
    m["standard"] = x["standard"]
    if x["bs"] is None:
        m["pdf_status"] = m["pdf_status"] or "skipped_no_bs_in_xbrl"
        return m
    m["bs_in_xbrl"] = True
    m["unit"] = UNITS.get(x["bs"]["scale"], f"scale{x['bs']['scale']}")
    if pdf_path is None:
        return m
    try:
        rng, why, _ = find_bs_pages(pdf_path, x["standard"], end_total_texts(x["bs"]))
        if rng is None:
            m["pdf_status"] = why
            return m
        s, e = rng
        texts = [nfkc_nospace(pymupdf.open(pdf_path)[i].get_text()) for i in range(s, e + 1)]
        m["bs_pages"] = e - s + 1
        # own page = some balance-sheet page shows liabilities/equity labels and no assets-side label
        m["liabilities_equity_own_page"] = any(any(k in t for k in LIAB_MARKERS) and
                                               not any(k in t for k in ASSET_MARKERS) for t in texts)
        m["pdf_status"] = "ok"
    except Exception as e:                                  # noqa: BLE001
        m["pdf_status"] = f"error:{type(e).__name__}"
    return m


COVERAGE = {
    "IFRS": lambda m: m.get("standard") == "ifrs",
    "千円": lambda m: m.get("unit") == "千円",
    "two-page balance sheet": lambda m: (m.get("bs_pages") or 0) >= 2,
    "liabilities/equity on own page": lambda m: m.get("liabilities_equity_own_page") is True,
}


def coverage(entries: list[dict]) -> dict:
    """Which sealed filings cover each format stratum (docIDs only)."""
    return {k: [e["docID"] for e in entries if f(e.get("metadata") or {})] for k, f in COVERAGE.items()}


def coverage_table(entries: list[dict]) -> str:
    cov = coverage(entries)
    lines = [f"Coverage of the sealed test set ({len(entries)} filings):", "| Stratum | Covered | Filings |",
             "|---|---|---|"]
    for k, ids in cov.items():
        lines.append(f"| {k} | {'yes' if ids else '**no**'} | {', '.join(ids) or '-'} |")
    missing = [k for k, ids in cov.items() if not ids]
    unknown = [e["docID"] for e in entries if (e.get("metadata") or {}).get("pdf_status") not in ("ok",)]
    if unknown:
        lines.append(f"PDF structure not read for: {', '.join(unknown)} (see metadata.pdf_status)")
    if missing:
        lines.append("Missing: " + ", ".join(missing) + ". Propose extra filings chosen by split only: "
                     "`python bench/split.py which <EDINET codes>`, keep those that land in test, and do not "
                     "open them before triage.")
    return "\n".join(lines)


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
        zip_t = next(p for p in moved if p.suffix == ".zip")
        pdf_t = next((p for p in moved if p.suffix.lower() == ".pdf"), None)
        entry["metadata"] = filing_metadata(zip_t, pdf_t)      # automatic; format fields only
        if side == "dev":
            summary["dev"].append(entry)
        else:
            entry |= {"sha256": {p.name: sha(p) for p in moved}, "sealed_at": stamp}
            record["sealed"][did] = entry
            summary["test_sealed"].append({k: entry[k] for k in ("docID", "edinetCode", "has_pdf", "metadata")})
    SEALED_RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    if summary["test_sealed"]:
        with open(FREEZE_LOG, "a", encoding="utf-8") as f:
            for e in summary["test_sealed"]:
                h = record["sealed"][e["docID"]]["sha256"]
                f.write(f"SEALED {stamp}  {e['docID']} {e['edinetCode']}  "
                        + "  ".join(f"{k} sha256 {v[:16]}" for k, v in h.items())
                        + "  metadata read automatically, no page viewed\n")
    summary["coverage"] = coverage(summary["test_sealed"])
    return summary


if __name__ == "__main__":
    res = triage(*sys.argv[1:3])
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(coverage_table(res["test_sealed"]))

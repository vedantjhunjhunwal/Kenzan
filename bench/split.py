"""JP-BS-Bench dev/test split. Locked: see split_spec.json and FREEZE_LOG.txt.

Rule: a company listed in forced_dev.txt is always 'dev' (pilot filings and anything inspected
before the test freeze); otherwise the salted sha256 of its EDINET code decides, independent of
any document content. Splitting by company keeps two filings of one company on the same side.

usage:
  python bench/split.py annotate <manifest.csv> [<out.csv>]       # adds a 'split' column
  python bench/split.py force-dev <forced_dev.txt from a run>      # merges a pilot's list into bench/forced_dev.txt
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / "split_spec.json").read_text(encoding="utf-8"))
FORCED_DEV_FILE = HERE / "forced_dev.txt"


def load_forced_dev(path: Path | None = None) -> set[str]:
    """Lines 'edinetCode[,docID]'; '#' starts a comment. Default: bench/forced_dev.txt (looked up at
    call time, not import time)."""
    path = Path(path) if path is not None else FORCED_DEV_FILE
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line.split(",")[0].strip().upper())
    return out


def hash_split(edinet_code: str) -> str:
    code = edinet_code.strip().upper()
    if not code:
        raise ValueError("empty EDINET code")
    u = int(hashlib.sha256(f"{SPEC['salt']}:{code}".encode()).hexdigest()[:8], 16) / 2**32
    return "test" if u < SPEC["test_fraction"] else "dev"


def split_of(edinet_code: str, forced_dev: set[str] | None = None) -> str:
    forced = load_forced_dev() if forced_dev is None else forced_dev
    return "dev" if edinet_code.strip().upper() in forced else hash_split(edinet_code)


def annotate(manifest: str, out: str | None = None) -> dict:
    forced = load_forced_dev()
    with open(manifest, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["split"] = split_of(r["edinetCode"], forced)
        r["split_reason"] = "forced_dev" if r["edinetCode"].strip().upper() in forced else "hash"
    with open(out or manifest, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["edinetCode", "split"])
        w.writeheader()
        w.writerows(rows)
    return {s: sum(r["split"] == s for r in rows) for s in ("dev", "test")} | \
        {"forced_dev": sum(r["split_reason"] == "forced_dev" for r in rows)}


def force_dev(run_list: str) -> int:
    """Merge a run's forced_dev.txt into bench/forced_dev.txt (append-only)."""
    have = FORCED_DEV_FILE.read_text(encoding="utf-8") if FORCED_DEV_FILE.exists() else \
        "# Companies forced into dev (pilot filings / inspected before the test freeze). Append-only.\n"
    known = load_forced_dev()
    added = 0
    for line in Path(run_list).read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].split(",")[0].strip().upper()
        if code and code not in known:
            have += line.strip() + "\n"
            known.add(code)
            added += 1
    FORCED_DEV_FILE.write_text(have, encoding="utf-8")
    return added


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    print(annotate(*args) if cmd == "annotate" else force_dev(*args))

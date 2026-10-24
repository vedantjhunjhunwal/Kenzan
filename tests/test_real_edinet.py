"""Regression tests on a real EDINET filing: Nintendo 有価証券報告書, FY ending 2026-03-31 (docID S100Y9NX),
XBRL zip as downloaded from the public EDINET site (XbrlSearchDlInfo.csv + S100Y9NX/XBRL/PublicDoc/...).
Bugs these pin down (all missed by synthetic tests): 2008 inline-XBRL namespace, indentation by leading
empty columns (colspan), note markers inside value cells (「※1 65,180」)."""
import csv
import json
import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import build_gt_xbrl as gtb  # noqa: E402
import fetch_edinet as fe  # noqa: E402

ZIP = ROOT / "tests" / "fixtures" / "edinet" / "S100Y9NX_xbrl.zip"
pytestmark = pytest.mark.skipif(not ZIP.exists(), reason="real EDINET fixture missing")


@pytest.fixture(scope="module")
def x():
    return fe.extract_xbrl(ZIP)


def _rows(bs):
    return [r for tb in bs["tables"] for r in tb["rows"]]


def test_2008_namespace_facts_found(x):
    bs = x["bs"]
    assert x["standard"] == "jgaap"
    assert bs["found_by"] == "heading" and bs["n_consolidated_facts"] == 88
    assert bs["scale"] == 6 and bs["instants"] == ["2025-03-31", "2026-03-31"] and len(bs["tables"]) == 2
    assert x["dei"]["EDINETCodeDEI"] == "E02367"


def test_label_is_first_nonempty_cell_and_level_is_leading_columns(x):
    by_label = {r["label"]: r for r in _rows(x["bs"]) if r["label"]}
    assert by_label["資産の部"]["lead_cols"] == 0
    assert by_label["流動資産"]["lead_cols"] == 1
    assert by_label["現金及び預金"]["lead_cols"] == 2
    assert by_label["建物及び構築物（純額）"]["lead_cols"] == 3
    assert by_label["資産合計"]["lead_cols"] == 1 and by_label["負債純資産合計"]["lead_cols"] == 0
    assert _rows(x["bs"])[3]["cells"][0]["text"] == ""          # cells[0] is an empty indent column


def test_note_markers_split_from_values(x):
    r = next(r for r in _rows(x["bs"]) if r["label"] == "受取手形及び売掛金")
    vals = [(c["value_text"], c["notes"], c["text"]) for c in r["cells"] if c["facts"]]
    assert vals == [("65,180", ["※1"], "※1 65,180"), ("147,485", ["※1"], "※1 147,485")]
    assert fe.split_notes("※2,※3 1,234") == ("1,234", ["※2", "※3"])
    assert fe.split_notes("※１、２ 9,876") == ("9,876", ["※1", "※2"])


def _gt(tmp_path, x):
    d = tmp_path / "S100Y9NX"
    d.mkdir()
    (d / "ixbrl_bs.json").write_text(json.dumps(x["bs"], ensure_ascii=False), encoding="utf-8")
    (d / "cal_bs.json").write_text(json.dumps(x["cal"], ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"docID": "S100Y9NX", "standard": "jgaap", "unit": "百万円"}), encoding="utf-8")
    return gtb.build_filing(d)


def test_ground_truth_from_real_filing(tmp_path, x):
    doc, st = _gt(tmp_path, x)
    assert (st["rows"], st["value_rows"], st["cells"]) == (56, 44, 88)
    assert st["xbrl_value_mismatch"] == 0 and st["untagged_cells"] == 0
    assert st["calc_parent_from_cal"] == 42 and st["presentation_levels"] == 4
    s = doc["check_summary"]
    assert s["template"]["checks"] == 26 and s["template"]["failed"] == 0 and s["coverage"] == 1.0
    assert {k: v for k, v in st.items() if k.startswith("conv:")} == \
        {"conv:dash_tagged_nil": 1, "conv:positive": 83, "conv:printed_negative_xbrl_negative": 4}
    rows = {r["label_path"]: r for r in doc["rows"]}
    by_id = {r["row_id"]: r for r in doc["rows"]}
    ar = rows["資産の部/流動資産/受取手形及び売掛金"]
    assert ar["cells"]["prior"]["reported_value"] == 65180 and ar["cells"]["prior"]["footnote_refs"] == ["※1"]
    assert by_id[rows["資産の部/流動資産/現金及び預金"]["calc_parent"]]["label_ja"] == "流動資産合計"
    tre = rows["純資産の部/株主資本/自己株式"]["cells"]["current"]
    assert tre["reported_value"] == -341854 and tre["xbrl_value"] == -341_854_000_000
    nil = rows["負債の部/固定負債/取締役報酬引当金"]["cells"]
    assert nil["prior"]["reported_value"] == 4 and nil["current"]["is_nil"]
    assert rows["負債純資産合計"]["row_type"] == "total" and rows["資産の部/資産合計"]["row_type"] == "total"


def test_local_mode_without_and_with_missing_pdf_flag(tmp_path):
    src = tmp_path / "downloads"
    src.mkdir()
    (src / "Xbrl_Search_20261025_032117.zip").write_bytes(ZIP.read_bytes())
    s = fe.run(["--local", str(src), "--out", str(tmp_path / "a"), "--no-zip"])
    assert s["outcomes"] == {"excluded:pdf_missing": 1}
    s = fe.run(["--local", str(src), "--out", str(tmp_path / "b"), "--no-zip", "--pilot", "--allow-missing-pdf"])
    assert s["xbrl_only"] == 1
    assert "E02367,S100Y9NX" in (tmp_path / "b" / "forced_dev.txt").read_text(encoding="utf-8")
    view = list(csv.DictReader(open(tmp_path / "b" / "filings" / "S100Y9NX" / "bs_rows.csv", encoding="utf-8-sig")))
    assert view[0]["label"] == "資産の部" and len(view) == 56
    s2 = fe.run(["--local", str(src), "--out", str(tmp_path / "b"), "--no-zip", "--pilot", "--allow-missing-pdf"])
    assert s2["processed"] == 1                                   # resume: not processed twice


def test_local_pairing_by_docid_and_by_folder(tmp_path):
    a = tmp_path / "in" / "nintendo"
    a.mkdir(parents=True)
    (a / "whatever.zip").write_bytes(ZIP.read_bytes())
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(a / "annual_report.pdf")                              # sole PDF next to the sole zip
    pairs, notes = fe.pair_local(tmp_path / "in")
    assert pairs[0][1].name == "annual_report.pdf" and pairs[0][2]["docID"] == "S100Y9NX" and notes == []
    b = tmp_path / "in2"
    b.mkdir()
    (b / "x.zip").write_bytes(ZIP.read_bytes())
    pdf.save(b / "S100Y9NX.pdf")
    pdf.save(b / "other.pdf")
    pairs, notes = fe.pair_local(b)
    assert pairs[0][1].name == "S100Y9NX.pdf" and any("other.pdf" in n for n in notes)
    s = fe.run(["--local", str(b), "--out", str(tmp_path / "o"), "--no-zip"])
    assert s["outcomes"] == {"excluded:bs_heading_not_found_in_pdf": 1}   # blank PDF: no balance sheet

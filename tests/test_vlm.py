"""VLM backend plumbing, tested with a FAKE runner (no model, no GPU).

These tests show that (a) a correct table from a VLM goes through the shared pipeline to the same
cells, structure and ACCEPT decision as the born-digital page, (b) one wrong digit fails a check and
goes to review, (c) malformed responses and cache misses fail the page instead of being repaired,
and (d) amendment A6-A8: column swaps, wrong or missing unit lines go to review, while
indentation-only errors are corrected from the page geometry. They are not evidence about any real
model's accuracy. Tests marked needs_ocr use the Tesseract second reading (jpn model).
"""
import io
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from docparse import vlm
from docparse.evaluate import evaluate
from docparse.extract import TesseractBackend
from docparse.pipeline import parse_pdf


def _ocr_ready() -> bool:
    try:
        TesseractBackend().check_setup()
        return True
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _ocr_ready(), reason="Tesseract jpn model not set up (second reading)")

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/balance_sheet_after_correction.pdf"
GT = ROOT / "gt/out/ground_truth"


def _scan_pdf(tmp_path, dpi=300) -> Path:
    """Same render as the smoke test (300 dpi grey PNG), so the OCR second reading is shared."""
    page = pymupdf.open(FIX)[0]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    buf = io.BytesIO()
    Image.frombytes("L", (pix.width, pix.height), pix.samples).save(buf, format="PNG")
    out = pymupdf.open()
    p = out.new_page(width=page.rect.width, height=page.rect.height)
    p.insert_image(p.rect, stream=buf.getvalue())
    path = tmp_path / "scan.pdf"
    out.save(path)
    return path


def _gt_table() -> dict:
    g = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))
    rows = [{"label": r["label_ja"], "indent": r["presentation_level"],
             "values": [r["cells"][p]["raw_text"] if p in r["cells"] else None for p in ("prior", "current")]}
            for r in g["rows"]]
    return {"heading_lines": ["- 4 -", "（訂正後）", "３．連結財務諸表及び主な注記", "（１）連結貸借対照表", "（単位：千円）"],
            "columns": ["前連結会計年度(2025年３月31日)", "当連結会計年度(2026年３月31日)"], "rows": rows}


class FakeRunner:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def __call__(self, image, prompt, settings):
        self.calls += 1
        return self.text, {"model": "fake"}


def _backend(tmp_path, text, second_reader="tesseract"):
    return vlm.VLMBackend(cache_dir=tmp_path / "cache", model="fake/model", runner=FakeRunner(text),
                          second_reader=second_reader)


@needs_ocr
def test_correct_vlm_table_is_verified_and_accepted(tmp_path):
    pdf = _scan_pdf(tmp_path)
    be = _backend(tmp_path, json.dumps(_gt_table(), ensure_ascii=False))
    pred = parse_pdf(str(pdf), force_route="visual", ocr=be)
    assert pred["metadata"]["route"]["backend"] == "vlm"
    assert pred["acceptance"]["decision"] == "accept", pred["acceptance"]["reasons"]
    rep = evaluate(str(pdf), str(GT), str(tmp_path / "eval"), route="visual", backend=be)
    assert rep["cells"]["f1"] == 1.0
    assert rep["structure"]["calc_parent"] == 1.0 and rep["structure"]["presentation_parent"] == 1.0


def test_one_wrong_digit_fails_a_check_and_goes_to_review(tmp_path):
    t = _gt_table()
    row = next(r for r in t["rows"] if r["label"] == "受取手形")
    row["values"][0] = "20,559"                          # 20,659 misread
    pred = parse_pdf(str(_scan_pdf(tmp_path)), force_route="visual",
                     ocr=_backend(tmp_path, json.dumps(t, ensure_ascii=False)))
    assert pred["acceptance"]["decision"] == "review"
    assert any(r.startswith("A2") for r in pred["acceptance"]["reasons"])


@pytest.mark.parametrize("text", [
    "Here is the table: {}",                                           # prose around JSON
    json.dumps({"heading_lines": [], "columns": ["a"], "rows": [{"label": "x", "indent": 0, "values": ["12a"]}]}),
    json.dumps({"heading_lines": [], "columns": ["a", "b"], "rows": [{"label": "x", "indent": 0, "values": ["1"]}]}),
    json.dumps({"heading_lines": [], "columns": ["a"], "rows": [{"label": "x", "indent": "0", "values": ["1"]}]}),
    json.dumps({"heading_lines": [], "columns": ["a"], "rows": [], "note": "x"}),
])
def test_malformed_response_fails_the_page_never_repaired(tmp_path, text):
    with pytest.raises(vlm.VLMSchemaError):
        vlm.parse_response(text)
    pred = parse_pdf(str(_scan_pdf(tmp_path)), safe=True, force_route="visual", ocr=_backend(tmp_path, text))
    assert pred["status"] == "failed" and pred["acceptance"]["decision"] == "review"


def test_fenced_json_is_accepted_as_is():
    t = {"heading_lines": [], "columns": ["a"], "rows": [{"label": "x", "indent": 0, "values": ["△1,234"]}]}
    assert vlm.parse_response("```json\n" + json.dumps(t) + "\n```") == t


@needs_ocr
def test_cache_roundtrip_and_offline_miss(tmp_path):
    pdf = _scan_pdf(tmp_path)
    text = json.dumps(_gt_table(), ensure_ascii=False)
    be = _backend(tmp_path, text)
    parse_pdf(str(pdf), force_route="visual", ocr=be)
    parse_pdf(str(pdf), force_route="visual", ocr=be)
    assert be.runner.calls == 1                                       # second read is a cache hit
    offline = vlm.VLMBackend(cache_dir=tmp_path / "cache", model="fake/model")
    assert parse_pdf(str(pdf), force_route="visual", ocr=offline)["acceptance"]["decision"] == "accept"
    other = vlm.VLMBackend(cache_dir=tmp_path / "cache", model="fake/model", settings={"max_side": 1000})
    pred = parse_pdf(str(pdf), safe=True, force_route="visual", ocr=other)   # different settings: no reuse
    assert pred["status"] == "failed" and "VLMCacheMiss" in pred["reason"]


def test_cache_key_is_the_embedded_image_bytes(tmp_path):
    pdf = _scan_pdf(tmp_path)
    img, how = vlm.page_image(pymupdf.open(pdf)[0])
    assert how.startswith("embedded:")
    _, how2 = vlm.page_image(pymupdf.open(FIX)[0])                      # born-digital: rendered
    assert how2 == "render:300dpi"


@needs_ocr
def test_smoke_gate_passes_a_correct_read_and_stops_a_bad_one(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("vlm_run", ROOT / "scripts/vlm_run.py")
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    good = vr.smoke(_backend(tmp_path / "a", json.dumps(_gt_table(), ensure_ascii=False)), tmp_path / "a")
    assert good["passed"] and good["cells_correct"] == 74
    t = _gt_table()
    for r in t["rows"][2:7]:                                   # 10 cells wrong -> below 70/74
        r["values"] = ["1", "2"]
    bad = vr.smoke(_backend(tmp_path / "b", json.dumps(t, ensure_ascii=False)), tmp_path / "b")
    assert not bad["passed"] and bad["cells_correct"] < vr.SMOKE_MIN_CELLS


# --------------------------------------------------------------------------- amendment A6-A8 (2026-10-25)
def _variant(kind: str) -> dict:
    t = _gt_table()
    if kind == "all_indents_0":
        for r in t["rows"]:
            r["indent"] = 0
    elif kind == "depreciation_one_less":
        for r in t["rows"]:
            if r["label"] == "減価償却累計額":
                r["indent"] -= 1
    elif kind == "swapped_columns":                    # 2025 numbers under 2026, headers kept
        for r in t["rows"]:
            r["values"] = r["values"][::-1]
    elif kind == "unit_million":
        t["heading_lines"] = [h.replace("千円", "百万円") for h in t["heading_lines"]]
    elif kind == "no_heading_lines":
        t["heading_lines"] = []
    return t


@needs_ocr
@pytest.mark.parametrize("kind,decision,cells,reason", [
    ("exact", "accept", 74, None),
    ("all_indents_0", "accept", 74, None),             # indentation comes from the page geometry
    ("depreciation_one_less", "accept", 74, None),
    ("swapped_columns", "review", None, "A8"),
    ("unit_million", "review", None, "A8"),
    ("no_heading_lines", "review", None, "A6"),
])
def test_fake_vlm_cases_after_amendment(tmp_path, kind, decision, cells, reason):
    pdf = _scan_pdf(tmp_path)
    be = _backend(tmp_path, json.dumps(_variant(kind), ensure_ascii=False))
    rep = evaluate(str(pdf), str(GT), str(tmp_path / "eval"), route="visual", backend=be)
    assert rep["acceptance"]["decision"] == decision, rep["acceptance"]["reasons"]
    if cells is not None:
        assert rep["cells"]["correct"] == cells
        assert rep["structure"]["calc_parent"] == 1.0 and rep["structure"]["presentation_parent"] == 1.0
    if reason:
        assert any(r.startswith(reason) for r in rep["acceptance"]["reasons"])


def test_without_a_second_reading_every_vlm_page_goes_to_review(tmp_path):
    pdf = _scan_pdf(tmp_path)
    be = _backend(tmp_path, json.dumps(_gt_table(), ensure_ascii=False), second_reader=None)
    pred = parse_pdf(str(pdf), force_route="visual", ocr=be)
    assert pred["acceptance"]["decision"] == "review"
    assert any(r.startswith("A8") for r in pred["acceptance"]["reasons"])


def test_text_layer_page_without_unit_line_goes_to_review():
    """A6 on the born-digital route: remove the unit line from the page's text layer."""
    doc = pymupdf.open(FIX)
    page = doc[0]
    for r in page.search_for("単位"):
        page.add_redact_annot(r.include_rect(pymupdf.Rect(r.x0 - 20, r.y0, r.x1 + 60, r.y1)))
    page.apply_redactions()
    from docparse.pipeline import parse_page
    d = parse_page(page)
    assert d["metadata"]["unit_ja"] is None
    assert d["acceptance"]["decision"] == "review" and any(x.startswith("A6") for x in d["acceptance"]["reasons"])


# --------------------------------------------------------------------------- amendment A9 + fixes (2026-10-25)
@needs_ocr
@pytest.mark.parametrize("label,col,new", [("現金及び預金", 0, "2,002,428"), ("受取手形", 1, "26,334")])
def test_a9_single_ones_digit_error_is_caught_by_per_cell_agreement(tmp_path, label, col, new):
    """The user's case: a last-digit error inside the truncation window passes every identity check;
    A9 catches it wherever Tesseract read that cell."""
    t = _gt_table()
    next(r for r in t["rows"] if r["label"] == label)["values"][col] = new
    pred = parse_pdf(str(_scan_pdf(tmp_path)), force_route="visual",
                     ocr=_backend(tmp_path, json.dumps(t, ensure_ascii=False)))
    cc = pred["metadata"]["route"]["backend_diagnostics"]["cross_check"]
    assert not any(r.startswith("A2") for r in pred["acceptance"]["reasons"])     # checks alone miss it
    assert pred["acceptance"]["decision"] == "review"
    assert any(r.startswith("A9") for r in pred["acceptance"]["reasons"])
    assert any(d["vlm"] == new for d in cc["cell_disagreements"])


@needs_ocr
def test_a9_perfect_read_on_clean_scan_agrees_cell_by_cell(tmp_path):
    pred = parse_pdf(str(_scan_pdf(tmp_path)), force_route="visual",
                     ocr=_backend(tmp_path, json.dumps(_gt_table(), ensure_ascii=False)))
    cc = pred["metadata"]["route"]["backend_diagnostics"]["cross_check"]
    assert cc["cells_ok"] and cc["cells_compared"] > 0 and not cc["cell_disagreements"]


def test_a9_blank_ocr_cells_do_not_block():
    t = {"heading_lines": [], "columns": ["前(2025年3月31日)", "当(2026年3月31日)"],
         "rows": [{"label": "現金", "indent": 0, "values": ["1,000", "2,000"]}]}
    second = {"status": "ok", "n_columns": 2, "rows": [{"label_norm": "現金", "values": {1: 2000}, "signed": {1: 2000}}]}
    assert vlm.cell_agreement(t, second, {0: 0})["cells_ok"]                      # col 0 blank in OCR
    second["rows"][0]["values"] = {0: 1001, 1: 2000}
    res = vlm.cell_agreement(t, second, {0: 0})
    assert not res["cells_ok"] and res["cell_disagreements"][0]["col"] == 0


@needs_ocr
def test_unit_is_read_on_a_positively_rotated_scan(tmp_path):
    """rot_+1 (seen set): the header band is cut above the period-header lines; before the fix
    psm 6 garbled the unit line when the column headers were in the same band."""
    import sys
    import numpy as np
    sys.path.insert(0, str(ROOT / "stress"))
    from run_stress import make_variant
    pdf = tmp_path / "rot_+1.pdf"
    make_variant(FIX, pdf, 300, 1.0, 0, None, np.random.default_rng(0))
    pred = parse_pdf(str(pdf), force_route="visual",
                     ocr=_backend(tmp_path, json.dumps(_gt_table(), ensure_ascii=False)))
    cc = pred["metadata"]["route"]["backend_diagnostics"]["cross_check"]
    assert cc["ocr_unit"] == "千円" and not any("unit" in p for p in cc["problems"])


def test_a9_suffix_variant_accepts_lost_leading_groups_only():
    """A9-suffix (pre-registered dev candidate): the OCR digit string must be a suffix of the VLM's."""
    t = {"heading_lines": [], "columns": ["前(2025年3月31日)", "当(2026年3月31日)"],
         "rows": [{"label": "x", "indent": 0, "values": ["3,377,407", "4,553"]}]}
    sec = {"status": "ok", "n_columns": 2, "rows": [{"label_norm": "x", "values": {0: 407, 1: 4553},
                                                    "signed": {0: 407, 1: 4553}}]}
    assert not vlm.cell_agreement(t, sec, {0: 0}, "magnitude")["cells_ok"]      # strict: 407 != 3377407
    assert vlm.cell_agreement(t, sec, {0: 0}, "suffix")["cells_ok"]             # suffix: '3377407' ends with '407'
    sec["rows"][0]["values"][1] = 4554                                         # last digit differs
    assert not vlm.cell_agreement(t, sec, {0: 0}, "suffix")["cells_ok"]

"""The original page end to end, and a check that the harness can actually fail."""
import copy
import json

import pytest
from conftest import FIXTURES, GT_STEM

from docparse.evaluate import evaluate, score_cells, score_corrections, score_structure
from docparse.export import read_long, write_all
from docparse.pipeline import parse_pdf

PDF = FIXTURES / "balance_sheet_after_correction.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="fixture PDF missing")


def test_original_page_matches_ground_truth(tmp_path):
    rep = evaluate(str(PDF), str(GT_STEM), str(tmp_path))
    assert rep["route"]["route"] == "born_digital"
    assert rep["cells"]["f1"] == 1.0, rep["cells"]["differences"]
    s = rep["structure"]
    assert (s["presentation_parent"], s["calc_parent"], s["row_type"]) == (1.0, 1.0, 1.0), s["differences"]
    assert rep["corrections"]["accuracy"] == 1.0 and rep["corrections"]["recall"] == 1.0
    summ = rep["validation"]["pred_summary"]
    assert summ["template"]["failed"] == 0 and summ["dynamic"]["failed"] == 0
    assert summ["template_expected"] == 26 and summ["template_evaluable"] == 12
    assert rep["acceptance"]["decision"] == "accept" and rep["acceptance"]["sections_present"] == ["資産"]


def test_harness_detects_injected_errors(tmp_path):
    gt = json.loads(GT_STEM.with_suffix(".json").read_text(encoding="utf-8"))
    pred = parse_pdf(str(PDF))
    bad = copy.deepcopy(pred)
    rows = {r["label_norm"]: r for r in bad["rows"]}
    rows["現金及び預金"]["cells"]["current"]["reported_value"] = 608280          # one-digit error
    rows["受取手形"]["calc_parent"] = rows["固定資産合計"]["row_id"]              # wrong parent
    rows["投資有価証券"]["cells"]["current"]["is_corrected"] = False           # missed correction
    paths = write_all(bad, tmp_path, "bad")
    cells = score_cells(read_long(GT_STEM.with_suffix(".long.csv")), read_long(paths["long"]))
    assert cells["correct"] == 73 and cells["differences"][0]["kind"] == "value_mismatch"
    assert score_structure(gt, bad)["calc_parent"] < 1.0
    corr = score_corrections(gt, bad)
    assert corr["recall"] == 0.5 and corr["accuracy"] < 1.0

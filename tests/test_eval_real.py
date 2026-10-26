"""bench/eval_real.py: the real-filing PDF evaluation runner, exercised on the synthetic EDINET
filing (the reference page as bs_pages.pdf, ground truth from its generated XBRL). No real filing."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))
import build_gt_xbrl as gtb  # noqa: E402
import eval_real  # noqa: E402
from test_bench_phaseA import _filing_dir  # noqa: E402


def test_runner_parses_pdf_and_scores_against_xbrl_ground_truth(tmp_path):
    _filing_dir(tmp_path)
    gtb.main(str(tmp_path / "raw"), str(tmp_path / "gt"))
    res = eval_real.main([str(tmp_path / "raw"), str(tmp_path / "gt"), str(tmp_path / "out")])
    r = res["filings"]["S1NEW"]
    assert r["scored"] and r["page_correct"], r["first_differences"]
    assert r["accepted_all_pages"] and r["pages"][0]["route"] == "born_digital"
    assert res["summary"]["accepted_and_incorrect"] == 0
    assert (tmp_path / "out" / "real_eval.md").exists()


def test_filing_without_kept_ground_truth_is_listed_not_scored(tmp_path):
    _filing_dir(tmp_path)
    gtb.main(str(tmp_path / "raw"), str(tmp_path / "gt"))
    p = tmp_path / "gt" / "S1NEW" / "ground_truth.json"
    gt = json.loads(p.read_text(encoding="utf-8"))
    gt["metadata"]["alignment"]["status"] = "excluded_alignment"
    p.write_text(json.dumps(gt, ensure_ascii=False), encoding="utf-8")
    res = eval_real.main([str(tmp_path / "raw"), str(tmp_path / "gt"), str(tmp_path / "out")])
    assert res["filings"]["S1NEW"] == {"scored": False, "why": "ground truth not kept: excluded_alignment"}


def test_sealed_run_refuses_unfrozen_code(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_real, "frozen_fingerprints", lambda: set())
    with pytest.raises(SystemExit, match="not logged"):
        eval_real.main([str(tmp_path), str(tmp_path), str(tmp_path / "out"), "--sealed"])


def test_sealed_run_happens_once(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_real, "frozen_fingerprints", lambda: {eval_real.code_fingerprint()})
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "real_eval.json").write_text("{}")
    with pytest.raises(SystemExit, match="evaluated once"):
        eval_real.main([str(tmp_path), str(tmp_path), str(tmp_path / "out"), "--sealed"])

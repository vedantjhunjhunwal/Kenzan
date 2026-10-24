"""Build the frozen ground truth for balance_sheet_after_correction.pdf page 1.

Provenance per field:
  label_ja, presentation_level, raw values, signs, underlines -> visual transcription (crops)
  bbox, dash codepoint in raw_text                          -> text layer (pixels can't give them)
  calc_parent, row_type, closes_header, label_en, xbrl       -> human annotation (below)
Derived with the same code the pipeline uses: label_norm / label_path (docparse.normalize,
docparse.hierarchy), identity checks (docparse.validate + docparse.registry), exports (docparse.export).
The build aborts if the visual read and the text layer disagree on anything except dash codepoints.

usage: python build_gt.py <pdf> <visual_transcription.json> <out_dir>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docparse.export import write_all  # noqa: E402
from docparse.hierarchy import assign_label_paths  # noqa: E402
from docparse.normalize import decimals_for, label_norm, parse_value  # noqa: E402
from docparse.validate import run_checks  # noqa: E402
from diff_vs_textlayer import diff  # noqa: E402
from textlayer_rows import read_rows  # noqa: E402

UNIT_MULT = 1000
DECIMALS = decimals_for(UNIT_MULT)
STATEMENT_TYPE = "consolidated_balance_sheet"
PERIODS = [
    {"id": "prior", "label_ja": "前連結会計年度", "context": "instant", "date": "2025-03-31"},
    {"id": "current", "label_ja": "当連結会計年度", "context": "instant", "date": "2026-03-31"},
]

# (label_en, xbrl_candidate, xbrl_conf, calc_parent_row#, row_type, closes_row#)
# xbrl_conf: "high" = standard, widely used element; "medium" = name from memory, needs taxonomy check.
# None verified against the FSA 勘定科目リスト yet; all excluded from scoring (xbrl_verified=False).
ANN = {
    1: ("Assets (section)", "AssetsAbstract", "medium", None, "header", None),
    2: ("Current assets", "CurrentAssetsAbstract", "medium", None, "header", None),
    3: ("Cash and deposits", "CashAndDeposits", "high", 13, "line_item", None),
    4: ("Notes receivable - trade", "NotesReceivableTrade", "high", 13, "line_item", None),
    5: ("Accounts receivable - trade", "AccountsReceivableTrade", "high", 13, "line_item", None),
    6: ("Electronically recorded monetary claims - operating",
        "ElectronicallyRecordedMonetaryClaimsOperatingCA", "medium", 13, "line_item", None),
    7: ("Merchandise and finished goods", "MerchandiseAndFinishedGoods", "high", 13, "line_item", None),
    8: ("Work in process", "WorkInProcess", "high", 13, "line_item", None),
    9: ("Raw materials and supplies", "RawMaterialsAndSupplies", "high", 13, "line_item", None),
    10: ("Short-term loans receivable", "ShortTermLoansReceivable", "high", 13, "line_item", None),
    11: ("Other current assets", "OtherCA", "high", 13, "line_item", None),
    12: ("Allowance for doubtful accounts (current)", "AllowanceForDoubtfulAccountsCA", "high", 13,
         "line_item", None),
    13: ("Total current assets", "CurrentAssets", "high", 43, "subtotal", 2),
    14: ("Non-current assets", "NoncurrentAssetsAbstract", "medium", None, "header", None),
    15: ("Property, plant and equipment", "PropertyPlantAndEquipmentAbstract", "medium", None, "header", None),
    16: ("Buildings and structures (gross)", "BuildingsAndStructures", "high", 18, "line_item", None),
    17: ("Accumulated depreciation - buildings and structures",
         "AccumulatedDepreciationBuildingsAndStructures", "medium", 18, "line_item", None),
    18: ("Buildings and structures, net", "BuildingsAndStructuresNet", "high", 30, "subtotal", None),
    19: ("Machinery, equipment and vehicles (gross)", "MachineryEquipmentAndVehicles", "high", 21,
         "line_item", None),
    20: ("Accumulated depreciation - machinery, equipment and vehicles",
         "AccumulatedDepreciationMachineryEquipmentAndVehicles", "medium", 21, "line_item", None),
    21: ("Machinery, equipment and vehicles, net", "MachineryEquipmentAndVehiclesNet", "high", 30,
         "subtotal", None),
    22: ("Land", "Land", "high", 30, "line_item", None),
    23: ("Leased assets - PP&E (gross)", "LeaseAssetsPPE", "medium", 25, "line_item", None),
    24: ("Accumulated depreciation - leased assets", "AccumulatedDepreciationLeaseAssetsPPE", "medium", 25,
         "line_item", None),
    25: ("Leased assets - PP&E, net", "LeaseAssetsNetPPE", "medium", 30, "subtotal", None),
    26: ("Construction in progress", "ConstructionInProgress", "high", 30, "line_item", None),
    27: ("Other PP&E (gross)", "OtherPPE", "medium", 29, "line_item", None),
    28: ("Accumulated depreciation - other PP&E", "AccumulatedDepreciationOtherPPE", "medium", 29,
         "line_item", None),
    29: ("Other PP&E, net", "OtherNetPPE", "medium", 30, "subtotal", None),
    30: ("Total property, plant and equipment", "PropertyPlantAndEquipment", "high", 42, "subtotal", 15),
    31: ("Intangible assets", "IntangibleAssetsAbstract", "medium", None, "header", None),
    32: ("Leased assets - intangible", "LeaseAssetsIA", "medium", 34, "line_item", None),
    33: ("Other intangible assets", "OtherIA", "medium", 34, "line_item", None),
    34: ("Total intangible assets", "IntangibleAssets", "high", 42, "subtotal", 31),
    35: ("Investments and other assets", "InvestmentsAndOtherAssetsAbstract", "medium", None, "header", None),
    36: ("Investment securities", "InvestmentSecurities", "high", 41, "line_item", None),
    37: ("Shares of subsidiaries and associates", "StocksOfSubsidiariesAndAffiliates", "medium", 41,
         "line_item", None),
    38: ("Long-term loans receivable", "LongTermLoansReceivable", "high", 41, "line_item", None),
    39: ("Retirement benefit asset", "NetDefinedBenefitAsset", "medium", 41, "line_item", None),
    40: ("Other investments and other assets", "OtherIOA", "medium", 41, "line_item", None),
    41: ("Total investments and other assets", "InvestmentsAndOtherAssets", "high", 42, "subtotal", 35),
    42: ("Total non-current assets", "NoncurrentAssets", "high", 43, "subtotal", 14),
    43: ("Total assets", "Assets", "high", None, "total", 1),
}


def rid(n):
    return None if n is None else f"r{n:02d}"


def gt_cell(raw_vis: str, raw_text: str) -> dict | None:
    """The visual read decides the value; the text layer only supplies the dash codepoint."""
    if raw_vis == "":
        return None
    pv = parse_value(raw_text if raw_vis == "DASH" else raw_vis)
    assert pv is not None and not pv.warnings, f"unexpected cell {raw_vis!r} / {raw_text!r}"
    if raw_vis == "DASH":
        assert pv.is_nil, f"text layer glyph {raw_text!r} is not a nil dash"
    return {"raw_text": pv.raw_text, "reported_value": pv.reported_value,
            "value": None if pv.reported_value is None else pv.reported_value * UNIT_MULT,
            "decimals": None if pv.is_nil else DECIMALS,
            "is_negative": pv.is_negative, "is_nil": pv.is_nil}


def main(pdf, vis_path, out_dir):
    disagreements, *_ = diff(pdf, vis_path)
    hard = [d for d in disagreements if "DASH" not in d[1]]
    assert not hard, f"visual vs text layer disagree; resolve before freezing: {hard}"

    vis = json.load(open(vis_path, encoding="utf-8"))
    text, _ = read_rows(pdf)
    rows = []
    for n, (v, t) in enumerate(zip(vis["rows"], text), 1):
        en, xbrl, conf, cp, rtype, closes = ANN[n]
        norm, refs = label_norm(v["label"])
        cells = {}
        for per in ("prior", "current"):
            tc = t["cells"].get(per)
            c = gt_cell(v[per], tc["raw"] if tc else "")
            if c is not None:
                c["is_corrected"] = bool(v.get("ul_" + per))
                c["confidence"] = None
                c["bbox"] = tc["bbox"]
                cells[per] = c
        rows.append({
            "row_id": rid(n), "label_ja": v["label"], "label_norm": norm, "label_path": None, "label_en": en,
            "row_type": rtype, "presentation_level": v["level"], "presentation_parent": None,
            "calc_parent": rid(cp), "calc_parent_source": None if rtype == "header" else "manual",
            "calc_rule": None, "total_status": None, "closes_header": rid(closes), "footnote_refs": refs,
            "xbrl_candidate": xbrl, "xbrl_confidence": conf, "xbrl_verified": False,
            "human_verification": "verified", "label_bbox": t["label_bbox"], "cells": cells,
        })
    for i, r in enumerate(rows):
        r["presentation_parent"] = next((p["row_id"] for p in reversed(rows[:i])
                                         if p["presentation_level"] < r["presentation_level"]), None)
    assign_label_paths(rows)
    assert not any("#" in r["label_path"] for r in rows), "label_path must be unique in ground truth"

    checks, summary = run_checks(rows, PERIODS, STATEMENT_TYPE)
    meta = {
        "company": None, "company_code": None,
        "document_type_guess": "決算短信 (from section heading 「３．連結財務諸表及び主な注記」; unverified)",
        "revision": "訂正後",
        "statement_type": STATEMENT_TYPE, "statement_title_ja": "連結貸借対照表",
        "accounting_standard_guess": "J-GAAP (unverified)",
        "unit_ja": "千円", "unit_multiplier": UNIT_MULT, "currency": "JPY", "decimals": DECIMALS,
        "rounding_convention": "truncate (assumed: no 切捨て note on this page; consistent with all checks)",
        "source": {"file": Path(pdf).name, "page_index": 0, "printed_page": "4"},
        "sections_present": ["資産の部"], "sections_missing": ["負債の部", "純資産の部"],
        "statement_complete": False,
        "hypotheses": [{
            "id": "H1", "status": "unverified",
            "text": "Correction was a reclassification between 投資有価証券 and 関係会社株式 (current period); "
                    "the subtotal 投資その他の資産合計 is not underlined. Needs the 訂正前 page.",
        }],
        "provenance": {
            "values_labels_levels_underlines": "visual transcription of 300-dpi crops",
            "bbox_and_dash_codepoints": "text layer (pixels cannot supply them)",
            "structure_and_mappings": "human annotation",
            "text_layer_disagreements": [f"row {d[0]}: {d[1]}" for d in disagreements],
        },
        "verification": {
            "human": {"status": "verified", "by": "user", "date": "2026-10-24",
                      "method": "row-by-row comparison of gt_overlay.png with the PDF",
                      "scope": "43 labels, indent levels, 74 values incl. △ signs and nil dashes, "
                               "underlined cells, calc_parent incl. 純額 blocks"},
            "identity_checks_recomputed_by_user": True,
            "independent_ai_review": {
                "status": "reported_by_user", "performed_in_this_session": False, "artifacts": None,
                "description": "AI review comparing the page rendered at 400 dpi with ground_truth.json; "
                               "no differences reported",
                "caveat": "another model's visual read can share failure modes with the transcription; "
                          "not counted as independent of the VLM path",
            },
        },
    }
    doc = {"status": "ok", "reason": None, "metadata": meta, "periods": PERIODS, "rows": rows, "checks": checks, "check_summary": summary}
    paths = write_all(doc, out_dir, "ground_truth")
    print(f"rows={len(rows)} cells={sum(len(r['cells']) for r in rows)} checks={len(checks)}")
    print("summary:", json.dumps(summary, ensure_ascii=False))
    for k, p in paths.items():
        print(k, p)


if __name__ == "__main__":
    main(*sys.argv[1:4])

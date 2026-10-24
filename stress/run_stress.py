"""Stress test: rasterized / rotated / noisy / JPEG variants of the original page.

The variant list and every parameter are fixed below BEFORE any run. The pipeline code is
fingerprinted (sha256 of docparse/*.py) and recorded with the results, so a reader can check it was
not changed between runs. Nothing here feeds back into the pipeline.

usage: python stress/run_stress.py <original.pdf> <gt_stem> <out_dir> [<spec.json>]
  spec: {"label", "seed", "variants": [[name, dpi|null, rotation_deg, noise_sigma, jpeg_q|null], ...]}
  default spec: stress/specs/seen.json
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
# Which pipeline to test: the project's docparse/ by default, or another snapshot, e.g.
# DOCPARSE_CODE_ROOT=baselines/round1 for the round-1 baseline.
CODE_ROOT = Path(os.environ.get("DOCPARSE_CODE_ROOT", ROOT)).resolve()
sys.path.insert(0, str(CODE_ROOT))
from docparse.evaluate import evaluate  # noqa: E402

# Variant lists and seeds live in stress/specs/*.json (seen.json = round-1 set, unseen.json = held-out).


def code_fingerprint() -> str:
    """Pipeline modules only; evaluate.py (the harness) is excluded so a harness fix cannot
    hide a pipeline change."""
    h = hashlib.sha256()
    for p in sorted((CODE_ROOT / "docparse").glob("*.py")):
        if p.name == "evaluate.py":
            continue
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def make_variant(src_pdf: Path, out_pdf: Path, dpi, rot, sigma, quality, rng) -> None:
    src = pymupdf.open(src_pdf)
    page = src[0]
    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    if rot:
        img = img.rotate(rot, resample=Image.BICUBIC, expand=False, fillcolor=255)
    if sigma:
        a = np.asarray(img, dtype=np.float32) + rng.normal(0, sigma, (img.height, img.width))
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    if quality:
        img.save(buf, format="JPEG", quality=quality)
    else:
        img.save(buf, format="PNG")
    out = pymupdf.open()
    p = out.new_page(width=page.rect.width, height=page.rect.height)
    p.insert_image(p.rect, stream=buf.getvalue())
    out.save(out_pdf)


def align_rows_positional(gt_rows, pred_rows):
    """Label-sequence alignment, then pair leftover rows by order inside each unmatched gap
    (so a misread label still pairs with the row it came from)."""
    a = [r["label_norm"] for r in gt_rows]
    b = [r["label_norm"] for r in pred_rows]
    m = {}
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                m[gt_rows[i1 + k]["row_id"]] = (pred_rows[j1 + k]["row_id"], tag == "equal")
    return m


def diagnose(gt: dict, pred: dict) -> list[dict]:
    """One line per ground-truth cell that the cell metric counts wrong, with the likely cause."""
    pr = {r["row_id"]: r for r in pred["rows"]}
    gper = {p["id"]: p["date"] for p in gt["periods"]}
    pper = {p.get("date"): p["id"] for p in pred["periods"]}
    pred_paths = {(r["label_path"], gper_date): True for r in pred["rows"] for gper_date in pper}
    m = align_rows_positional(gt["rows"], pred["rows"])
    out = []
    for g in gt["rows"]:
        for gpid, gc in g["cells"].items():
            date = gper[gpid]
            pair = m.get(g["row_id"])
            p = pr.get(pair[0]) if pair else None
            pc = p["cells"].get(pper.get(date, ""), None) if p else None
            same_val = pc is not None and (pc["reported_value"], pc["is_nil"], pc["is_negative"]) == \
                (gc["reported_value"], gc["is_nil"], gc["is_negative"])
            path_ok = p is not None and p["label_path"] == g["label_path"] and date in pper
            if same_val and path_ok:
                continue
            if p is None:
                cause = "row not extracted"
            elif date not in pper:
                cause = "period header not recognised"
            elif pc is None:
                cause = "cell not extracted (column assignment)"
            elif not same_val:
                if gc["is_negative"] and not pc["is_negative"] and abs(pc["reported_value"] or 0) == abs(gc["reported_value"] or 0):
                    cause = "sign lost (△ not read)"
                elif gc["is_nil"] != pc["is_nil"]:
                    cause = "nil dash misread"
                else:
                    cause = "digits misread"
            elif p["label_norm"] != g["label_norm"]:
                cause = "label misread -> path mismatch"
            else:
                cause = "hierarchy differs -> path mismatch"
            out.append({"row": g["row_id"], "period": date, "gt_label": g["label_ja"],
                        "pred_label": p["label_ja"] if p else None, "gt_raw": gc["raw_text"],
                        "pred_raw": pc["raw_text"] if pc else None, "cause": cause})
    return out


def main(src, gt_stem, out_dir, spec_path=None):
    spec_path = Path(spec_path or ROOT / "stress" / "specs" / "seen.json")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    variants, seed, label = spec["variants"], spec["seed"], spec["label"]
    spec_sha = hashlib.sha256(spec_path.read_bytes()).hexdigest()[:16]
    out = Path(out_dir)
    (out / "pdfs").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    fp = code_fingerprint()
    gt = json.loads(Path(f"{gt_stem}.json").read_text(encoding="utf-8"))
    results = []
    for name, dpi, rot, sigma, q in variants:
        pdf = Path(src) if dpi is None else out / "pdfs" / f"{name}.pdf"
        if dpi is not None:
            make_variant(Path(src), pdf, dpi, rot, sigma, q, rng)
        t0 = time.time()
        rep = evaluate(str(pdf), gt_stem, str(out / "runs" / name))
        secs = time.time() - t0
        pred = json.loads((out / "runs" / name / "prediction.json").read_text(encoding="utf-8"))
        diag = diagnose(gt, pred)
        (out / "runs" / name / "cell_diagnosis.json").write_text(json.dumps(diag, ensure_ascii=False, indent=1),
                                                                encoding="utf-8")
        c, s, k, v = rep["cells"], rep["structure"], rep["corrections"], rep["validation"]["pred_summary"]
        acc = rep.get("acceptance", {"decision": "n/a", "reasons": []})
        fully_correct = c["f1"] == 1.0 and s["calc_parent"] == 1.0 and s["presentation_parent"] == 1.0
        results.append({
            "set": label, "decision": acc["decision"], "acceptance_reasons": acc["reasons"],
            "fully_correct": fully_correct, "false_accept": acc["decision"] == "accept" and not fully_correct,
            "deskew_deg": pred["metadata"].get("route", {}).get("backend_diagnostics", {}).get("deskew_deg"),
            "statement_type_source": pred["metadata"].get("statement_type_source"),
            "variant": name, "params": {"dpi": dpi, "rotation": rot, "noise_sigma": sigma, "jpeg_q": q},
            "status": rep["status"], "reason": rep["reason"], "route": rep["route"]["route"],
            "backend": rep["route"].get("backend"), "seconds": round(secs, 1),
            "cells_correct": c["correct"], "cells_gt": c["gt_cells"], "cells_pred": c["pred_cells"],
            "precision": c["precision"], "recall": c["recall"], "f1": c["f1"],
            "rows_matched": s["rows_matched"], "level": s["presentation_level"],
            "pres_parent": s["presentation_parent"], "calc_parent": s["calc_parent"], "row_type": s["row_type"],
            "corr_accuracy": k["accuracy"], "corr_decided": k["decided"], "corr_recall": k["recall"],
            "consistency_template": v["template"]["consistency"], "consistency_dynamic": v["dynamic"]["consistency"],
            "solver_checks": v["solver"]["checks"], "checks_failed": v["template"]["failed"] + v["dynamic"]["failed"]
            + v["solver"]["failed"], "coverage": v["coverage"],
            "check_status_agreement": rep["validation"]["status_agreement_with_gt"],
            "warnings": rep["warnings"], "wrong_cells_by_cause": _count(diag),
        })
        print(f"[{label}] {name:13} {rep['status']:7} F1={c['f1']:.3f}  cells {c['correct']}/{c['gt_cells']}  "
              f"calc_parent={s['calc_parent']:.3f}  decision={acc['decision']}  {secs:.0f}s", flush=True)
    meta = {"set": label, "spec": str(spec_path.name), "spec_sha256": spec_sha, "code_fingerprint": fp,
            "seed": seed, "results": results}
    (out / "results.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "results.md").write_text(to_markdown(results, fp, label, spec_sha), encoding="utf-8")
    print("code fingerprint:", fp, "| spec sha256:", spec_sha,
          "| accepted:", sum(r["decision"] == "accept" for r in results),
          "| false accepts:", sum(r["false_accept"] for r in results))


def _count(diag):
    out = {}
    for d in diag:
        out[d["cause"]] = out.get(d["cause"], 0) + 1
    return out


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def to_markdown(results, fp, label="", spec_sha=""):
    lines = [f"Set: **{label}** | spec sha256 `{spec_sha}` | pipeline code fingerprint `{fp}`", "",
             "| Variant | Status | Route | Cells correct | P | R | F1 | Rows matched | Level | Pres. parent "
             "| Calc parent | Row type | is_corrected acc (decided) | Consistency tpl / dyn | Checks failed "
             "| Coverage | Check-status agreement | Warnings | Deskew ° | Stmt type from | Decision | False accept |",
             "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|--:|---|---|---|"]
    for r in results:
        w = ", ".join(f"{k}:{v}" for k, v in r["warnings"].items()) or "–"
        lines.append(
            f"| {r['variant']} | {r['status']} | {r['route']} | {r['cells_correct']}/{r['cells_gt']} "
            f"| {_pct(r['precision'])} | {_pct(r['recall'])} | {_pct(r['f1'])} | {r['rows_matched']} "
            f"| {_pct(r['level'])} | {_pct(r['pres_parent'])} | {_pct(r['calc_parent'])} | {_pct(r['row_type'])} "
            f"| {_pct(r['corr_accuracy'])} ({r['corr_decided']}/74) "
            f"| {_pct(r['consistency_template'])} / {_pct(r['consistency_dynamic'])} | {r['checks_failed']} "
            f"| {_pct(r['coverage'])} | {_pct(r['check_status_agreement'])} | {w} "
            f"| {r['deskew_deg'] if r['deskew_deg'] is not None else '–'} | {r['statement_type_source'] or '–'} "
            f"| {r['decision']} | {'**YES**' if r['false_accept'] else 'no'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main(*sys.argv[1:5])

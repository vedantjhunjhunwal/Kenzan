"""Held-out v3: Tesseract vs VLM vs VLM + checks, scored offline from cached VLM responses.

Arms (fixed before any VLM output existed; see stress/FREEZE_LOG.txt):
  tesseract+checks     frozen v2 pipeline (baselines/v2) with Tesseract; its own acceptance decision.
                       Read from stress/out_heldout_v3/results.json, not re-run.
  vlm (no checks)      the VLM's table taken as is whenever it parses: what you get if you trust it.
  vlm+checks           the same table through the acceptance rule A1-A5 (identity checks).
  vlm+checks+ocr       vlm+checks, and additionally review unless the Tesseract read of the same page
                       agrees: for each value column, the multiset of signed values (and dashes) must be equal.

Per variant: cell F1, calc-parent and presentation-parent accuracy, fully correct (F1 = calc =
pres = 100%), decision, false accept (accepted but not fully correct). Key question: is any scan
fully correct AND accepted, with 0 false accepts?

A variant with no cached response is reported as "not run" (it fails with VLMCacheMiss), never
skipped silently; the script then exits 2.

usage: python stress/run_vlm_compare.py --cache vlm_cache [--model M] [--quant 4bit|none]
       python stress/run_vlm_compare.py --ocr-ceiling      # no VLM needed
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stress"))
from docparse import vlm  # noqa: E402
from docparse.evaluate import evaluate  # noqa: E402
from run_stress import code_fingerprint  # noqa: E402

TESS = ROOT / "stress/out_heldout_v3"
SPEC = ROOT / "stress/specs/heldout_v3.json"
HASHES = ROOT / "stress/specs/heldout_v3.image_sha256.json"
GT = ROOT / "gt/out/ground_truth"


def signed_values(pred: dict) -> dict[int, collections.Counter]:
    """Per value column (left-to-right position, not period id: OCR can misread a header so one
    column comes out as 'col1' instead of 'current'), the multiset of signed values and dashes."""
    pos = {p["id"]: i for i, p in enumerate(pred.get("periods", []))}
    out: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in pred.get("rows", []):
        for pid, c in r["cells"].items():
            pid = pos.get(pid, pid)
            if c.get("is_nil"):
                out[pid]["nil"] += 1
            elif c.get("reported_value") is not None:
                v = c["reported_value"]
                out[pid][-abs(v) if c.get("is_negative") else abs(v)] += 1
    return out


def agreement(vlm_pred: dict, ocr_pred: dict) -> dict:
    a, b = signed_values(vlm_pred), signed_values(ocr_pred)
    unconfirmed = sum(sum((a[p] - b[p]).values()) for p in a)
    ocr_only = sum(sum((b[p] - a[p]).values()) for p in b)
    return {"agree": unconfirmed == 0 and ocr_only == 0 and bool(a), "vlm_cells_not_in_ocr": unconfirmed,
            "ocr_cells_not_in_vlm": ocr_only}


def ocr_ceiling() -> dict:
    """Needs no VLM: would the OCR-agreement rule even let a PERFECT read through? Compares the
    ground truth's values with the Tesseract read of each held-out page (same multiset test)."""
    gt = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    res = {}
    for name, dpi, *_ in spec["variants"]:
        if dpi is None:
            continue
        ocr = json.loads((TESS / "runs" / name / "prediction.json").read_text(encoding="utf-8"))
        res[name] = agreement(gt, ocr)
    out = {"question": "does the Tesseract read agree with the ground truth (per-period multiset of signed "
                       "values and dashes)? If not, vlm+checks+ocr cannot accept a fully correct VLM read.",
           "tesseract_results": "stress/out_heldout_v3/results.json", "variants": res,
           "pages_where_a_perfect_read_could_pass_the_ocr_rule": sum(r["agree"] for r in res.values())}
    (TESS / "ocr_agreement_ceiling.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def ones_digit_detection() -> str:
    """Share of single ones-digit errors the checks catch on this page (sign-aware window)."""
    txt = (ROOT / "gt/out/detection_power.txt").read_text(encoding="utf-8")
    m = re.search(r"digit:ones\s+\d+\s+[\d.]+%\s+([\d.]+%)", txt)
    return m.group(1) if m else "unknown"


def vlm_checks_caveat() -> str:
    return (f"Every accept from the vlm+checks arm has NO OCR agreement behind it: it rests on the identity "
            f"checks alone, and on this page those catch only {ones_digit_detection()} of single ones-digit "
            f"errors (gt/out/detection_power.txt, from gt/detection_power.py). This arm was frozen "
            f"(stress/FREEZE_LOG.txt, VLM METHOD FROZEN 2026-10-24T23:20:10Z) after the Tesseract output on v3 "
            f"had been compared with the v3 ground truth, but before any VLM output existed.")


def fully_correct(rep: dict) -> bool:
    c, s = rep["cells"], rep["structure"]
    return c["f1"] == 1.0 and s["calc_parent"] == 1.0 and s["presentation_parent"] == 1.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="vlm_cache")
    ap.add_argument("--model", default=vlm.DEFAULT_MODEL)
    ap.add_argument("--quant", default=vlm.DEFAULT_SETTINGS["quant"], choices=["4bit", "none"])
    ap.add_argument("--ocr-ceiling", action="store_true", help="only the no-VLM agreement ceiling")
    a = ap.parse_args(argv)
    if a.ocr_ceiling:
        print(json.dumps(ocr_ceiling(), indent=1))
        return 0

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    expected = json.loads(HASHES.read_text(encoding="utf-8"))
    tess = json.loads((TESS / "results.json").read_text(encoding="utf-8"))
    tess_by = {r["variant"]: r for r in tess["results"]}
    be = vlm.VLMBackend(a.cache, a.model, {"quant": a.quant})          # no runner: cache only
    skey = vlm.settings_key(a.model, be.settings)
    out = ROOT / "stress/out_heldout_v3_vlm" / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', a.model)}_{skey}"
    out.mkdir(parents=True, exist_ok=True)

    rows, not_run = [], []
    for name, dpi, *_ in spec["variants"]:
        if dpi is None:
            continue                      # born-digital goes to the text layer; no VLM involved
        pdf = TESS / "pdfs" / f"{name}.pdf"
        if vlm.pdf_image_sha256(pdf) != expected[name]:
            raise SystemExit(f"{pdf} page image differs from the frozen hash; regenerate with stress/make_pdfs.py")
        rep = evaluate(str(pdf), str(GT), str(out / "runs" / name), backend=be)
        pred = json.loads((out / "runs" / name / "prediction.json").read_text(encoding="utf-8"))
        ocr_pred = json.loads((TESS / "runs" / name / "prediction.json").read_text(encoding="utf-8"))
        t = tess_by[name]
        if rep["status"] == "failed" and "VLMCacheMiss" in (rep["reason"] or ""):
            not_run.append(name)
        ok_vlm, fc = rep["status"] == "ok", fully_correct(rep)
        dec_checks = rep["acceptance"]["decision"]
        agr = agreement(pred, ocr_pred)
        dec_ocr = "accept" if dec_checks == "accept" and agr["agree"] else "review"
        diag = pred["metadata"].get("route", {}).get("backend_diagnostics", {})
        rows.append({
            "variant": name, "vlm_status": rep["status"], "vlm_reason": rep["reason"],
            "tesseract": {"f1": t["f1"], "calc_parent": t["calc_parent"], "pres_parent": t["pres_parent"],
                          "fully_correct": t["fully_correct"], "decision": t["decision"],
                          "false_accept": t["false_accept"]},
            "vlm": {"f1": rep["cells"]["f1"], "cells_correct": rep["cells"]["correct"],
                    "calc_parent": rep["structure"]["calc_parent"],
                    "pres_parent": rep["structure"]["presentation_parent"], "fully_correct": fc,
                    "checks_failed": rep["validation"]["pred_summary"]["template"]["failed"]
                    + rep["validation"]["pred_summary"]["dynamic"]["failed"],
                    "coverage": rep["validation"]["pred_summary"]["coverage"]},
            "decisions": {
                "vlm_no_checks": "accept" if ok_vlm else "review",
                "vlm+checks": dec_checks,
                "vlm+checks+ocr": dec_ocr,
            },
            "acceptance_reasons": rep["acceptance"]["reasons"],
            "ocr_agreement": agr,
            "image_sha256": diag.get("image_sha256"), "cache_file": diag.get("cache_file"),
            "run_info": diag.get("run_info"),
        })

    def arm(dec_of, fc_of):
        acc = [r for r in rows if dec_of(r) == "accept"]
        return {"accepted": len(acc), "fully_correct_and_accepted": sum(fc_of(r) for r in acc),
                "false_accepts": sum(not fc_of(r) for r in acc),
                "fully_correct": sum(fc_of(r) for r in rows)}
    summary = {
        "tesseract+checks": arm(lambda r: r["tesseract"]["decision"], lambda r: r["tesseract"]["fully_correct"]),
        "vlm (no checks)": arm(lambda r: r["decisions"]["vlm_no_checks"], lambda r: r["vlm"]["fully_correct"]),
        "vlm+checks": arm(lambda r: r["decisions"]["vlm+checks"], lambda r: r["vlm"]["fully_correct"]),
        "vlm+checks+ocr": arm(lambda r: r["decisions"]["vlm+checks+ocr"], lambda r: r["vlm"]["fully_correct"]),
    }
    n = len(rows)
    for k, f in (("tesseract+checks", lambda r: r["tesseract"]["f1"]), ("vlm", lambda r: r["vlm"]["f1"])):
        summary.setdefault("mean_f1", {})[k] = round(sum(f(r) for r in rows) / n, 4)
    meta = {"set": "heldout_v3", "n_degraded": n, "model": a.model, "settings": be.settings, "settings_key": skey,
            "prompt_version": vlm.PROMPT_VERSION, "prompt_sha256": hashlib.sha256(vlm.PROMPT.encode()).hexdigest(),
            "vlm_pipeline_fingerprint": code_fingerprint(), "tesseract_pipeline_fingerprint": tess["code_fingerprint"],
            "spec_sha256": tess["spec_sha256"], "not_run": not_run, "vlm+checks_caveat": vlm_checks_caveat(),
            "what_v3_is": "8 new degradations of the same reference page, not new documents",
            "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "summary": summary, "results": rows}
    (out / "compare.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "compare.md").write_text(to_markdown(meta), encoding="utf-8")
    print((out / "compare.md").read_text(encoding="utf-8"))
    if not_run:
        print(f"WARNING: no cached VLM response for {not_run}; those rows are 'not run', not results.")
        return 2
    return 0


def _p(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def to_markdown(m: dict) -> str:
    L = [f"# Held-out v3: Tesseract vs VLM vs VLM + checks", "",
         f"Model `{m['model']}`, settings {json.dumps(m['settings'])} (key `{m['settings_key']}`), prompt "
         f"{m['prompt_version']} sha256 `{m['prompt_sha256'][:16]}`. Spec sha256 `{m['spec_sha256']}`. "
         f"Pipeline fingerprints: VLM run `{m['vlm_pipeline_fingerprint']}`, Tesseract run "
         f"`{m['tesseract_pipeline_fingerprint']}` (baselines/v2). Scored {m['scored_at']}.", "",
         f"Held-out v3 is {m['what_v3_is']}.", "",
         f"**Caveat for vlm+checks:** {m['vlm+checks_caveat']}", ""]
    if m["not_run"]:
        L += [f"**Not run (no cached response): {', '.join(m['not_run'])}.** Those rows are not results.", ""]
    L += ["| Variant | Tesseract F1 | Tess. decision | VLM F1 | VLM calc / pres parent | VLM fully correct "
          "| VLM checks failed | VLM+checks | OCR agrees (VLM cells not in OCR) | VLM+checks+OCR |",
          "|---|--:|---|--:|--:|---|--:|---|---|---|"]
    for r in m["results"]:
        t, v, d, g = r["tesseract"], r["vlm"], r["decisions"], r["ocr_agreement"]
        L.append(f"| {r['variant']} | {_p(t['f1'])} | {t['decision']} | {_p(v['f1'])} "
                 f"| {_p(v['calc_parent'])} / {_p(v['pres_parent'])} | {'yes' if v['fully_correct'] else 'no'} "
                 f"| {v['checks_failed']} | {d['vlm+checks']} | {'yes' if g['agree'] else 'no'} "
                 f"({g['vlm_cells_not_in_ocr']}) | {d['vlm+checks+ocr']} |")
    L += ["", "| Arm | Accepted | Fully correct AND accepted | False accepts | Fully correct (any decision) |",
          "|---|--:|--:|--:|--:|"]
    for k in ("tesseract+checks", "vlm (no checks)", "vlm+checks", "vlm+checks+ocr"):
        s = m["summary"][k]
        name = k + (" (no OCR agreement; see caveat)" if k == "vlm+checks" else "")
        L.append(f"| {name} | {s['accepted']} / {m['n_degraded']} | {s['fully_correct_and_accepted']} "
                 f"| {s['false_accepts']} | {s['fully_correct']} |")
    mf = m["summary"]["mean_f1"]
    L += ["", f"Mean cell F1 over the {m['n_degraded']} degraded pages: Tesseract {_p(mf['tesseract+checks'])}, "
          f"VLM {_p(mf['vlm'])}.", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())

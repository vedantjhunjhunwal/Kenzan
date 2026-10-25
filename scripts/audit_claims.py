"""Audit every number in README.md and docs/*.md against the result file it cites.

For each claim: the doc it appears in, the text that must appear there, the source file, and the
value recomputed from that file. A claim passes only if the doc text is present AND equals what the
file says. Writes docs/AUDIT.md and exits non-zero on any mismatch.

usage: python scripts/audit_claims.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = {p: (ROOT / p).read_text(encoding="utf-8") for p in
        ("README.md", "docs/RESULTS.md", "docs/METHOD.md", "docs/BENCHMARK.md", "SUBMISSION.md", "docs/VLM.md")
        if (ROOT / p).exists()}


def J(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def pct(x):
    return f"{100 * x:.1f}%"


claims: list[dict] = []


def claim(doc: str, text: str, source: str, ok_value: bool, note: str = "", line_key: str | None = None):
    """Record a claim. If line_key is given, `text` must appear on the doc line containing line_key."""
    body = DOCS.get(doc, "")
    if line_key:
        lines = [l for l in body.splitlines() if line_key in l]
        present = any(text in l for l in lines)
    else:
        present = text in body
    claims.append({"doc": doc, "claim": text if not line_key else f"{line_key}: {text}", "source": source,
                   "in_doc": present, "matches_file": ok_value, "note": note})


# ------------------------------------------------------------------ sources
geo = {r["variant"]: r for r in J("stress/out_geometry/geometry_results.json")}
summ = J("stress/summary_all_runs.json")
runs = {k: {r["variant"]: r for r in J(f"stress/out_{k}/results.json")["results"]}
        for k in ("v1_unseen", "v2_unseen", "v1_seen", "v2_seen")}
gt = J("bench/results/S100Y9NX/ground_truth_xbrl.json")
gts = gt["metadata"]["gt_build_stats"]
dp = (ROOT / "gt/out/detection_power.txt").read_text(encoding="utf-8")
G, S = "stress/out_geometry/geometry_results.json", "stress/summary_all_runs.json"

# ------------------------------------------------------------------ geometry / reference page
o = geo["original"]
for doc in ("README.md",):
    claim(doc, f"{o['cells']} cells, F1 {pct(o['f1'])}", G, o["f1"] == 1.0 and o["cells"] == "74/74")
    claim(doc, "all 100%, all accepted", G, all(r["f1"] == 1 and r["calc_parent"] == 1 and r["corrections"] == 1
                                                and r["decision"] == "accept" for r in geo.values()))
names = {"original": "| original |", "scale80": "| 80% scale |", "shift40": "| shifted 40 pt right |",
         "larger_page": "| A4 on an A3 page |"}
for k, key in names.items():
    r = geo[k]
    claim("docs/RESULTS.md", f"{r['cells']} | {pct(r['f1']).replace('.0%', '%')}", G,
          True, line_key=key)
    claim("docs/RESULTS.md", f"| {r['checks_failed']} | {pct(r['coverage'])} | {r['decision']} |", G, True, line_key=key)

# ------------------------------------------------------------------ stress means
u1, u2, s1, s2 = summ["unseen_v1"], summ["unseen_v2"], summ["seen_v1"], summ["seen_v2"]
claim("README.md", f"{pct(u1['mean_f1_degraded'])} → **{pct(u2['mean_f1_degraded'])}**", S, True)
claim("README.md", f"{pct(s1['mean_f1_degraded'])} → {pct(s2['mean_f1_degraded'])}", S, True)
claim("docs/RESULTS.md", f"**{pct(u1['mean_f1_degraded'])}** | **{pct(u2['mean_f1_degraded'])}**", S, True)
claim("docs/RESULTS.md", f"**{pct(s1['mean_f1_degraded'])}** | **{pct(s2['mean_f1_degraded'])}**", S, True)
n_deg = s2["n_degraded"] + u2["n_degraded"]
acc_deg = sum(len([a for a in summ[k]["accepted"] if a != "born_digital"]) for k in ("seen_v2", "unseen_v2"))
fa = sum(summ[k]["false_accepts"] for k in ("seen_v2", "unseen_v2"))
claim("README.md", f"{acc_deg} of {n_deg} degraded pages accepted, hence {fa} false accepts", S, True)
best = max(r["f1"] for v, r in runs["v2_unseen"].items() if v != "born_digital")
claim("README.md", "about 85%", "stress/out_v2_unseen/results.json", abs(best - 0.85) < 0.01,
      note=f"best held-out F1 = {pct(best)}")

# ------------------------------------------------------------------ unseen table (per variant)
labels = {"raster_200": "| raster 200 dpi |", "raster_120": "| raster 120 dpi |", "rot_+0.5": "| rotation +0.5° |",
          "rot_-0.5": "| rotation −0.5° |", "rot_+1.5": "| rotation +1.5° |", "rot_-1.5": "| rotation −1.5° |",
          "noise_s10": "| noise σ=10 |", "jpeg_q50": "| JPEG q=50 |", "scan_200": "| 200 dpi + −0.75° + σ=8 + q=60 |"}
vc1, vc2 = summ["unseen_v1"]["value_correct"], summ["unseen_v2"]["value_correct"]
for v, key in labels.items():
    a, b = runs["v1_unseen"][v], runs["v2_unseen"][v]
    src = "stress/out_v{1,2}_unseen/results.json + summary_all_runs.json"
    claim("docs/RESULTS.md", f"| {pct(a['f1'])} | **{pct(b['f1'])}** |", src, True, line_key=key)
    claim("docs/RESULTS.md", f"| {vc1[v]} → {vc2[v]} |", src, True, line_key=key)
    claim("docs/RESULTS.md", f"| {pct(b['calc_parent'])} | {b['checks_failed']} | {pct(b['coverage'])} |", src, True,
          line_key=key)
    reasons = sorted({r.split()[0] for r in b["acceptance_reasons"]})
    claim("docs/RESULTS.md", f"{b['decision']} ({', '.join(reasons)})", src, True, line_key=key)

# ------------------------------------------------------------------ seen table
seen_rows = {"| raster 300 / 150 / 100 dpi |": ["raster_300", "raster_150", "raster_100"],
             "| rotation +1 / −1 / +2 / −2° |": ["rot_+1", "rot_-1", "rot_+2", "rot_-2"],
             "| noise σ=15 · JPEG q=30 · combined scan |": ["noise_s15", "jpeg_q30", "scan_combo"]}
for key, vs in seen_rows.items():
    sep = " · " if "·" in key else " / "
    f = lambda k: sep.join(f"{100 * runs[k][v]['f1']:.1f}" for v in vs) + "%"
    claim("docs/RESULTS.md", f"| {f('v1_seen')} | {f('v2_seen')} |", "stress/out_v{1,2}_seen/results.json", True,
          line_key=key)

# ------------------------------------------------------------------ acceptance table
for k, key in (("seen_v2", "| v2 seen |"), ("unseen_v2", "| v2 unseen |")):
    r = summ[k]
    pages = r["n_degraded"] + 1
    claim("docs/RESULTS.md", f"| {pages} | {len(r['accepted'])} (born-digital) | {r['false_accepts']} | "
          f"{r['fully_correct_degraded']} / {r['n_degraded']} |", S, r["accepted"] == ["born_digital"], line_key=key)

# ------------------------------------------------------------------ deskew, latency
desk = []
for k in ("v2_seen", "v2_unseen"):
    for v, r in runs[k].items():
        rot = (r.get("params") or {}).get("rotation") or 0
        if rot and r.get("deskew_deg") is not None:
            desk.append(abs(abs(r["deskew_deg"]) - abs(rot)))
claim("docs/RESULTS.md", "Deskew recovered every angle to within 0.02°", "stress/out_v2_*/results.json",
      max(desk) <= 0.02, note=f"max error {max(desk):.2f}°")
secs = [r["seconds"] for k in runs for v, r in runs[k].items() if v != "born_digital"]
# Timings depend on the machine, so they are reported, not compared: a rerun on another machine
# rewrites the seconds. The doc must state a range; the note shows what the files now say.
m = re.search(r"(\d+)–(\d+) s/page", DOCS.get("docs/RESULTS.md", ""))
claim("docs/RESULTS.md", m.group(0) if m else "<range> s/page", "stress/out_*/results.json (seconds)", True,
      note=f"machine-dependent, not compared; files now say {min(secs):.1f}–{max(secs):.1f} s")

# ------------------------------------------------------------------ detection power
for txt in ("52.3%", "70.7%", "97.2%"):
    claim("docs/METHOD.md", txt, "gt/out/detection_power.txt", txt in dp)
claim("docs/METHOD.md", "13 of 20", "gt/out/detection_power.txt", "exact      13/20" in dp)
claim("docs/METHOD.md", "3,816", "gt/out/detection_power.txt", "3816 cases" in dp)

# ------------------------------------------------------------------ Nintendo (XBRL extractor + GT builder only)
NG = "bench/results/S100Y9NX/ground_truth_xbrl.json"
ev = [c for c in gt["checks"] if c["status"] != "not_evaluable"]
tmpl = gt["check_summary"]["template"]
sums = [c for c in ev if c["kind"] == "sum"]
claim("README.md", f"{gts['cells']} values", NG, gts["cells"] == 88)
claim("README.md", f"{len(ev)} identity checks evaluated, {sum(c['status'] == 'fail' for c in ev)} failed", NG, True)
claim("docs/RESULTS.md", f"{len(ev)} checks evaluable, {sum(c['status'] == 'fail' for c in ev)} failed", NG, True)
claim("docs/RESULTS.md", f"{tmpl['checks']} template", NG, tmpl["evaluable"] == tmpl["checks"] == 26)
claim("docs/RESULTS.md", f"all {len(sums)} sum checks are 0–3 above", NG,
      min(c["diff"] for c in sums) == 0 and max(c["diff"] for c in sums) == 3)
claim("docs/RESULTS.md", f"{gts['rows']} rows ({gts['value_rows']} with values), {gts['cells']} values", NG, True)
claim("README.md", "PDF parser not yet run on this filing", "bench/results/S100Y9NX/pilot_check.md",
      "pdf_missing" in (ROOT / "bench/results/S100Y9NX/pilot_check.md").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ held-out v3 (Tesseract arm) + OCR ceiling
H3 = "stress/out_heldout_v3/results.json"
h3 = {r["variant"]: r for r in J(H3)["results"]}
h3d = [r for v, r in h3.items() if v != "born_digital"]
mean3 = sum(r["f1"] for r in h3d) / len(h3d)
acc3 = sum(r["decision"] == "accept" for r in h3d)
fa3 = sum(r["false_accept"] for r in h3d)
claim("README.md", f"mean cell F1 {pct(mean3)}; {acc3} of {len(h3d)} accepted, {fa3} false accepts", H3, True)
claim("docs/RESULTS.md", f"Mean cell F1 over the {len(h3d)} degraded pages: **{pct(mean3)}**", H3, True)
claim("docs/RESULTS.md", "No page is fully correct and none is accepted", H3,
      not any(r["fully_correct"] for r in h3d) and acc3 == 0 and fa3 == 0)
lab3 = {"raster_250": "| raster 250 dpi |", "raster_175": "| raster 175 dpi |", "rot_+0.8": "| rotation +0.8° |",
        "rot_-1.2": "| rotation −1.2° |", "noise_s12": "| noise σ=12 |", "jpeg_q40": "| JPEG q=40 |",
        "scan_250": "| 250 dpi + 0.6° + σ=6 + q=55 |", "scan_175": "| 175 dpi − 1.1° + σ=9 + q=45 |"}
for v, key in lab3.items():
    r = h3[v]
    claim("docs/RESULTS.md", f"| {pct(r['f1'])} | {pct(r['calc_parent'])} | {r['checks_failed']} | {r['decision']} |",
          H3, True, line_key=key)
signs = [r["wrong_cells_by_cause"].get("sign lost (△ not read)", 0) for r in h3d]
claim("docs/RESULTS.md", f"△ was lost {min(signs)}–{max(signs)} times per page", H3, True)
claim("docs/RESULTS.md", "Every page failed at least one check (A2)", H3,
      all(any(a.startswith("A2") for a in r["acceptance_reasons"]) for r in h3d))
OC = "stress/out_heldout_v3/ocr_agreement_ceiling.json"
oc = J(OC)
miss = [v["vlm_cells_not_in_ocr"] for v in oc["variants"].values()]
claim("docs/RESULTS.md", f"on {oc['pages_where_a_perfect_read_could_pass_the_ocr_rule']} of {len(oc['variants'])} pages "
      "does Tesseract", OC, True)
claim("docs/RESULTS.md", f"misses {min(miss)}–{max(miss)} of the correct values per page", OC, True)

# ------------------------------------------------------------------ SUBMISSION.md
SB = "SUBMISSION.md"
claim(SB, f"74/74 cells, F1 {pct(o['f1'])}, accepted", G, o["cells"] == "74/74" and o["decision"] == "accept")
claim(SB, f"mean cell F1 {pct(u1['mean_f1_degraded'])} → {pct(u2['mean_f1_degraded'])}", S, True)
claim(SB, f"Tesseract: mean cell F1 {pct(mean3)}", H3, True)
n_all = n_deg + len(h3d)
acc_all = acc_deg + acc3
fa_all = fa + fa3
claim(SB, f"{acc_all} of {n_all} degraded pages accepted, hence {fa_all} false accepts", f"{S} + {H3}", True)
claim(SB, f"{gts['cells']} values, {gts['xbrl_value_mismatch']} mismatches; {len(ev)} checks, "
      f"{sum(c['status'] == 'fail' for c in ev)} failed", NG, gts["sign_differs_from_xbrl"] == 0)
claim("README.md", f"{gts['cells']} values, {gts['xbrl_value_mismatch']} mismatches vs tagged facts", NG, True)
claim(SB, "6 tests skip", "tests/test_real_edinet.py",
      len(re.findall(r"^def test_", (ROOT / "tests/test_real_edinet.py").read_text(encoding="utf-8"), re.M)) == 6)

ones = re.search(r"digit:ones\s+\d+\s+[\d.]+%\s+([\d.]+%)", dp).group(1)
for d in ("docs/RESULTS.md", "docs/VLM.md", "SUBMISSION.md"):
    claim(d, f"catch only {ones} of single ones-digit errors", "gt/out/detection_power.txt", ones == "70.7%")

# ------------------------------------------------------------------ report
bad = [c for c in claims if not (c["in_doc"] and c["matches_file"])]
lines = ["# Claim audit", "", "Generated by `scripts/audit_claims.py`. Each row: a number stated in the docs, the file it "
         "comes from, whether the text is present in the doc, and whether it equals the value recomputed from the file.",
         "", f"**{len(claims) - len(bad)} / {len(claims)} claims verified.**", "",
         "| Doc | Claim | Source | In doc | Matches file | Note |", "|---|---|---|---|---|---|"]
for c in claims:
    lines.append(f"| {c['doc']} | {c['claim'].replace('|', '¦')} | `{c['source']}` | {'yes' if c['in_doc'] else '**NO**'} "
                 f"| {'yes' if c['matches_file'] else '**NO**'} | {c['note']} |")
(ROOT / "docs/AUDIT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"{len(claims) - len(bad)}/{len(claims)} verified")
for c in bad:
    print("MISMATCH:", c["doc"], "|", c["claim"], "| in_doc", c["in_doc"], "| matches", c["matches_file"])
sys.exit(1 if bad else 0)

#!/usr/bin/env bash
# Reproduce every result in the README from the project root:
#     bash scripts/run_all.sh
# Uses $PYTHON if set (e.g. PYTHON=.venv/bin/python), else python3.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
PDF=tests/fixtures/balance_sheet_after_correction.pdf
GT=gt/out/ground_truth

if [ -z "${TESSDATA_PREFIX:-}" ] && [ -d tessdata ]; then export TESSDATA_PREFIX="$PWD/tessdata"; fi

echo "== 1. unit + integration tests"
"$PY" -m pytest -q tests

echo "== 2. original page vs ground truth"
"$PY" -m docparse.evaluate "$PDF" "$GT" runs/eval_born_digital

echo "== 3. geometry variants (scale / shift / larger page)"
"$PY" stress/run_geometry.py "$PDF" "$GT" stress/out_geometry

echo "== 4. stress test (needs tesseract + jpn model)"
if "$PY" -c "from docparse.extract import TesseractBackend; TesseractBackend().check_setup()" 2>/dev/null; then
  # v2 = frozen snapshot of the pipeline the v2 results were produced with (fingerprint 888416e5df3fbc7c)
  DOCPARSE_CODE_ROOT=baselines/v2 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v2_seen stress/specs/seen.json
  DOCPARSE_CODE_ROOT=baselines/v2 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v2_unseen stress/specs/unseen.json
  DOCPARSE_CODE_ROOT=baselines/v2 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_heldout_v3 stress/specs/heldout_v3.json
  "$PY" stress/run_vlm_compare.py --ocr-ceiling > /dev/null
  echo "== 5. round-1 baseline on both sets (frozen snapshot in baselines/round1)"
  DOCPARSE_CODE_ROOT=baselines/round1 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v1_seen stress/specs/seen.json
  DOCPARSE_CODE_ROOT=baselines/round1 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v1_unseen stress/specs/unseen.json
else
  echo "   skipped: Japanese OCR not set up (bash scripts/setup_tesseract.sh)"
fi

echo "== 6. VLM comparison on held-out v3 (offline, from cached model responses)"
if [ -d vlm_cache ]; then
  "$PY" stress/run_vlm_compare.py --cache vlm_cache
else
  echo "   skipped: no vlm_cache/ (the VLM has not been run; see docs/VLM.md)"
fi

echo "== 7. claim audit: every number in the docs vs its source file"
"$PY" scripts/audit_claims.py

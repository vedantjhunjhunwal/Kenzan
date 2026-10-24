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
  "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v2_seen stress/specs/seen.json
  "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v2_unseen stress/specs/unseen.json
  echo "== 5. round-1 baseline on both sets (frozen snapshot in baselines/round1)"
  DOCPARSE_CODE_ROOT=baselines/round1 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v1_seen stress/specs/seen.json
  DOCPARSE_CODE_ROOT=baselines/round1 "$PY" stress/run_stress.py "$PDF" "$GT" stress/out_v1_unseen stress/specs/unseen.json
else
  echo "   skipped: Japanese OCR not set up (bash scripts/setup_tesseract.sh)"
fi

echo "== 6. claim audit: every number in the docs vs its source file"
"$PY" scripts/audit_claims.py

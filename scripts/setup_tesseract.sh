#!/usr/bin/env bash
# Japanese OCR for the visual path.
#
# Option A (Debian/Ubuntu with the jpn package available):
#     sudo apt-get install -y tesseract-ocr tesseract-ocr-jpn
#     -> nothing else needed; leave TESSDATA_PREFIX unset.
#
# Option B (this script; used when the jpn package is unavailable, e.g. restricted mirrors):
#     needs the tesseract binary (>= 4) already installed, plus curl.
#     Builds ./tessdata with the official tessdata_best jpn + jpn_vert models, the system eng/osd models and
#     the configs/ directory, then prints the TESSDATA_PREFIX to export.
#
#     bash scripts/setup_tesseract.sh            # -> ./tessdata
#     export TESSDATA_PREFIX="$PWD/tessdata"
set -euo pipefail

DEST="${1:-$PWD/tessdata}"
BASE_URL="https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main"
MODELS="jpn jpn_vert"   # the jpn model loads jpn_vert for vertical-text detection

command -v tesseract >/dev/null || { echo "tesseract binary not found (apt-get install tesseract-ocr)"; exit 1; }
SYS="$(env -u TESSDATA_PREFIX tesseract --list-langs 2>&1 | sed -n 's/.*"\(.*\)".*/\1/p' | head -1)"
SYS="${SYS%/}"
mkdir -p "$DEST"
if [ "$(cd "$SYS" && pwd)" != "$(cd "$DEST" && pwd)" ]; then
  [ -d "$SYS/configs" ] && cp -r "$SYS/configs" "$DEST/"
  [ -d "$SYS/tessconfigs" ] && cp -r "$SYS/tessconfigs" "$DEST/"
  for m in eng osd; do [ -f "$SYS/$m.traineddata" ] && cp "$SYS/$m.traineddata" "$DEST/"; done
fi

for m in $MODELS; do
  if [ ! -s "$DEST/$m.traineddata" ]; then
    curl -fsSL -o "$DEST/$m.traineddata" "$BASE_URL/$m.traineddata"
  fi
  SIZE=$(wc -c < "$DEST/$m.traineddata")
  [ "$SIZE" -gt 1000000 ] || { echo "$m.traineddata looks wrong ($SIZE bytes)"; exit 1; }
done

TESSDATA_PREFIX="$DEST" tesseract --list-langs
echo
echo "export TESSDATA_PREFIX=\"$DEST\""

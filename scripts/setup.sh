#!/bin/bash
# One-time install into ~/.cache/lip-dictation (about 1.5 GB to download):
#   1. Python environment (PyTorch, mediapipe, OpenCV)
#   2. auto_avsr model code at a pinned commit (only the files inference needs)
#   3. model weights (~1 GB) from the Auto-AVSR model zoo
#   4. the "Lip Dictation" helper app that macOS asks camera access for (built locally)
# Safe to re-run: finished steps are skipped, broken ones are redone.
# Usage: scripts/setup.sh [--skip-model]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${LIPREAD_HOME:-$HOME/.cache/lip-dictation}"
VENDOR="$HOME_DIR/vendor/auto_avsr"
MODELS="$HOME_DIR/models"
LOCK="$HOME_DIR/.setup.lock"

AUTO_AVSR_REPO="https://github.com/mpc001/auto_avsr"
AUTO_AVSR_COMMIT="182b62837773ab01052d4ac21ef1d2203ea7d267"
MODEL_NAME="vsr_trlrs2lrs3vox2avsp_base.pth"
MODEL_GDRIVE_ID="1r1kx7l9sWnDOCnaFHIGvOtzuhFyFA88_"
MODEL_MD5_PREFIX="49f77"   # all the auto_avsr model zoo table publishes
MODEL_SHA256="fbf7cd70ff1c0e694b3030fb779dbb4570f04e4b841d62f9296c229e94878ddb"

SKIP_MODEL=0
[ "${1:-}" = "--skip-model" ] && SKIP_MODEL=1

mkdir -p "$HOME_DIR" "$MODELS"
exec > >(tee -a "$HOME_DIR/setup.log") 2>&1
step() { echo "==> $*"; }
# curl's progress bar redraws one line with \r; turn it into "@@ download <percent>" lines for the plugin.
download_progress() {
  tr '\r' '\n' | awk '
    match($0, /[0-9]+(\.[0-9]+)?%/) {
      p = int(substr($0, RSTART, RLENGTH - 1))
      if (p != last) { print "@@ download " p; fflush(); last = p }
    }'
}
fail() { echo "ERROR: $*"; exit 1; }

if ! mkdir "$LOCK" 2>/dev/null; then
  fail "another setup is running (if not, delete $LOCK)"
fi
# Stop child downloads too when the plugin or the user cancels setup.
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
trap 'trap - TERM INT; kill 0' TERM INT

# GUI apps (Claude Desktop) start with a minimal PATH.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" != "arm64" ]; then
  echo "WARNING: PyTorch no longer ships wheels for Intel Macs; setup will likely fail."
fi
command -v uv >/dev/null 2>&1 || fail "uv is required. Install it with: brew install uv"
command -v git >/dev/null 2>&1 || fail "git is required. Install it with: xcode-select --install"
xcrun --find clang >/dev/null 2>&1 || fail "the Xcode command line tools are required. Install them with: xcode-select --install"

step "1/4 Python environment (~500 MB download)"
# --locked: install exactly the versions in sidecar/uv.lock (tested), never re-resolve.
UV_PROJECT_ENVIRONMENT="$HOME_DIR/venv" uv sync --locked --project "$ROOT/sidecar" --python 3.12 \
  || fail "could not install the Python packages (see $HOME_DIR/setup.log)"

step "2/4 auto_avsr model code @ ${AUTO_AVSR_COMMIT:0:7}"
if [ "$(cat "$VENDOR/.lipread-complete" 2>/dev/null || true)" != "$AUTO_AVSR_COMMIT" ]; then
  rm -f "$VENDOR/.lipread-complete"
  if ! git -C "$VENDOR" rev-parse --git-dir >/dev/null 2>&1; then
    rm -rf "$VENDOR"
    git clone --quiet --filter=blob:none --no-checkout "$AUTO_AVSR_REPO" "$VENDOR" \
      || fail "could not clone $AUTO_AVSR_REPO"
  fi
  # Only what inference needs: skips the ~55 MB of training data files in that repo.
  git -C "$VENDOR" sparse-checkout set --no-cone \
    /LICENSE /espnet/ /datamodule/transforms.py /preparation/detectors/mediapipe/ /spm/unigram/
  # Always run: after an interrupted checkout HEAD may already match while files are missing.
  git -C "$VENDOR" checkout --quiet --force "$AUTO_AVSR_COMMIT" || fail "could not check out auto_avsr"
  echo "$AUTO_AVSR_COMMIT" > "$VENDOR/.lipread-complete"
fi

step "3/4 Model weights ($MODEL_NAME, ~1 GB)"
MODEL="$MODELS/$MODEL_NAME"
if [ "$SKIP_MODEL" = 1 ]; then
  echo "skipped (--skip-model)"
elif [ -f "$MODEL" ]; then
  echo "already downloaded"
else
  cat <<'NOTICE'
NOTE: these pretrained weights come from the Auto-AVSR project (https://github.com/mpc001/auto_avsr).
They carry no explicit license and were trained on LRS2/LRS3 (BBC/TED), VoxCeleb2 and AVSpeech, whose
terms limit use to research and non-commercial purposes. You are responsible for following those terms.
NOTICE
  # Google Drive does not reliably resume, and a quota page would poison a resumed file: start fresh.
  rm -f "$MODEL.part"
  set +o pipefail
  curl --fail --location --retry 3 --progress-bar -o "$MODEL.part" \
    "https://drive.usercontent.google.com/download?id=$MODEL_GDRIVE_ID&export=download&confirm=t" \
    2>&1 >/dev/null | download_progress
  CURL_STATUS=${PIPESTATUS[0]}
  set -o pipefail
  [ "$CURL_STATUS" = 0 ] || { rm -f "$MODEL.part"; fail "model download failed (curl exit $CURL_STATUS)"; }
  if head -c 512 "$MODEL.part" | grep -qi "<html\|<!doctype"; then
    rm -f "$MODEL.part"
    fail "Google Drive returned a web page instead of the model (quota?). Try later, or download it by hand from the auto_avsr README into $MODEL"
  fi
  MD5="$(md5 -q "$MODEL.part" 2>/dev/null || md5sum "$MODEL.part" | cut -d' ' -f1)"
  SHA256="$(shasum -a 256 "$MODEL.part" | cut -d' ' -f1)"
  echo "sha256: $SHA256"
  if [ "${MD5:0:5}" != "$MODEL_MD5_PREFIX" ] || { [ -n "$MODEL_SHA256" ] && [ "$SHA256" != "$MODEL_SHA256" ]; }; then
    rm -f "$MODEL.part"
    fail "checksum mismatch: the download is corrupt or not the expected model"
  fi
  mv "$MODEL.part" "$MODEL"
fi

step "4/4 Camera helper app"
# Camera access goes to the app responsible for a process. Under Claude Desktop that app declares no
# camera use and macOS refuses without asking, so the sidecar runs as this app instead (see main.c).
APP="$HOME_DIR/Lip Dictation.app"
APP_SOURCE="$ROOT/scripts/camera-app"
APP_STAMP="$(cat "$APP_SOURCE/main.c" "$APP_SOURCE/Info.plist" | shasum -a 256 | cut -d' ' -f1)"
# Rebuild only when the sources change: a new binary makes macOS ask for camera access again.
if [ "$(cat "$HOME_DIR/.camera-app-built" 2>/dev/null || true)" != "$APP_STAMP" ] || [ ! -x "$APP/Contents/MacOS/lip-dictation" ]; then
  rm -rf "$APP" "$HOME_DIR/.camera-app-built"
  mkdir -p "$APP/Contents/MacOS"
  cp "$APP_SOURCE/Info.plist" "$APP/Contents/Info.plist"
  xcrun clang -O2 -Wall -o "$APP/Contents/MacOS/lip-dictation" "$APP_SOURCE/main.c" \
    || fail "could not build the camera helper app"
  codesign --force --sign - "$APP" || fail "could not sign the camera helper app"
  echo "$APP_STAMP" > "$HOME_DIR/.camera-app-built"
else
  echo "already built"
fi

step "Checking the install"
/bin/bash "$ROOT/scripts/lipread.sh" check

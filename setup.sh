#!/bin/bash
# setup.sh: build the Python environments for the listening tool and install the Claude Code skill.
# Safe to run again: existing environments are reused, yt-dlp is upgraded, and an existing skill that
# differs from this one is left alone unless you pass --force.
#
#   ./setup.sh               environments + skill
#   ./setup.sh --no-skill    environments only
#   ./setup.sh --skill-only  only (re)install the skill
#   ./setup.sh --force       replace an existing, different skill (the old one is kept as SKILL.md.bak-<time>)
#
# PYTHON=/path/to/python3.13 ./setup.sh   use a specific interpreter (only 3.13 has been tested)
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
TOOL="$ROOT/listening"
DO_ENV=1; DO_SKILL=1; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --no-skill) DO_SKILL=0 ;;
    --skill-only) DO_ENV=0 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nsetup stopped: %s\n' "$*" >&2; exit 1; }

build_envs() {
  say "Checking the machine"
  if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "warning: this was developed and tested only on Apple Silicon macOS. Here: $(uname -s) $(uname -m)."
    echo "         The pinned versions may not install, and the lead/backing split expects Apple's MPS."
  fi
  command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg is not on PATH. Install it (brew install ffmpeg) and run this again."
  command -v ffprobe >/dev/null 2>&1 || die "ffprobe is not on PATH. It comes with ffmpeg (brew install ffmpeg)."
  echo "ffmpeg: $(command -v ffmpeg)"
  if ! command -v cc >/dev/null 2>&1 || ! cc --version >/dev/null 2>&1; then
    die "no C compiler. One dependency (webrtcvad) is built from source: run 'xcode-select --install' first."
  fi

  PY="${PYTHON:-}"
  if [ -z "$PY" ]; then
    for c in python3.13 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
      if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
    done
  fi
  [ -n "$PY" ] || die "Python 3.13 not found. Install it (brew install python@3.13), or set PYTHON=/path/to/python."
  PYV="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" || die "cannot run $PY"
  echo "python: $PY ($PYV)"
  if [ "$PYV" != "3.13" ]; then
    echo "warning: only Python 3.13 has been tested; the pinned versions in listening/constraints.txt are for 3.13."
  fi

  V3="$TOOL/venv3"; VY="$TOOL/venv"
  say "Main environment: $V3 (about 1.2 GB; several minutes the first time)"
  [ -x "$V3/bin/python" ] || "$PY" -m venv "$V3"
  "$V3/bin/python" -m pip install --quiet --upgrade pip
  CONS="$TOOL/constraints.txt"
  # 'setuptools<81' first: webrtcvad (via resemblyzer) still imports pkg_resources.
  "$V3/bin/pip" install --quiet "setuptools<81" -c "$CONS"
  "$V3/bin/pip" install --quiet -c "$CONS" \
      "torch==2.6.0" "torchaudio==2.6.0" demucs==4.1.0 faster-whisper==1.2.1 librosa==1.0.0 \
      matplotlib soundfile praat-parselmouth==0.4.7 resemblyzer==0.1.4
  # resemblyzer drags in the old 'typing' backport; it breaks Python 3.13.
  "$V3/bin/pip" uninstall --quiet -y typing >/dev/null 2>&1 || true
  # Lead/backing split. torch stays pinned through the constraints file: left free, pip pulls a newer torch,
  # which breaks torchaudio's forced aligner. audioread: librosa 1.0 no longer depends on it, audio-separator imports it.
  "$V3/bin/pip" install --quiet -c "$CONS" "audio-separator==0.45.0" audioread
  "$V3/bin/pip" uninstall --quiet -y typing >/dev/null 2>&1 || true

  say "Checking the main environment"
  "$V3/bin/python" - <<'PYEOF' || die "the main environment does not import cleanly (see the error above)"
import torch, torchaudio, demucs, faster_whisper, librosa, matplotlib, soundfile, parselmouth, resemblyzer, PIL, scipy
import pkg_resources  # webrtcvad needs it
print("torch", torch.__version__, "| torchaudio", torchaudio.__version__, "| MPS available:", torch.backends.mps.is_available())
PYEOF
  [ -x "$V3/bin/audio-separator" ] || die "audio-separator did not install into $V3/bin"

  say "yt-dlp environment: $VY (kept apart so it can be upgraded freely)"
  [ -x "$VY/bin/python" ] || "$PY" -m venv "$VY"
  "$VY/bin/python" -m pip install --quiet --upgrade pip
  # [default] adds the helper packages yt-dlp wants for YouTube (its JavaScript challenge scripts among them)
  "$VY/bin/pip" install --quiet --upgrade "yt-dlp[default]"
  command -v deno >/dev/null 2>&1 || echo "note: no deno on PATH. yt-dlp says YouTube without a JavaScript runtime is deprecated (brew install deno)."
  echo "yt-dlp $("$VY/bin/yt-dlp" --version)"
  mkdir -p "$TOOL/runs"
}

install_skill() {
  say "Claude Code skill"
  SRC="$ROOT/skill/listen-to-song/SKILL.md"
  DST_DIR="$HOME/.claude/skills/listen-to-song"
  DST="$DST_DIR/SKILL.md"
  [ -f "$SRC" ] || die "missing $SRC"
  TMP="$(mktemp "${TMPDIR:-/tmp}/listen-skill.XXXXXX")"
  # fill in where this copy of the tool lives (escape the characters sed treats specially)
  ESC="$(printf '%s' "$TOOL" | sed -e 's/[\\&|]/\\&/g')"
  sed -e "s|__TOOL_DIR__|$ESC|g" "$SRC" > "$TMP"
  if grep -q "__TOOL_DIR__" "$TMP"; then rm -f "$TMP"; die "could not fill in the tool path"; fi
  if [ -f "$DST" ]; then
    if cmp -s "$TMP" "$DST"; then
      rm -f "$TMP"; echo "already installed and up to date: $DST"; return 0
    fi
    if [ "$FORCE" != "1" ]; then
      rm -f "$TMP"
      echo "A different listen-to-song skill is already at $DST."
      echo "It was NOT changed. To replace it (the old file is kept as a .bak), run: ./setup.sh --skill-only --force"
      return 0
    fi
    BAK="$DST.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$DST" "$BAK"
    echo "kept the existing skill as $BAK"
  fi
  mkdir -p "$DST_DIR"
  mv "$TMP" "$DST"
  chmod 644 "$DST"
  echo "installed: $DST (tool path: $TOOL)"
}

if [ "$DO_ENV" = "1" ]; then build_envs; fi
if [ "$DO_SKILL" = "1" ]; then install_skill; else say "Skill not installed (--no-skill). Later: ./setup.sh --skill-only"; fi

say "Done"
if [ "$DO_ENV" = "1" ]; then
  echo "Tests (no audio needed):"
  echo "  \"$TOOL/venv3/bin/python\" \"$TOOL/v3/tests/test_seal.py\""
  echo "  \"$TOOL/venv3/bin/python\" \"$TOOL/v3/tests/test_harmony.py\""
fi
echo "Then, in Claude Code (start a new session so it sees the skill):  /listen-to-song Artist, Title"
echo "The first song downloads about 3.8 GB of models."

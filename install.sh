#!/usr/bin/env bash
# Install Yee Nyunn's Editor as an Agent Skill.
#
#   curl -fsSL https://raw.githubusercontent.com/naymintun800/yee-nyunn-editor/main/install.sh | bash
#
# or, if you would rather read it first (sensible — it is 60 lines):
#
#   curl -fsSL https://raw.githubusercontent.com/naymintun800/yee-nyunn-editor/main/install.sh -o install.sh
#   less install.sh && bash install.sh
#
# Options:
#   --dir <path>   install here instead of the autodetected directory
#   --uninstall    remove it again
#
# It copies one directory and writes nothing else. Re-running it updates in place.
set -euo pipefail

REPO=https://github.com/naymintun800/yee-nyunn-editor
SKILL=yee-nyunn-editor
DIR=""
MODE=install

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:?--dir needs a path}"; shift 2 ;;
    --uninstall) MODE=uninstall; shift ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Where skills live differs by client, and more than one may be installed. Rather than
# guess, prefer an explicit choice, then an existing directory, then the Agent Skills
# default.
if [ -z "$DIR" ]; then
  if   [ -n "${YNE_SKILLS_DIR:-}" ];  then DIR="$YNE_SKILLS_DIR"
  elif [ -d "$HOME/.agents/skills" ]; then DIR="$HOME/.agents/skills"
  elif [ -d "$HOME/.codex" ];         then DIR="$HOME/.codex/skills"
  elif [ -d "$HOME/.agents" ];        then DIR="$HOME/.agents/skills"
  else                                     DIR="$HOME/.agents/skills"
  fi
fi

if [ "$MODE" = uninstall ]; then
  if [ -d "$DIR/$SKILL" ]; then rm -rf "${DIR:?}/$SKILL"; echo "removed $DIR/$SKILL"
  else echo "nothing installed at $DIR/$SKILL"; fi
  exit 0
fi

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

# Run from inside a clone if that is where this script lives; otherwise fetch a shallow
# copy into a temp dir that is cleaned up on any exit.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/skills/$SKILL/SKILL.md" ]; then
  SRC="$HERE"
else
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  echo "fetching $REPO ..."
  git clone --depth 1 -q "$REPO" "$TMP/repo"
  SRC="$TMP/repo"
fi

mkdir -p "$DIR"
rm -rf "${DIR:?}/$SKILL"
cp -R "$SRC/skills/$SKILL" "$DIR/$SKILL"
chmod +x "$DIR/$SKILL/scripts/"*.py 2>/dev/null || true

[ -f "$DIR/$SKILL/SKILL.md" ] || { echo "install failed: no SKILL.md at $DIR/$SKILL" >&2; exit 1; }
echo "installed -> $DIR/$SKILL"
echo
echo "Start a new session and ask it to edit a video; it loads when the task matches."
echo "Needs ffmpeg and mpv on PATH. Wrong directory for your client? Re-run with --dir <path>."

# Only warn about things that actually matter at runtime.
for t in ffmpeg mpv; do
  command -v "$t" >/dev/null || echo "note: $t is not on PATH — install it before editing"
done

#!/usr/bin/env bash
# Uninstall the Proton Authenticator bar widget for Omarchy.
#
#   ./uninstall.sh [--dry-run] [--purge] [--keep-legacy] [-h]
#
# Removes the bar placement (through Omarchy, so shell.json stays consistent),
# unloads the widget and removes the plugin folder. By default the folder is
# moved to a hidden backup next to it (ignored by Omarchy); --purge deletes it
# and every earlier backup of this plugin instead. Also retires pre-release ids
# (mp.protonauth, …) unless --keep-legacy is given. Idempotent; no sudo.
# Proton Authenticator itself, its data and your keyring are never touched.
set -euo pipefail

PLUGIN_ID="io.github.mapski.proton-authenticator"
LEGACY_IDS=("mp.protonauth" "mp.proton-authenticator-plugin" "mapski.proton-authenticator")
PLUGINS_DIR="${OMARCHY_PLUGINS_DIR:-$HOME/.config/omarchy/plugins}"
SHELL_JSON="${OMARCHY_SHELL_JSON:-$HOME/.config/omarchy/shell.json}"
TS="$(date +%Y%m%d-%H%M%S)"

DRY_RUN=0
PURGE=0
KEEP_LEGACY=0

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
run() {
  if (( DRY_RUN )); then
    printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}
have() { command -v "$1" >/dev/null 2>&1; }

while (( $# )); do
  case "$1" in
    -n|--dry-run) DRY_RUN=1 ;;
    --purge) PURGE=1 ;;
    --keep-legacy) KEEP_LEGACY=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done
(( EUID != 0 )) || die "run as your normal user, not root"
have jq || die "jq is required"
(( DRY_RUN )) && say "Dry run: nothing will be changed."

ids=("$PLUGIN_ID")
(( KEEP_LEGACY )) || ids+=("${LEGACY_IDS[@]}")

shell_up=0
if have omarchy-shell && [[ $(omarchy-shell shell ping 2>/dev/null || true) == ok ]]; then
  shell_up=1
fi

config=""
(( shell_up )) && config="$(omarchy-shell shell listShellConfig 2>/dev/null || true)"
if [[ -z $config && -f $SHELL_JSON ]]; then config="$(cat -- "$SHELL_JSON")"; fi
[[ -n $config ]] || config='{}'

referenced() {
  jq -e --arg id "$1" '
    ([.bar.layout[]?[]? | (if type=="object" then .id else . end)] | index($id) != null)
    or ([.plugins[]? | (if type=="object" then .id else . end)] | index($id) != null)' \
    <<<"$config" >/dev/null 2>&1
}

backed_up=0
did_something=0
for id in "${ids[@]}"; do
  if referenced "$id"; then
    if (( shell_up )); then
      if (( ! backed_up )) && [[ -f $SHELL_JSON ]]; then
        run cp -p -- "$SHELL_JSON" "$SHELL_JSON.bak-proton-auth-$TS"; backed_up=1
      fi
      say "Removing $id from the bar"
      run omarchy-shell shell setPluginEnabled "$id" false >/dev/null
      did_something=1
    else
      warn "$id is still referenced in $SHELL_JSON but omarchy-shell is not running."
      echo "    Log in and run: omarchy plugin disable $id"
    fi
  fi

  dir="$PLUGINS_DIR/$id"
  if [[ -e $dir || -L $dir ]]; then
    if (( PURGE )); then
      say "Deleting $dir"
      run rm -rf -- "$dir"
    else
      say "Moving $dir -> $PLUGINS_DIR/.$id.bak-$TS"
      run mv -- "$dir" "$PLUGINS_DIR/.$id.bak-$TS"
    fi
    did_something=1
  fi

  if (( PURGE )); then
    shopt -s nullglob
    for b in "$PLUGINS_DIR/.$id.bak-"* "$PLUGINS_DIR/.$id.bak."* "$PLUGINS_DIR/.$id.install."*; do
      say "Deleting old backup $b"
      run rm -rf -- "$b"
      did_something=1
    done
    shopt -u nullglob
  fi
done

# Stray helper temp dirs (normally removed by the helper itself).
shopt -s nullglob
for t in "${TMPDIR:-/tmp}"/protonauth-idb-*; do
  [[ -O $t ]] || continue
  run rm -rf -- "$t"
done
shopt -u nullglob

if (( shell_up )); then run omarchy-shell shell rescanPlugins; fi

if (( did_something )); then
  say "Uninstalled. Proton Authenticator and its data were not touched."
else
  say "Nothing to do: $PLUGIN_ID is not installed."
fi

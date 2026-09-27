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
# Only removes entries it can prove belong to this plugin/installation.
# Proton Authenticator itself, its data and your keyring are never touched.
set -euo pipefail

PLUGIN_ID="io.github.mapski.proton-authenticator"
LEGACY_IDS=("mp.protonauth" "mp.proton-authenticator-plugin" "mapski.proton-authenticator")
PLUGINS_DIR="${OMARCHY_PLUGINS_DIR:-$HOME/.config/omarchy/plugins}"
SHELL_JSON="${OMARCHY_SHELL_JSON:-$HOME/.config/omarchy/shell.json}"
TS="$(date +%Y%m%d-%H%M%S)"
if [[ ${XDG_STATE_HOME:-} == /* ]]; then STATE_BASE="$XDG_STATE_HOME"; else STATE_BASE="$HOME/.local/state"; fi
STATE_DIR="$STATE_BASE/$PLUGIN_ID"
INSTALL_ID_FILE="$STATE_DIR/install-id"
TMP_MARKER=".$PLUGIN_ID"

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

# Move src to a name derived from dest that does not exist yet (never into or
# over an existing entry). mv -n -T uses renameat2(RENAME_NOREPLACE) on GNU
# coreutils, and the post-check catches anything that was not moved.
move_aside() {
  local src="$1" dest="$2" n=1
  while [[ -e $dest || -L $dest ]]; do dest="$2.$n"; n=$((n + 1)); done
  if (( DRY_RUN )); then run mv -n -T -- "$src" "$dest"; return 0; fi
  mv -n -T -- "$src" "$dest"
  [[ ! -e $src && ! -L $src ]] || die "could not move $src aside (target appeared: $dest)"
}

# Back up shell.json to a fresh mktemp file next to it (never through a
# pre-existing path or symlink).
backup_shell_json() {
  [[ -f $SHELL_JSON && -z ${_shell_backed_up:-} ]] || return 0
  if (( DRY_RUN )); then
    echo "[dry-run] back up $SHELL_JSON -> $SHELL_JSON.bak-proton-auth-$TS.XXXXXX"
  else
    local bak
    bak="$(mktemp "$SHELL_JSON.bak-proton-auth-$TS.XXXXXX")" || die "cannot create shell.json backup"
    cp -p -- "$SHELL_JSON" "$bak"
    say "Backed up shell.json -> $bak"
  fi
  _shell_backed_up=1
}

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

# A plugin folder is ours only if it is a real directory (not a symlink)
# owned by us whose manifest.json declares the expected id.
owned_plugin_dir() {
  local d="$1" want="$2" got
  [[ -d $d && ! -L $d && -O $d ]] || return 1
  [[ -f $d/manifest.json && ! -L $d/manifest.json ]] || return 1
  got="$(jq -r '.id // empty' "$d/manifest.json" 2>/dev/null)" || return 1
  [[ $got == "$want" ]]
}

# This installation's id (written by install.sh / the helper). Empty if absent.
install_id=""
if [[ -f $INSTALL_ID_FILE && ! -L $INSTALL_ID_FILE && -O $INSTALL_ID_FILE ]]; then
  IFS= read -r install_id <"$INSTALL_ID_FILE" || true
  [[ $install_id =~ ^[0-9a-f]{32}$ ]] || install_id=""
fi

# A helper temp dir is ours only if it is a real directory owned by us that
# contains a regular marker file (owned by us) holding this installation's id.
owned_tmp_dir() {
  local d="$1" m="$1/$TMP_MARKER" got=""
  [[ -n $install_id ]] || return 1
  [[ -d $d && ! -L $d && -O $d ]] || return 1
  [[ -f $m && ! -L $m && -O $m ]] || return 1
  IFS= read -r got <"$m" || true
  [[ $got == "$install_id" ]]
}

did_something=0
skipped=()
for id in "${ids[@]}"; do
  if referenced "$id"; then
    if (( shell_up )); then
      backup_shell_json
      say "Removing $id from the bar"
      run omarchy-shell shell setPluginEnabled "$id" false >/dev/null
      did_something=1
    else
      warn "$id is still referenced in $SHELL_JSON but omarchy-shell is not running."
      echo "    Log in and run: omarchy plugin disable $id"
    fi
  fi

  dir="$PLUGINS_DIR/$id"
  if [[ -e $dir || -L $dir ]] && ! owned_plugin_dir "$dir" "$id"; then
    skipped+=("$dir")
  elif [[ -e $dir ]]; then
    if (( PURGE )); then
      say "Deleting $dir"
      run rm -rf -- "$dir"
    else
      say "Moving $dir -> $PLUGINS_DIR/.$id.bak-$TS"
      move_aside "$dir" "$PLUGINS_DIR/.$id.bak-$TS"
    fi
    did_something=1
  fi

  if (( PURGE )); then
    shopt -s nullglob
    for b in "$PLUGINS_DIR/.$id.bak-"* "$PLUGINS_DIR/.$id.bak."* "$PLUGINS_DIR/.$id.install."*; do
      if ! owned_plugin_dir "$b" "$id"; then skipped+=("$b"); continue; fi
      say "Deleting old backup $b"
      run rm -rf -- "$b"
      did_something=1
    done
    shopt -u nullglob
  fi
done

# Stray helper temp dirs (normally removed by the helper itself). Only those
# carrying this installation's marker are removed; anything else is left alone.
shopt -s nullglob
for t in "${TMPDIR:-/tmp}"/protonauth-idb-*; do
  if owned_tmp_dir "$t"; then
    say "Deleting helper temp dir $t"
    run rm -rf -- "$t"
  else
    skipped+=("$t")
  fi
done
shopt -u nullglob

# The install id itself (only the file we know; the dir only if now empty).
if (( PURGE )) && [[ -f $INSTALL_ID_FILE && ! -L $INSTALL_ID_FILE && -O $INSTALL_ID_FILE ]]; then
  say "Deleting install id $INSTALL_ID_FILE"
  run rm -f -- "$INSTALL_ID_FILE"
  run rmdir -- "$STATE_DIR" 2>/dev/null || true
fi

if (( ${#skipped[@]} )); then
  warn "left alone (not provably created by this plugin/installation):"
  printf '    %s\n' "${skipped[@]}" >&2
fi

if (( shell_up )); then run omarchy-shell shell rescanPlugins; fi

if (( did_something )); then
  say "Uninstalled. Proton Authenticator and its data were not touched."
else
  say "Nothing to do: $PLUGIN_ID is not installed."
fi

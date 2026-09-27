#!/usr/bin/env bash
# Install / update the Proton Authenticator bar widget for Omarchy.
#
#   ./install.sh [--dry-run] [--no-enable] [--section left|center|right]
#                [--install-deps] [--force] [-h]
#
# Safe to run repeatedly (idempotent). Never uses sudo unless you pass
# --install-deps (package installs go through `omarchy pkg`, which asks for it).
# Migrates pre-release ids (mp.protonauth, …) keeping placement + settings.
set -euo pipefail

PLUGIN_ID="io.github.mapski.proton-authenticator"
LEGACY_IDS=("mp.protonauth" "mp.proton-authenticator-plugin" "mapski.proton-authenticator")
REPO_URL="https://github.com/mapski/omarchy.proton.auth.git-plugin"
PLUGINS_DIR="${OMARCHY_PLUGINS_DIR:-$HOME/.config/omarchy/plugins}"
SHELL_JSON="${OMARCHY_SHELL_JSON:-$HOME/.config/omarchy/shell.json}"
TS="$(date +%Y%m%d-%H%M%S)"
# Per-installation id; the helper stamps it into the temp dirs it creates so
# uninstall.sh can tell them apart from anyone else's (XDG state dir).
if [[ ${XDG_STATE_HOME:-} == /* ]]; then STATE_BASE="$XDG_STATE_HOME"; else STATE_BASE="$HOME/.local/state"; fi
STATE_DIR="$STATE_BASE/$PLUGIN_ID"
INSTALL_ID_FILE="$STATE_DIR/install-id"

# Files that make up the installed plugin (everything else is repo tooling).
PAYLOAD=(manifest.json BarWidget.qml Panel.qml Service.qml Model.js ProtonAuthIcon.qml
         bin/protonauth-list bin/protonauth_list.py assets/icon.svg
         LICENSE README.md CHANGELOG.md)

PKGS=(python-cryptography python-pyotp libsecret wl-clipboard)
AUR_APP="proton-authenticator-bin"

DRY_RUN=0
ENABLE=1
SECTION=""
INSTALL_DEPS=0
FORCE=0
SRC_DIR=""
check_dir=""
stage=""

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

usage() {
  sed -n '2,11p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
  cat <<USAGE

Options:
  -n, --dry-run        Show what would happen; change nothing.
      --no-enable      Copy files only; do not enable or place the widget.
      --section S      Bar section for a fresh placement (left|center|right).
      --install-deps   Install missing packages via 'omarchy pkg add' / 'omarchy pkg aur add'.
      --force          Replace an existing git-managed install (backed up first).
  -h, --help           Show this help.
USAGE
}

# Both variables only ever hold a directory this run created with mktemp -d;
# nothing that existed before the run is ever removed.
cleanup() {
  if [[ -n $check_dir && -d $check_dir && ! -L $check_dir ]]; then rm -rf -- "$check_dir"; fi
  if [[ -n $stage && -d $stage && ! -L $stage ]]; then rm -rf -- "$stage"; fi
  return 0
}
trap cleanup EXIT

while (( $# )); do
  case "$1" in
    -n|--dry-run) DRY_RUN=1 ;;
    --no-enable) ENABLE=0 ;;
    --section) SECTION="${2:-}"; shift ;;
    --install-deps) INSTALL_DEPS=1 ;;
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done
[[ -z $SECTION || $SECTION =~ ^(left|center|right)$ ]] || die "--section must be left, center or right"
(( EUID != 0 )) || die "run as your normal user, not root"

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

# A plugin folder is ours only if it is a real directory (not a symlink)
# owned by us whose manifest.json declares the expected id.
owned_plugin_dir() {
  local d="$1" want="$2" got
  [[ -d $d && ! -L $d && -O $d ]] || return 1
  [[ -f $d/manifest.json && ! -L $d/manifest.json ]] || return 1
  got="$(jq -r '.id // empty' "$d/manifest.json" 2>/dev/null)" || return 1
  [[ $got == "$want" ]]
}
shell_up() { have omarchy-shell && [[ $(omarchy-shell shell ping 2>/dev/null || true) == ok ]]; }

# ---------------------------------------------------------------- source
# Resolve the checkout this script lives in; empty if that fails (checked below).
if script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"; then
  :
else
  script_dir=""
fi
# Run from a checkout of the repository; never download-and-execute.
[[ -n $script_dir && -f $script_dir/manifest.json && -f $script_dir/BarWidget.qml ]] ||
  die "run install.sh from a checkout: git clone $REPO_URL.git && cd omarchy.proton.auth.git-plugin && ./install.sh"
SRC_DIR="$script_dir"
src_id="$(jq -r .id "$SRC_DIR/manifest.json" 2>/dev/null || true)"
[[ $src_id == "$PLUGIN_ID" ]] || die "manifest id '$src_id' != expected '$PLUGIN_ID'"
version="$(jq -r .version "$SRC_DIR/manifest.json")"
say "Proton Authenticator plugin v$version ($PLUGIN_ID)"
(( DRY_RUN )) && say "Dry run: nothing will be changed."

# ---------------------------------------------------------------- deps
have jq || die "jq is required (omarchy pkg add jq)"
have omarchy || warn "'omarchy' CLI not found; is this an Omarchy system?"

missing_pkgs=()
for p in "${PKGS[@]}"; do
  pacman -Q "$p" >/dev/null 2>&1 && continue
  case "$p" in
    # A pip --user install also satisfies the helper.
    python-cryptography) /usr/bin/python3 -c 'import cryptography' 2>/dev/null && continue ;;
    python-pyotp)        /usr/bin/python3 -c 'import pyotp' 2>/dev/null && continue ;;
    libsecret)           have secret-tool && continue ;;
    wl-clipboard)        have wl-copy && have wl-paste && continue ;;
    *) ;;
  esac
  missing_pkgs+=("$p")
done
app_missing=0
[[ -x /usr/bin/proton-authenticator ]] || app_missing=1

if (( ${#missing_pkgs[@]} || app_missing )); then
  if (( ${#missing_pkgs[@]} )); then
    warn "missing packages: ${missing_pkgs[*]}"
    if (( INSTALL_DEPS )); then run omarchy pkg add "${missing_pkgs[@]}"
    else echo "    install with: omarchy pkg add ${missing_pkgs[*]}"; fi
  fi
  if (( app_missing )); then
    warn "Proton Authenticator app not found (/usr/bin/proton-authenticator)"
    if (( INSTALL_DEPS )); then run omarchy pkg aur add "$AUR_APP"
    else echo "    install with: omarchy pkg aur add $AUR_APP"; fi
  fi
  (( INSTALL_DEPS )) || echo "    (or re-run with --install-deps). Continuing; the widget will show a hint."
else
  say "Dependencies OK"
fi

# ---------------------------------------------------------------- install id
ensure_install_id() {
  local id=""
  if [[ -f $INSTALL_ID_FILE && ! -L $INSTALL_ID_FILE ]]; then
    IFS= read -r id <"$INSTALL_ID_FILE" || true
  fi
  [[ $id =~ ^[0-9a-f]{32}$ ]] && return 0
  [[ -e $INSTALL_ID_FILE || -L $INSTALL_ID_FILE ]] && die "unexpected content in $INSTALL_ID_FILE; move it aside and re-run"
  id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  [[ $id =~ ^[0-9a-f]{32}$ ]] || die "could not generate an install id"
  (umask 077 && mkdir -p -- "$STATE_DIR" && set -o noclobber && printf '%s\n' "$id" >"$INSTALL_ID_FILE") ||
    die "could not write $INSTALL_ID_FILE"
  say "Install id stored in $INSTALL_ID_FILE"
}
if (( DRY_RUN )); then
  [[ -f $INSTALL_ID_FILE ]] || echo "[dry-run] create install id -> $INSTALL_ID_FILE"
else
  ensure_install_id
fi

# ---------------------------------------------------------------- stage
target="$PLUGINS_DIR/$PLUGIN_ID"

build_stage() {
  local dest="$1" f
  for f in "${PAYLOAD[@]}"; do
    [[ -f $SRC_DIR/$f && ! -L $SRC_DIR/$f ]] || die "missing payload file: $f"
    mkdir -p -- "$dest/$(dirname -- "$f")"
    cp -p -- "$SRC_DIR/$f" "$dest/$f"
  done
  chmod 0755 "$dest/bin/protonauth-list" "$dest/bin/protonauth_list.py"
}

check_dir="$(mktemp -d -t proton-auth-check.XXXXXX)"
build_stage "$check_dir/p"
if have omarchy; then
  omarchy plugin validate "$check_dir/p" || die "plugin failed 'omarchy plugin validate'"
  say "Manifest validated"
fi

changed=1
if [[ -d $target ]]; then
  if [[ -d $target/.git && $FORCE -eq 0 ]]; then
    say "$target is managed by 'omarchy plugin add' (git checkout)."
    echo "    Update it with: omarchy plugin update $PLUGIN_ID   (or pass --force to replace it)"
    exit 0
  fi
  if diff -rq --exclude=.git "$check_dir/p" "$target" >/dev/null 2>&1; then
    changed=0
    say "Installed files already up to date ($target)"
  fi
fi
rm -rf -- "$check_dir"; check_dir=""

if (( changed )); then
  run mkdir -p -- "$PLUGINS_DIR"
  if (( DRY_RUN )); then
    echo "[dry-run] stage payload -> $(printf '%q' "$PLUGINS_DIR/.${PLUGIN_ID}.install.XXXXXX") (mktemp -d)"
  else
    # Fresh, unpredictable staging dir next to the target (same filesystem, so
    # the final mv is a rename). mktemp never reuses an existing entry.
    stage="$(mktemp -d "$PLUGINS_DIR/.${PLUGIN_ID}.install.XXXXXX")" || die "mktemp failed in $PLUGINS_DIR"
    chmod 0755 -- "$stage"
    build_stage "$stage"
  fi
  if [[ -e $target || -L $target ]]; then
    # Hidden name: Omarchy's plugin scanner ignores dot-dirs, so a backup never
    # shows up as a duplicate plugin. Never reuse an existing name.
    say "Backing up existing install -> $PLUGINS_DIR/.${PLUGIN_ID}.bak-$TS"
    move_aside "$target" "$PLUGINS_DIR/.${PLUGIN_ID}.bak-$TS"
  fi
  if (( DRY_RUN )); then
    echo "[dry-run] rename stage -> $target"
  else
    [[ ! -e $target && ! -L $target ]] || die "$target appeared during install; leaving it alone (staged copy removed)"
    mv -n -T -- "$stage" "$target"
    [[ ! -e $stage ]] || die "$target appeared during install; leaving it alone (staged copy removed)"
    stage=""
  fi
  say "Installed files -> $target"
fi

# ---------------------------------------------------------------- shell / bar
if ! shell_up; then
  warn "omarchy-shell is not running; files are installed."
  echo "    After logging in, run: omarchy plugin enable $PLUGIN_ID --section ${SECTION:-right}"
  exit 0
fi

config="$(omarchy-shell shell listShellConfig)"
in_bar() { jq -e --arg id "$1" '[.bar.layout[]?[]? | (if type=="object" then .id else . end)] | index($id) != null' <<<"$config" >/dev/null 2>&1; }
locate() { jq -c --arg id "$1" '
  [.bar.layout | to_entries[] | .key as $s | .value | to_entries[]
    | select((.value | if type=="object" then .id else . end) == $id)
    | {section: $s, index: .key, entry: .value}][0] // empty' <<<"$config"; }

run omarchy-shell shell rescanPlugins
if (( ! DRY_RUN )); then
  for _ in $(seq 1 60); do
    omarchy plugin list --json 2>/dev/null | jq -e --arg id "$PLUGIN_ID" 'any(.[]; .id == $id)' >/dev/null && break
    sleep 0.1
  done
fi

legacy_loc=""; legacy_id=""
for lid in "${LEGACY_IDS[@]}"; do
  loc="$(locate "$lid")"
  if [[ -n $loc ]]; then legacy_loc="$loc"; legacy_id="$lid"; break; fi
done

if (( ENABLE )); then
  if in_bar "$PLUGIN_ID"; then
    say "Widget already on the bar; leaving placement alone"
  elif [[ -n $legacy_loc ]]; then
    sec="$(jq -r .section <<<"$legacy_loc")"; idx="$(jq -r .index <<<"$legacy_loc")"
    say "Migrating bar placement from $legacy_id (${sec}[${idx}])"
    backup_shell_json
    run omarchy plugin enable "$PLUGIN_ID" --section "$sec" --index "$idx"
    # Carry over any per-widget settings (each passed as its own argv entry).
    legacy_settings="$(jq -r '.entry | if type=="object" then (del(.id) | to_entries[] | [.key, (.value|tojson)] | @tsv) else empty end' <<<"$legacy_loc")"
    while IFS=$'\t' read -r key val; do
      [[ -n $key ]] || continue
      run omarchy bar set "$PLUGIN_ID" "$key" "$val" --json
    done <<<"$legacy_settings"
  else
    backup_shell_json
    if [[ -n $SECTION ]]; then
      run omarchy plugin enable "$PLUGIN_ID" --section "$SECTION"
    else
      run omarchy plugin enable "$PLUGIN_ID"
    fi
  fi
fi

# Retire legacy installs (placement removed via Omarchy; folder backed up).
for lid in "${LEGACY_IDS[@]}"; do
  if in_bar "$lid" || omarchy plugin list --json 2>/dev/null | jq -e --arg id "$lid" 'any(.[]; .id == $id and .enabled)' >/dev/null; then
    backup_shell_json
    say "Disabling legacy widget $lid"
    run omarchy plugin disable "$lid"
  fi
  if owned_plugin_dir "$PLUGINS_DIR/$lid" "$lid"; then
    say "Backing up legacy folder -> $PLUGINS_DIR/.$lid.bak-$TS"
    move_aside "$PLUGINS_DIR/$lid" "$PLUGINS_DIR/.$lid.bak-$TS"
    run omarchy-shell shell rescanPlugins
  elif [[ -e $PLUGINS_DIR/$lid || -L $PLUGINS_DIR/$lid ]]; then
    warn "leaving $PLUGINS_DIR/$lid alone (not a plugin folder with id $lid)"
  fi
done

say "Done. Helper self-test: $target/bin/protonauth-list --check"

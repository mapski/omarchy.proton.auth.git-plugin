# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-10-23

### Fixed
- Honor URI algorithms/digit counts and explicit Steam types; never guess a TOTP
  code for an unsupported entry.
- Invalidate stale helper replies after close, privacy/reveal changes and reloads.
- Keep preference-save errors visible after closing a panel; preference replies
  use their own real/demo context and revision guards.
- Preserve list identities/scroll position at OTP changes and account additions;
  keep header/footer outside the capped, clipped scrolling account list.
- Match card padding to scaled font/layout metrics, including fractional scaling.
- Recompute copied codes and report startup failures; acknowledge next-period copies.

### Added
- Private-by-default reveal, fuzzy search, pins/recents, keyboard shortcuts,
  local issuer glyphs, theme accents, optional close-on-copy and a local-vault age hint.
- Shared service, per-panel reveal permission, ID-only atomic preferences and
  visible retryable preference errors.
- Nonprompt libsecret reads, dump protection, bounded parser/process interfaces,
  and a detached sensitive clipboard owner that expires only its own selection.
- RFC/Steam/encrypted-vault, prefs/lifecycle/clipboard regressions and security policy.
- Privacy-safe bug-report template, weekly GitHub Actions dependency proposals
  and helper/manifest version agreement checks.

### Changed
- System-only Python dependencies; pyotp is no longer needed. No native build step.
- Clipboard expiry is mandatory (5–120 seconds). Legacy disabled expiry migrates to 20.
- Local countdown/boundary refresh replaces refreshIntervalSec polling.
- Document Python erasure/memory-lock limits and clipboard/SQLite trust boundaries.

Published on GitHub as [v1.1.0](https://github.com/mapski/omarchy.proton.auth.git-plugin/releases/tag/v1.1.0).
Marketplace verification is tracked separately from the release.

## [1.0.2] - 2026-10-17

### Fixed
- Use a shared width for the countdown and Next-code column so mixed six- and
  eight-digit accounts stay aligned.

### Changed
- Replace the old desktop crop with a crisp high-DPI render of four demo
  accounts, including an eight-digit code. No real accounts or codes are shown.
- Rendered regression now checks alignment across mixed code lengths.

## [1.0.1] - 2026-10-17

### Fixed
- Long account lists now scroll within the popup instead of painting beyond
  its capped border. Search resets scrolling and keyboard navigation reveals
  the selected card.
- Card heights follow the text and countdown's implicit size, including theme
  font metrics, so their borders enclose the contents.

### Added
- Rendered demo-only layout regression against installed Omarchy components.
- About description and current version in the README.

No new runtime dependencies.

### Security
- Clipboard auto-clear no longer pulls `wl-paste` output into the persistent
  Quickshell process (marketplace review #9048). A short-lived
  `bin/protonauth-clipcheck` child compares at most `len(code)+1` bytes of
  text under a 2 s timeout (KILL after 3 s) and prints only `match` /
  `nomatch`; the code reaches it on stdin, never argv. Non-text clipboards
  are left alone. Helper keyring reads, JSON output and wall-clock runtime
  are likewise bounded, and both Process{}es have a watchdog.
- `install.sh` stages into a fresh `mktemp -d` folder instead of the
  predictable `.<id>.install.<pid>`, and only ever removes folders it created
  in the same run (marketplace review #9048).
- The helper's `protonauth-idb-*` temp dir is created with `mkdtemp()`,
  stamped with a per-installation id marker and always removed by the helper.
- `uninstall.sh` removes temp dirs only with a matching installation marker, and
  plugin folders/backups only if they are real, user-owned folders whose
  manifest declares the expected id; everything else is left alone.
- Issuer/account labels are rendered as plain text (no rich text/remote
  images), and copied codes are passed to `wl-copy` on stdin instead of argv.
- Helper: temp copy removed on SIGTERM/SIGHUP/SIGINT, snapshot file `0600`,
  generic errors no longer echo exception text, `secret-tool` called by
  absolute path.
- `shell.json` and plugin-folder backups use fresh, never-reused names; legacy
  folders are only retired if their manifest carries the legacy id.
- CI: actions pinned to commit SHAs, `persist-credentials: false`, ruff lint.

## [1.0.0] - 2026-09-27

First public release.

### Added
- `BarWidget.qml` entry point that loads `Panel.qml` and forwards the panel
  lifecycle, following Omarchy's plugin develop guide (supports
  `omarchy-shell shell summon|hide`).
- Bar widget with a searchable dropdown of Proton Authenticator TOTP codes:
  current code, next code and a countdown ring per account; click or Enter to copy.
- `protonauth-list` helper that reads the official app's local vault read-only
  and prints labels + codes as JSON (seeds never leave the helper process).
- `protonauth-list --check` self-test (deps, vault, keyring) that prints no codes.
- Clipboard hygiene: copies are marked sensitive (`wl-copy --sensitive`) and
  auto-cleared after 20 s (configurable, only if the clipboard still holds the code).
- Code turns the theme's urgent colour in its last 5 seconds.
- Friendly status hints for every helper error code.
- `protonauth-list --demo` and a `demoMode` widget setting: fake accounts for
  screenshots and trying the UI (never reads the vault).
- Monochrome, theme-following bar icon drawn natively with `QtQuick.Shapes`
  (like Omarchy's built-in Dropbox/Tailscale marks) plus `assets/icon.svg`
  using `currentColor`.
- Startup health check (`--check`, no codes) drives the icon state; codes are
  dropped from memory when the panel closes.
- `preview.png` for the Omarchy plugin marketplace.
- Idempotent `install.sh` / `uninstall.sh` with `--dry-run`, backups and no sudo.
- CI: shellcheck, JSON validation, Python compile and Model.js unit tests.

### Changed
- Plugin id renamed from `mp.protonauth` to `io.github.mapski.proton-authenticator`
  (the `io.github.<user>.<name>` form from Omarchy's develop guide; `omarchy.*` is reserved)
  (repo: `mapski/omarchy.proton.auth.git-plugin`). `install.sh` migrates the old
  widget's bar placement and settings automatically.

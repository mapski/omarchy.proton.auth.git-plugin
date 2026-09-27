# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security
- `install.sh` stages into a fresh `mktemp -d` folder instead of the
  predictable `.<id>.install.<pid>`, and only ever removes folders it created
  in the same run (marketplace review #9048).
- The helper's `protonauth-idb-*` temp dir is created with `mkdtemp()`,
  stamped with a per-installation id marker and always removed by the helper.
- `uninstall.sh` removes temp dirs only with a matching installation marker, and
  plugin folders/backups only if they are real, user-owned folders whose
  manifest declares the expected id; everything else is left alone.

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

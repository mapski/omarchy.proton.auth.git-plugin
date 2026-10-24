# Proton Authenticator for Omarchy

Search and copy Proton Authenticator codes from the Omarchy bar. The plugin
reads the official app’s local vault; Proton keeps account management and sync.
It runs locally without a native build step or an additional system daemon.

**1.1.0 candidate — local validation in progress.** This update corrects OTP
format handling, adds private-by-default codes, fuzzy search, pinned/recent
accounts and keyboard controls, and preserves scroll position across refreshes.
The popup has a capped frame with a separately scrolling account list.

Independent community plugin by mapski. Not affiliated with or endorsed by
Proton AG. The screenshot below is rendered from the candidate using public demo
accounts with codes hidden. No real account or code is shown.

![1.1.0 demo: hidden codes, bounded list and active scrollbar](preview.png)

## Install and update

```sh
omarchy plugin add https://github.com/mapski/omarchy.proton.auth.git-plugin.git --enable
omarchy pkg add python-cryptography libsecret wl-clipboard
omarchy pkg aur add proton-authenticator-bin
```

Package installation may require administrative approval. The plugin itself
runs as your user. Open the official app once and set up its keyring-backed
vault. Password-backed and insecure fallback key storage are not supported.

```sh
omarchy plugin update io.github.mapski.proton-authenticator
```

No compiler or post-update build is needed. These commands install the published
GitHub version; an unpublished local candidate does not change what others get.

Requirements: Omarchy 4 with the Quattro plugin API, the official desktop app,
Secret Service (`libsecret`, normally gnome-keyring), system Python 3 with
`cryptography`, and `wl-clipboard` with `--sensitive` support (2.3 or newer).
The helper uses `/usr/bin/python3 -E -s`: user-site packages and Python environment
overrides do not replace its dependencies. `pyotp` is no longer required.
The standard-library HMAC implementation is covered by literal RFC 4226/6238
vectors and Steam fixtures.

## Usage

Click the bar icon, or run:

```sh
omarchy-shell shell summon io.github.mapski.proton-authenticator '{}'
```

| Action | Control |
|---|---|
| Search | Start typing; search is focused on open, with fuzzy issuer/account matching |
| Select and reveal | Arrow keys, Ctrl+J/K, or click a hidden row |
| Copy | Enter or click an already revealed row |
| Move five rows | PgUp / PgDn |
| Pin or unpin | Ctrl+P or right-click a row; a star marks a pin |
| Hide/show codes | Ctrl+H or the Hidden/Visible label |
| Clear search / close | Esc clears a nonempty search; another Esc closes |
| Next / previous bar panel | Tab / Shift+Tab |
| Add, edit or sync | + Add opens the official app |
| Refresh | Right-click the bar icon |
| Unlock / retry | Click the error hint; Enter when there is no selectable row |
| Retry preferences | Click the footer’s preference error |

Codes are hidden by default. In private mode selecting a row reveals its code;
copying takes a second action. Reveal permission belongs to that panel and is
cleared when it closes. Privacy, pinned IDs and the last five copied IDs are
remembered locally. Recent ordering is frozen while a panel is open.

Copying recalculates the selected account’s code. With five seconds or less
remaining it copies the **next** code and says “Next code copied”; that code
may not be accepted until the next period begins. Clipboard startup failures
show an error instead of a success highlight. By default the panel closes after
successful copy feedback.

The footer’s “Vault unchanged” hint uses local database modification time.
It does **not** prove whether Proton sync has succeeded or failed.

## Settings

```sh
omarchy bar set io.github.mapski.proton-authenticator clipboardClearSec 20
omarchy bar set io.github.mapski.proton-authenticator closeOnCopy false --json
omarchy bar set io.github.mapski.proton-authenticator staleHours 24
omarchy bar set io.github.mapski.proton-authenticator demoMode true --json
```

| Setting | Default | Range / behavior |
|---|---|---|
| `clipboardClearSec` | 20 | 5–120 seconds; legacy 0 no longer disables expiry |
| `closeOnCopy` | true | Close after successful copy feedback |
| `staleHours` | 24 | 1–168 hours without a local vault change |
| `demoMode` | false | Fake public test accounts; never reads the vault/keyring or saves demo preferences |

Countdowns tick locally. Helper refreshes occur at code boundaries, account
selection and detected vault changes; the old `refreshIntervalSec` setting is
retired. Health checks run periodically without unlocking the keyring.

## Security properties and limits

Security is a design priority, not a certification. See [SECURITY.md](SECURITY.md)
for the threat model and reporting guidance.

- **Local-only runtime.** No network requests, remote issuer images, telemetry
  or built-in update checks. Updates are explicitly performed by Omarchy/Git.
- **Read-only vault workflow.** SQLite opens the source read-only and creates a
  private encrypted snapshot in a fresh 0700 temporary directory, with a 0600
  database. Cleanup is scoped to files created by this installation. SQLite
  WAL handling can access companion files; this is not a promise of zero
  filesystem activity in the source directory.
- **Explicit formats.** TOTP SHA-1/256/512 and Steam are implemented. Other types
  are non-copyable unsupported rows. Authentication/decryption errors fail the
  request; the helper never guesses codes or returns seeds.
- **Bounded helper boundary.** Size/count limits, deadlines, validated IDs,
  fixed child arguments, capped output and plain-text labels. The shell gets
  labels and only deliberately exposed codes, never seeds or decrypted objects.
  Late replies are invalidated when panels close or privacy changes.
- **Privacy without an unlocking surprise.** Ordinary reads do not request
  keyring unlocking. Unlock is an explicit action. Recent upstream formats
  encrypt labels too, so a private listing may decrypt entries to read labels;
  hiding codes is not a guarantee that only one entry is decrypted.
- **Process hardening.** Sensitive Python workers disable core dumps and set
  Linux non-dumpability. Memory locking is best effort for current pages;
  future allocations and immutable Python copies are not guaranteed locked
  or erased. This does not isolate the plugin from malware running as you.
- **Clipboard ownership.** A short-lived detached worker receives a code over
  stdin, starts `wl-copy --foreground --sensitive`, and ends only its own
  clipboard owner at expiry. It survives shell reloads and never reads the
  clipboard or runs a global clear that could erase a later selection.
  The startup acknowledgment checks for immediate process failure, not proof
  that a particular destination received the code. Clipboard managers may
  retain their own copy despite sensitivity/expiry; pasted applications have it.

Revealed codes can appear in screenshots. QML and Python cannot guarantee
cryptographic erasure of previously allocated strings. Omarchy plugins share
an unsandboxed shell process. Review the code and the documented boundaries.

## Compatibility and diagnostics

The local vault format is not a supported Proton integration API. Future
Proton or WebKit changes may require a plugin update. Fixtures cover legacy
plaintext labels and newer encrypted-label payloads; this is not a promise
that every future app version works.

```sh
~/.config/omarchy/plugins/io.github.mapski.proton-authenticator/bin/protonauth-list --check
```

`--check` reports readiness and no codes. It can include a local vault path;
redact local paths/counts before sharing diagnostics. Do not post real account
names, codes, seeds, keyring data or vault files.

| Error / symptom | Next step |
|---|---|
| Missing dependency | Install the listed system packages; user-site Python packages are ignored |
| Vault unavailable | Open the official app and set it up once |
| Keyring locked | Use the explicit Unlock/retry action |
| Unsupported key storage | Use the official app’s keyring-backed storage; do not weaken vault protection |
| Unsupported schema / decryption failure | Report app/WebKit versions with a synthetic reproduction, never vault contents |
| Preferences unavailable / not saved | Click the footer to retry; malformed files require deliberate repair |
| Copy failed | Check wl-clipboard and the Wayland session; retry without exposing codes |

## Removal and rollback

```sh
omarchy plugin remove io.github.mapski.proton-authenticator
```

This removes the plugin checkout/bar registration. It leaves the official app,
its vault/keyring and shared dependencies untouched. The plugin’s own state
folder `${XDG_STATE_HOME:-~/.local/state}/io.github.mapski.proton-authenticator`
contains an installation marker and ID-only preferences; remove that folder
separately if you want to discard preferences. Do not delete similarly named
files belonging to another installation. Installer backups are documented by
`install.sh`/`uninstall.sh`; review `--dry-run` before cleanup.

For rollback use the saved prior plugin checkout and matching version, then
reload through Omarchy. Do not hard-reset unrelated installed changes.

## Development

```sh
omarchy plugin validate .
python3 -m py_compile bin/protonauth_list.py bin/protonauth_clipboard.py
node --test tests/*.test.js
python3 -m unittest discover -s tests -p 'test_*.py'
# Running Wayland session; synthetic popup only:
PROTONAUTH_QML_TEST=1 node --test tests/layout.test.js
```

Python tests need cryptography in the test environment (CI installs it explicitly).
Tests use public RFC seeds, encrypted synthetic vaults and fake clipboard/keyring
boundaries. The rendered regression checks card bounds, mixed code lengths,
long lists, five added accounts, scroll preservation and close-during-refresh.
A local integration test can exercise the real plugin updater against a local
Git remote; publication/update verification from GitHub requires an approved push.

[Changelog](CHANGELOG.md) · [MIT license](LICENSE) © 2026 mapski.

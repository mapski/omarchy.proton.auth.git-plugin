# Security policy

This community plugin handles authentication material. We prioritize correct
codes, limited exposure, bounded external inputs and conservative failure.
Marketplace verification is not a security audit or certification.

## Supported version

The current supported release is **1.1.0**. Security fixes target the current
published release. Keep the official app and system runtime dependencies
updated as well.

## Reporting

Use GitHub private vulnerability reporting if it is enabled for this repository.
If it is unavailable, open an issue asking for a private contact without details
of the vulnerability. Never attach a real vault, seed, code, account list,
keyring output or clipboard content. Public RFC seeds and synthetic fixtures
are the preferred way to reproduce problems.

## Trust boundaries

The official Proton app owns accounts, sync and encrypted storage. This helper
reads its local database and Secret Service key, computes codes in a separate
process, and emits a bounded JSON view to the unsandboxed Omarchy shell.
Privacy mode hides all but an explicitly selected code; encrypted labels may
still require decrypting every item within the helper. Nothing here is an
application-specific keyring access policy or a defense against same-user malware.

Core dumps and Linux dumpability are disabled for sensitive workers. Best-effort
locking covers currently allocated pages only; Python/crypto/Qt copies, future
allocations, allocator reuse and erasure are not guaranteed. No claim of reliable
zeroization is made. Root/privileged processes remain outside this boundary.

The source SQLite connection is read-only; backup snapshots contain encrypted
records and use private permissions. WAL/SQLite companion-file behavior is not
reimplemented or guaranteed side-effect free. Snapshot ownership markers scope
cleanup. Database discovery, rows, labels, keys, stdin/stdout and child runtime
are bounded; unexpected crypto/schema errors return no partial codes.

Ordinary keyring access does not request Unlock; a bounded libsecret worker
loads only already-unlocked items. Explicit user Unlock may show a system prompt.
Children use absolute executable paths, stdin for code transfer, closed inherited
file descriptors and a reduced environment. Codes/seeds never belong in argv,
logs, persisted preferences or screenshots used for reports.

The detached clipboard owner expires only its own foreground wl-copy process.
It cannot revoke copies made by clipboard managers or destination applications.
The sensitive hint is advisory, and wl-copy’s internal storage is outside this
plugin’s control. Successful startup is not proof of end-to-end paste delivery.

Preferences contain account IDs, privacy mode, pins and recents, not labels or
codes. IDs can still reveal usage patterns to a process that can read your user
files or process arguments. State uses private permissions and atomic writes;
read/save errors remain visible and can be retried.

## Review expectations

Review changes to URI/OTP semantics, keyring prompting, parsing/bounds, temporary
file ownership, subprocess lifecycle, clipboard expiry, asynchronous UI state,
multiple panels and update/install scripts. Require synthetic regressions for
new formats and privacy races, real QML layout checks for UI changes, and the
standard Omarchy updater integration before publication. Document review gaps
and runtime evidence rather than claiming universal security superiority.

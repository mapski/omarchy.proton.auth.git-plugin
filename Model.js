.pragma library

function parseList(raw) {
  try {
    var data = JSON.parse(String(raw || ""))
    if (!data || typeof data !== "object")
      return { ok: false, entries: [], error: { code: "DECRYPT_FAILED", message: "Invalid JSON" } }
    if (!Array.isArray(data.entries)) data.entries = []
    return data
  } catch (e) {
    return { ok: false, entries: [], error: { code: "DECRYPT_FAILED", message: String(e) } }
  }
}

function formatCode(code) {
  var s = String(code || "").replace(/\s+/g, "")
  if (s.length === 6) return s.slice(0, 3) + " " + s.slice(3)
  if (s.length === 8) return s.slice(0, 4) + " " + s.slice(4)
  return s
}

function filterEntries(entries, query) {
  var q = String(query || "").trim().toLowerCase()
  var list = entries || []
  if (!q) return list
  var out = []
  for (var i = 0; i < list.length; i++) {
    var e = list[i]
    var hay = (String(e.issuer || "") + " " + String(e.account || "")).toLowerCase()
    if (hay.indexOf(q) !== -1) out.push(e)
  }
  return out
}

function remainingFraction(remaining, period) {
  var p = Number(period) || 30
  var r = Number(remaining) || 0
  if (p <= 0) return 0
  return Math.max(0, Math.min(1, r / p))
}

function entryKey(entry) {
  return String((entry && (entry.id || "")) || "") + "|" + String((entry && entry.issuer) || "") + "|" + String((entry && entry.account) || "")
}

// Human-readable status for helper error codes (see README › Troubleshooting).
function errorHint(error) {
  if (!error) return ""
  var code = String(error.code || "")
  var hints = {
    DEPS_MISSING: "Missing dependency — see README (python-cryptography, python-pyotp, libsecret)",
    VAULT_UNREADABLE: "Open Proton Authenticator once and sign in or add a code",
    KEY_MISSING: "Vault key not in keyring — open Proton Authenticator once",
    KEYRING_LOCKED: "Keyring is locked — unlock it and retry",
    UNSUPPORTED_SCHEMA: "Unsupported Proton Authenticator version — please file an issue",
    HELPER_TIMEOUT: "Timed out reading the vault or keyring — unlock the keyring and retry",
    OUTPUT_TOO_LARGE: "Vault too large to display (entry or size limit reached)",
    DECRYPT_FAILED: "Could not decrypt the vault"
  }
  var hint = hints[code] || ""
  var msg = String(error.message || code || "Error")
  return hint ? hint + " (" + code + ")" : msg
}

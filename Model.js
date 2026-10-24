.pragma library

// This boundary accepts only bounded helper data. Never include parser errors:
// JSON exception messages can quote the response, including an OTP.
function invalidReply(code) {
  return { ok: false, entries: [], error: { code: code || "INVALID_REPLY", message: "Invalid helper response" } }
}

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function finiteNumber(value) {
  return typeof value === "number" && isFinite(value)
}

function integerIn(value, min, max) {
  return finiteNumber(value) && Math.floor(value) === value && value >= min && value <= max
}

function validId(value) {
  return typeof value === "string" && value.length <= 128 && /^[A-Za-z0-9._:][A-Za-z0-9._:-]*$/.test(value)
}

function text(value, limit) {
  return typeof value === "string" ? value.slice(0, limit) : ""
}

function boundedUtf8(value) {
  if (value.length > 1048576) return false
  var bytes = 0
  for (var i = 0; i < value.length; i++) {
    var c = value.charCodeAt(i)
    if (c < 128) bytes++
    else if (c < 2048) bytes += 2
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < value.length && value.charCodeAt(i + 1) >= 0xdc00 && value.charCodeAt(i + 1) <= 0xdfff) {
      bytes += 4
      i++
    } else bytes += 3
    if (bytes > 1048576) return false
  }
  return true
}

function normalizedError(error) {
  if (!record(error) || typeof error.code !== "string" || !/^[A-Z][A-Z0-9_]{0,63}$/.test(error.code)) return null
  return { code: error.code, message: text(error.message, 200) }
}

function parsedEntry(row) {
  if (!record(row) || !validId(row.id) || typeof row.issuer !== "string" || row.issuer.length > 256 || typeof row.account !== "string" || row.account.length > 256) return null
  var kind = row.kind === undefined ? "totp" : row.kind
  var hidden = row.code === null && row.nextCode === null && row.period === null && row.digits === null && row.remaining === null
  if (hidden) {
    if (kind !== null && kind !== "unsupported") return null
  } else {
    if (kind !== "totp" && kind !== "steam") return null
    if (!integerIn(row.period, 1, 86400) || !integerIn(row.digits, 1, 10) || !finiteNumber(row.remaining) || row.remaining < 0 || row.remaining > row.period) return null
    var pattern = kind === "steam" ? /^[23456789BCDFGHJKMNPQRTVWXY]{5}$/ : /^[0-9]{1,10}$/
    if (kind === "steam" && row.digits !== 5) return null
    if (typeof row.code !== "string" || !pattern.test(row.code) || row.code.length !== row.digits || typeof row.nextCode !== "string" || !pattern.test(row.nextCode) || row.nextCode.length !== row.digits) return null
  }
  var result = { id: row.id, issuer: row.issuer, account: row.account,
    code: row.code, nextCode: row.nextCode, period: row.period, digits: row.digits,
    remaining: row.remaining, kind: kind, order: finiteNumber(row.order) ? Math.max(-1000000000, Math.min(1000000000, row.order)) : 0 }
  var error = normalizedError(row.error)
  if (error) result.error = error
  return result
}

function parseList(raw) {
  if (typeof raw !== "string") return invalidReply()
  if (!boundedUtf8(raw)) return invalidReply("OUTPUT_TOO_LARGE")
  var data
  try { data = JSON.parse(raw) } catch (e) { return invalidReply() }
  if (!record(data) || typeof data.ok !== "boolean" || !Array.isArray(data.entries)) return invalidReply()
  if (data.entries.length > 1000) return invalidReply("OUTPUT_TOO_LARGE")
  var error = normalizedError(data.error)
  if (!data.ok && !error) return invalidReply()
  if (data.ok && data.error !== undefined && data.error !== null) return invalidReply()
  var out = { ok: data.ok, entries: [], error: error }
  if (data.ok) {
    var ids = []
    for (var i = 0; i < data.entries.length; i++) {
      var row = parsedEntry(data.entries[i])
      if (!row || ids.indexOf(row.id) !== -1) return invalidReply()
      ids.push(row.id)
      out.entries.push(row)
    }
  }
  if (typeof data.updatedAt === "string" && data.updatedAt.length <= 32 && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(data.updatedAt)) out.updatedAt = data.updatedAt
  if (finiteNumber(data.now) && data.now >= 0 && data.now <= 8640000000000) out.now = data.now
  if (finiteNumber(data.vaultMtime) && data.vaultMtime >= 0 && data.vaultMtime <= 8640000000000) out.vaultMtime = data.vaultMtime
  if (typeof data.demo === "boolean") out.demo = data.demo
  if (record(data.prefs)) out.prefs = normalizePrefs(data.prefs)
  if (record(data.copy)) {
    out.copy = {}
    if (validId(data.copy.id)) out.copy.id = data.copy.id
    if (typeof data.copy.usedNext === "boolean") out.copy.usedNext = data.copy.usedNext
    if (integerIn(data.copy.clearAfter, 0, 300)) out.copy.clearAfter = data.copy.clearAfter
  }
  // Health metadata deliberately excludes vault paths and arbitrary strings.
  if (record(data.check)) {
    out.check = {}
    if (integerIn(data.check.items, 0, 1000)) out.check.items = data.check.items
    if (data.check.keyring === "unlocked" || data.check.keyring === "locked") out.check.keyring = data.check.keyring
  }
  if (record(data.diagnostics)) {
    out.diagnostics = {}
    var booleans = ["coreDumpsDisabled", "nonDumpable", "memoryLocked"]
    for (var j = 0; j < booleans.length; j++) {
      var key = booleans[j]
      if (typeof data.diagnostics[key] === "boolean") out.diagnostics[key] = data.diagnostics[key]
    }
  }
  return out
}

function formatCode(code) {
  var s = (finiteNumber(code) ? String(code) : text(code, 32)).replace(/\s+/g, "")
  if (s.length === 6) return s.slice(0, 3) + " " + s.slice(3)
  if (s.length === 8) return s.slice(0, 4) + " " + s.slice(4)
  return s
}

function idList(raw, limit) {
  var out = []
  if (!Array.isArray(raw)) return out
  // Preferences are tiny even when called directly with hostile local input.
  for (var i = 0; i < Math.min(raw.length, 1000) && out.length < limit; i++) {
    if (validId(raw[i]) && out.indexOf(raw[i]) === -1) out.push(raw[i])
  }
  return out
}

function normalizePrefs(raw) {
  var prefs = record(raw) ? raw : {}
  return { privacy: typeof prefs.privacy === "boolean" ? prefs.privacy : true,
    pinned: idList(prefs.pinned, 100), recent: idList(prefs.recent, 5) }
}

function updatePrefs(raw, operation, idOrBool) {
  var prefs = normalizePrefs(raw)
  if (operation === "privacy") {
    if (typeof idOrBool === "boolean") prefs.privacy = idOrBool
  } else if (validId(idOrBool)) {
    var index
    if (operation === "pin") {
      index = prefs.pinned.indexOf(idOrBool)
      if (index !== -1) prefs.pinned.splice(index, 1)
      else if (prefs.pinned.length < 100) prefs.pinned.push(idOrBool)
    } else if (operation === "recent") {
      index = prefs.recent.indexOf(idOrBool)
      if (index !== -1) prefs.recent.splice(index, 1)
      prefs.recent.unshift(idOrBool)
      prefs.recent = prefs.recent.slice(0, 5)
    }
  }
  return prefs
}

// Lower scores are better. Contiguous issuer matches precede subsequences;
// account matches are deliberately below issuer matches. Every token must match.
function fuzzyScore(label, token) {
  var at = label.indexOf(token)
  if (at !== -1) return at === 0 ? 0 : 10 + Math.min(at, 9)
  var previous = -1
  var first = -1
  for (var i = 0; i < token.length; i++) {
    previous = label.indexOf(token.charAt(i), previous + 1)
    if (previous === -1) return -1
    if (first === -1) first = previous
  }
  return 20 + previous - first - token.length + 1 + first
}

function compareText(a, b) {
  return a < b ? -1 : (a > b ? 1 : 0)
}

function filterAndOrder(entries, query, rawPrefs, recentSnapshot) {
  var prefs = normalizePrefs(rawPrefs)
  var recent = Array.isArray(recentSnapshot) ? idList(recentSnapshot, 5) : prefs.recent
  var q = text(query, 256).trim().toLowerCase()
  var tokens = q ? q.split(/\s+/) : []
  var list = Array.isArray(entries) ? entries : []
  var matches = []
  for (var i = 0; i < Math.min(list.length, 1000); i++) {
    var row = list[i]
    if (!record(row) || !validId(row.id)) continue
    var issuer = text(row.issuer, 256).toLowerCase()
    var account = text(row.account, 256).toLowerCase()
    var score = 0
    for (var j = 0; j < tokens.length; j++) {
      var a = fuzzyScore(issuer, tokens[j])
      var b = fuzzyScore(account, tokens[j])
      if (b !== -1) b += 100
      var best = a === -1 ? b : (b === -1 ? a : Math.min(a, b))
      if (best === -1) { score = -1; break }
      score += best
    }
    if (score < 0) continue
    var recentIndex = recent.indexOf(row.id)
    matches.push({ row: row, score: score, pinned: prefs.pinned.indexOf(row.id) !== -1 ? 0 : 1,
      recent: recentIndex === -1 ? 5 : recentIndex, issuer: issuer, account: account, index: i })
  }
  matches.sort(function(a, b) {
    return a.score - b.score || a.pinned - b.pinned || a.recent - b.recent || compareText(a.issuer, b.issuer) || compareText(a.account, b.account) || compareText(a.row.id, b.row.id) || a.index - b.index
  })
  return matches.map(function(item) { return item.row })
}

// Compatibility for consumers that still only need a plain text filter.
function filterEntries(entries, query) {
  var q = text(query, 256).trim().toLowerCase()
  var list = Array.isArray(entries) ? entries : []
  var out = []
  for (var i = 0; i < Math.min(list.length, 1000); i++) {
    var e = list[i]
    if (!record(e)) continue
    var hay = (text(e.issuer, 256) + " " + text(e.account, 256)).toLowerCase()
    if (!q || hay.indexOf(q) !== -1) out.push(e)
  }
  return out
}

function findIndexById(entries, id) {
  if (!Array.isArray(entries) || !validId(id)) return -1
  for (var i = 0; i < Math.min(entries.length, 1000); i++) {
    if (record(entries[i]) && entries[i].id === id) return i
  }
  return -1
}

function selectionAfterRefresh(entries, selectedId) {
  if (findIndexById(entries, selectedId) !== -1) return selectedId
  if (Array.isArray(entries)) {
    for (var i = 0; i < Math.min(entries.length, 1000); i++) {
      if (record(entries[i]) && validId(entries[i].id)) return entries[i].id
    }
  }
  return ""
}

function liveRemaining(entry, replyNowSec, nowSec) {
  if (!record(entry) || typeof entry.code !== "string" || !entry.code || !finiteNumber(entry.remaining) || !integerIn(entry.period, 1, 86400) || !finiteNumber(replyNowSec) || !finiteNumber(nowSec)) return 0
  return Math.max(0, Math.min(entry.period, entry.remaining) - Math.max(0, nowSec - replyNowSec))
}

function refreshDelayMs(entries, replyNowSec, nowMs) {
  if (!Array.isArray(entries) || !finiteNumber(replyNowSec) || !finiteNumber(nowMs)) return -1
  var delay = -1
  for (var i = 0; i < Math.min(entries.length, 1000); i++) {
    var e = entries[i]
    if (!record(e) || typeof e.code !== "string" || !e.code || !finiteNumber(e.remaining) || !integerIn(e.period, 1, 86400)) continue
    var next = Math.ceil(liveRemaining(e, replyNowSec, nowMs / 1000) * 1000) + 100
    if (delay === -1 || next < delay) delay = next
  }
  return delay
}

function hiddenEntries(entries, revealId) {
  var out = []
  if (!Array.isArray(entries)) return out
  for (var i = 0; i < Math.min(entries.length, 1000); i++) {
    var row = parsedEntry(entries[i])
    if (!row) continue
    if (row.id !== revealId) {
      row.code = null
      row.nextCode = null
      row.period = null
      row.digits = null
      row.remaining = null
      row.kind = null
    }
    out.push(row)
  }
  return out
}

function remainingFraction(remaining, period) {
  var p = (typeof period === "number" || typeof period === "string" ? Number(period) : 0) || 30
  var r = (typeof remaining === "number" || typeof remaining === "string" ? Number(remaining) : 0) || 0
  if (!isFinite(p) || !isFinite(r) || p <= 0) return 0
  return Math.max(0, Math.min(1, r / p))
}

function entryKey(entry) {
  return (record(entry) ? text(entry.id, 128) + "|" + text(entry.issuer, 256) + "|" + text(entry.account, 256) : "||")
}

function issuerGlyph(issuer) {
  var label = text(issuer, 256).trim()
  var name = label.toLowerCase().replace(/[^a-z0-9]/g, "")
  var glyphs = { github: "\uf09b", gitlab: "\uf296", google: "\uf1a0", gmail: "\uf1a0", proton: "\uf0e0", protonmail: "\uf0e0", aws: "\uf270", amazon: "\uf270", amazonwebservices: "\uf270", discord: "\udb81\ude6f", microsoft: "\uf17a", apple: "\uf179", facebook: "\uf09a", dropbox: "\uf16b", steam: "\uf1b6", cloudflare: "\uefa2", twitter: "\uf099", x: "\uf099" }
  if (Object.prototype.hasOwnProperty.call(glyphs, name)) return glyphs[name]
  return label ? label.charAt(0).toUpperCase() : "?"
}

function errorHint(error) {
  if (!record(error)) return ""
  var code = text(error.code, 64)
  var hints = {
    DEPS_MISSING: "Missing dependency — see README (python-cryptography, python-pyotp, libsecret)",
    VAULT_UNREADABLE: "Open Proton Authenticator once and sign in or add a code",
    KEY_MISSING: "Vault key not in keyring — open Proton Authenticator once",
    KEYRING_LOCKED: "Keyring is locked — unlock it and retry",
    APP_LOCKED: "Open and unlock Proton Authenticator, then retry",
    UNSUPPORTED_SCHEMA: "Unsupported Proton Authenticator version — please file an issue",
    UNSUPPORTED_ENTRY: "This account type is unsupported — open Proton Authenticator",
    HELPER_OUTDATED: "Update the plugin helper and reload the shell",
    HELPER_TIMEOUT: "Timed out reading the vault or keyring — unlock the keyring and retry",
    OUTPUT_TOO_LARGE: "Vault too large to display (entry or size limit reached)",
    INVALID_REPLY: "Invalid helper response — update the plugin and retry",
    COPY_FAILED: "Could not copy to the clipboard — retry",
    ENTRY_NOT_FOUND: "This account was removed — refresh the list",
    PREFS_UNREADABLE: "Could not read preferences — check file permissions",
    PREFS_INVALID: "Invalid preferences — restore defaults or retry",
    PREFS_WRITE_FAILED: "Could not save preferences — check file permissions",
    DECRYPT_FAILED: "Could not decrypt the vault"
  }
  var hint = Object.prototype.hasOwnProperty.call(hints, code) ? hints[code] : ""
  var msg = text(error.message, 200) || code || "Error"
  return hint ? hint + " (" + code + ")" : msg
}

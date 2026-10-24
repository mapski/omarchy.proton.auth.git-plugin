import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// One shared state owner per plugin. The manifest service is used on the bar;
// a standalone panel may own a local instance (demo regression only).
Item {
  id: root
  property var shell: null
  property var manifest: null
  property var pluginRegistry: null
  property var settings: ({})
  property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, ""))
  readonly property string helperPath: pluginDir.replace(/\/$/, "") + "/bin/protonauth-list"
  readonly property bool demoMode: settings && settings.demoMode === true
  property bool ok: false
  property var entries: []
  property var error: null
  property string updatedAt: ""
  property int now: 0
  property int vaultMtime: 0
  property bool refreshing: false
  property var prefs: Model.normalizePrefs({})
  property bool prefsLoaded: false
  property string prefsError: ""
  readonly property bool privacy: !prefsLoaded || prefs.privacy !== false
  property string revealId: ""
  property var revealOwner: null
  property int prefsRevision: 0
  // Preference persistence outlives a view. Only a real/demo context switch
  // invalidates its replies; closing a panel must not hide a failed save.
  property int prefsEpoch: 0
  property var owners: []
  property int generation: 0
  property int requestSerial: 0
  property var pending: []
  property var current: null
  property string _out: ""
  property bool _timedOut: false
  property bool _oversized: false
  property bool _started: false
  signal copyFinished(string accountId, bool success, bool usedNext)

  function modeArgs(args) { return demoMode ? args.concat(["--demo"]) : args }
  function request(args, kind, input, accountId) {
    var call = { args: args, kind: kind, input: input || "", accountId: accountId || "",
                 generation: generation, serial: requestSerial, prefsRevision: prefsRevision, prefsEpoch: prefsEpoch }
    // Keep only the latest pending code view or full preferences document.
    pending = pending.filter(p => p.kind !== kind || (kind !== "codes" && kind !== "prefs-set"))
    pending = pending.concat([call])
    dispatch()
  }
  function dispatch() {
    if (current || listProcess.running || pending.length === 0) return
    var next = pending[0]
    pending = pending.slice(1)
    if (next.kind === "codes" && (next.generation !== generation || owners.length === 0)) {
      dispatch(); return
    }
    current = next
    _out = ""; _timedOut = false; _oversized = false; _started = false
    refreshing = next.kind === "codes"
    listProcess.command = [helperPath].concat(next.args)
    listProcess.stdinEnabled = next.input !== ""
    listProcess.running = true
  }
  function finish() {
    if (!current) return
    var call = current
    var raw = _out
    _out = ""
    var reply = _timedOut ? {ok:false, entries:[], error:{code:"HELPER_TIMEOUT"}}
      : _oversized ? {ok:false, entries:[], error:{code:"OUTPUT_TOO_LARGE"}}
      : !_started ? {ok:false, entries:[], error:{code:"DEPS_MISSING"}}
      : Model.parseList(raw)
    raw = ""
    current = null
    refreshing = false
    var fresh = call.generation === generation
    if (call.kind === "codes" && fresh && call.serial === requestSerial && owners.length > 0) {
      entries = reply.ok ? (privacy ? Model.hiddenEntries(reply.entries, revealId) : reply.entries) : []
      ok = reply.ok === true
      error = reply.error || null
      updatedAt = String(reply.updatedAt || "")
      now = Number(reply.now) || Math.floor(Date.now() / 1000)
      vaultMtime = Number(reply.vaultMtime) || vaultMtime
    } else if (call.kind === "check" && fresh) {
      var wasOk = ok
      var previousMtime = vaultMtime
      ok = reply.ok === true
      error = reply.error || null
      vaultMtime = Number(reply.vaultMtime) || vaultMtime
      if (!ok) {
        generation++
        requestSerial++
        revealId = ""
        entries = Model.hiddenEntries(entries, "")
      } else if (owners.length && (!wasOk || previousMtime !== vaultMtime)) refresh()
    } else if ((call.kind === "prefs-get" || call.kind === "prefs-set") && call.prefsEpoch === prefsEpoch) {
      if (reply.ok && reply.prefs && (call.kind === "prefs-get" || call.prefsRevision === prefsRevision)) {
        prefsError = ""
        prefsLoaded = true
        prefs = Model.normalizePrefs(reply.prefs)
        if (owners.length) refresh()
      } else if (!reply.ok && (call.kind === "prefs-get" || call.prefsRevision === prefsRevision)) {
        prefsError = prefsLoaded ? "Preferences not saved — click to retry" : "Preferences unavailable — click to retry"
      }
    } else if (call.kind === "copy") {
      copyFinished(call.accountId, fresh && reply.ok === true, !!(reply.copy && reply.copy.usedNext))
      if (reply.ok && fresh) editPrefs("recent", call.accountId)
    }
    Qt.callLater(dispatch)
  }
  function panelOpened(owner, panelSettings) {
    settings = panelSettings || ({})
    if (owners.indexOf(owner) < 0) owners = owners.concat([owner])
    refresh()
  }
  function panelClosed(owner) {
    owners = owners.filter(o => o !== owner)
    if (owners.length === 0) forget()
    else if (revealOwner === owner) {
      revealOwner = null
      revealId = ""
      entries = Model.hiddenEntries(entries, "")
      refresh()
    }
  }
  function forget() {
    generation++
    requestSerial++
    revealId = ""
    revealOwner = null
    entries = []
    _out = ""
    pending = pending.filter(p => p.kind !== "codes")
    if (current && current.kind === "codes") listProcess.running = false
  }
  function reveal(accountId, owner) {
    revealOwner = owner || null
    revealId = String(accountId || "")
    entries = Model.hiddenEntries(entries, revealId)
    refresh()
  }
  function refresh(allowPrompt) {
    if (!owners.length) return
    requestSerial++
    var args = privacy ? ["--private"] : []
    if (privacy && revealId) args = args.concat(["--reveal", revealId])
    if (allowPrompt === true) args.push("--allow-prompt")
    request(modeArgs(args), "codes", "", "")
  }
  function check() { request(modeArgs(["--check"]), "check", "", "") }
  function copy(accountId, clearAfter) {
    if (!accountId || !owners.length) return
    request(modeArgs(["--copy", String(accountId), "--clear-after", String(clearAfter)]), "copy", "", accountId)
  }
  function editPrefs(operation, value) {
    // Never overwrite unread preferences after a failed initial read.
    if (!prefsLoaded) return
    var next = Model.updatePrefs(prefs, operation, value)
    prefsRevision++
    if (operation === "privacy") {
      generation++
      requestSerial++
      revealId = ""
      entries = Model.hiddenEntries(entries, "")
      _out = ""
    }
    prefs = next
    if (!demoMode) request(["--prefs-set"], "prefs-set", JSON.stringify(next), "")
    if (owners.length) refresh()
  }
  function retryPrefs() {
    if (demoMode) return
    if (prefsLoaded) request(["--prefs-set"], "prefs-set", JSON.stringify(prefs), "")
    else request(["--prefs-get"], "prefs-get", "", "")
  }
  onDemoModeChanged: {
    prefsEpoch++
    forget()
    pending = []
    prefsError = ""
    prefs = Model.normalizePrefs({})
    prefsLoaded = demoMode
    if (!demoMode) request(["--prefs-get"], "prefs-get", "", "")
    if (owners.length) refresh()
  }
  Component.onCompleted: Qt.callLater(function() {
    if (demoMode) { prefsLoaded = true; check() }
    else { request(["--prefs-get"], "prefs-get", "", ""); check() }
  })
  Process {
    id: listProcess
    running: false
    command: []
    stdinEnabled: false
    onStarted: {
      root._started = true
      if (root.current && root.current.input) write(root.current.input)
      if (root.current) root.current.input = ""
      stdinEnabled = false
    }
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(data) {
        if (!root.current) return
        if (root.current.kind === "codes" && root.current.generation !== root.generation) return
        if (root._out.length + data.length > 1024 * 1024) {
          root._out = ""; root._oversized = true; listProcess.running = false
        } else root._out += data
      }
    }
    onExited: root.finish()
    onRunningChanged: if (!running && root.current) Qt.callLater(function() {
      if (!listProcess.running && root.current) root.finish()
    })
  }
  Timer {
    interval: root.current && root.current.args.indexOf("--allow-prompt") >= 0 ? 125000 : 30000
    running: listProcess.running
    onTriggered: { root._timedOut = true; root._out = ""; listProcess.running = false }
  }
  Timer {
    interval: root.owners.length ? 5000 : 60000
    running: true
    repeat: true
    onTriggered: if (!root.current && !root.pending.length) {
      if (!root.prefsLoaded) root.retryPrefs()
      root.check()
    }
  }
}

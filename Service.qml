import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})
  property string pluginDir: ""

  property bool ok: false
  property var entries: []
  property var error: null
  property string updatedAt: ""
  property bool refreshing: false

  readonly property int refreshIntervalSec: {
    var n = parseInt(String((settings && settings.refreshIntervalSec) || 1), 10)
    if (!isFinite(n) || n < 1) n = 1
    if (n > 30) n = 30
    return n
  }

  readonly property string helperPath: {
    var base = String(pluginDir || "")
    if (base.indexOf("file://") === 0) base = base.substring(7)
    if (base.length && base.charAt(base.length - 1) !== "/") base += "/"
    return base + "bin/protonauth-list"
  }

  // Fake accounts from the helper (screenshots / trying the UI). Never reads the vault.
  readonly property bool demoMode: {
    var v = settings ? settings.demoMode : false
    return v === true || v === "true"
  }

  property string _out: ""
  property string _err: ""

  function _run(args) {
    if (listProcess.running) return
    // Only ever run the helper bundled with this plugin (fixed argv, no shell).
    if (!helperPath || !helperPath.endsWith("/bin/protonauth-list")) return
    refreshing = true
    _out = ""
    _err = ""
    listProcess.command = [helperPath].concat(args)
    listProcess.running = true
  }

  // Codes for the open panel.
  function refresh() { _run(demoMode ? ["--demo"] : []) }

  // Health only (deps, vault, keyring) for the bar icon; returns no codes.
  function check() { _run(demoMode ? ["--demo"] : ["--check"]) }

  // Forget codes (called when the panel closes); keeps the health state.
  function forget() { entries = [] }

  function apply() {
    var raw = _out && _out.length ? _out : _err
    var parsed = Model.parseList(raw)
    ok = parsed.ok === true
    entries = parsed.entries || []
    error = parsed.error || null
    updatedAt = String(parsed.updatedAt || "")
    refreshing = false
  }

  Process {
    id: listProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: listStdout
      waitForEnd: true
      onStreamFinished: root._out = text
    }
    stderr: StdioCollector {
      id: listStderr
      waitForEnd: true
      onStreamFinished: root._err = text
    }
    onExited: root.apply()
  }
}

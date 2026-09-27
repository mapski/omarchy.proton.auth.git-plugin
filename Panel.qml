import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  // Must match manifest.json "id".
  readonly property string pluginId: "io.github.mapski.proton-authenticator"
  moduleName: pluginId
  ipcTarget: pluginId
  manageIpc: false

  // Injected by BarWidget.qml (see Omarchy's develop guide).
  property var anchorItem: null
  property var hostWidget: null

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  property string searchText: ""
  property int selectedIndex: 0
  property bool cursorActive: false
  property string copiedKey: ""
  // Code currently on the clipboard (kept only until the auto-clear fires).
  property string _clipboardCode: ""
  property string pluginDir: {
    var u = Qt.resolvedUrl(".")
    var s = String(u)
    if (s.indexOf("file://") === 0) s = s.substring(7)
    return s
  }

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color accent: "#6d4aff"
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color urgent: Color.urgent
  readonly property bool healthy: svc.ok
  Component.onCompleted: svc.check()
  readonly property int clipboardClearSec: {
    var raw = settings ? settings.clipboardClearSec : undefined
    var n = parseInt(String(raw === undefined || raw === null ? 20 : raw), 10)
    if (!isFinite(n) || n < 0) n = 20
    return Math.min(n, 600)
  }
  readonly property var visibleEntries: Model.filterEntries(svc.entries, searchText)
  readonly property string statusLine: {
    if (svc.refreshing && svc.entries.length === 0) return "Loading…"
    if (!svc.ok && svc.error) return Model.errorHint(svc.error)
    if (visibleEntries.length === 0) return searchText.length ? "No matches" : "No accounts"
    return visibleEntries.length + " account" + (visibleEntries.length === 1 ? "" : "s")
  }

  function refresh() { svc.refresh() }

  function ensureCursor() {
    if (visibleEntries.length === 0) { selectedIndex = 0; return }
    if (selectedIndex >= visibleEntries.length) selectedIndex = visibleEntries.length - 1
    if (selectedIndex < 0) selectedIndex = 0
  }

  function moveCursor(dy) {
    cursorActive = true
    ensureCursor()
    if (visibleEntries.length === 0) return
    selectedIndex = Math.max(0, Math.min(visibleEntries.length - 1, selectedIndex + dy))
  }

  function copyEntry(entry) {
    if (!entry || !entry.code) return
    var code = String(entry.code).replace(/\s+/g, "")
    copiedKey = Model.entryKey(entry)
    // --sensitive asks clipboard managers that honour the hint not to keep it.
    copyProcess.command = ["wl-copy", "--sensitive", "--", code]
    copyProcess.running = true
    copiedTimer.restart()
    _clipboardCode = code
    if (clipboardClearSec > 0) clipboardClearTimer.restart()
  }

  // Clear the clipboard after clipboardClearSec, but only if it still holds the
  // code we put there (never wipe something the user copied afterwards).
  function clearClipboardIfOurs() {
    if (!_clipboardCode || clipboardCheck.running) return
    clipboardCheck.command = ["wl-paste", "--no-newline"]
    clipboardCheck.running = true
  }

  function activateCursor() {
    ensureCursor()
    if (visibleEntries.length === 0) return
    copyEntry(visibleEntries[selectedIndex])
  }

  function openApp() {
    // Detach via uwsm-app (same pattern as Proton Drive's openMountFolder).
    // Process{} would keep the app as a child of the shell and tear it down;
    // the .desktop also needs WEBKIT_DISABLE_DMABUF_RENDERER or WebKit dies on open.
    Quickshell.execDetached({
      command: [
        "/usr/bin/uwsm-app", "--",
        "env", "WEBKIT_DISABLE_DMABUF_RENDERER=1",
        "/usr/bin/proton-authenticator"
      ]
    })
    root.close()
  }

  onOpenedChanged: {
    if (opened) {
      cursorActive = false
      searchText = ""
      refresh()
      poll.running = true
      Qt.callLater(function() { if (keyCatcher) keyCatcher.forceActiveFocus() })
    } else {
      poll.running = false
      svc.forget()
    }
  }

  Service {
    id: svc
    settings: root.settings
    pluginDir: root.pluginDir
  }

  Timer {
    id: poll
    interval: Math.max(1, svc.refreshIntervalSec) * 1000
    repeat: true
    running: false
    onTriggered: if (root.opened) root.refresh()
  }

  Timer {
    id: copiedTimer
    interval: 1200
    onTriggered: root.copiedKey = ""
  }

  Process { id: copyProcess; running: false; command: [] }

  Timer {
    id: clipboardClearTimer
    interval: Math.max(1, root.clipboardClearSec) * 1000
    onTriggered: root.clearClipboardIfOurs()
  }

  Process {
    id: clipboardCheck
    running: false
    command: []
    stdout: StdioCollector {
      id: clipboardCheckOut
      waitForEnd: true
    }
    onExited: {
      var current = String(clipboardCheckOut.text || "").trim()
      if (current.length && current === root._clipboardCode) {
        clearProcess.command = ["wl-copy", "--clear"]
        clearProcess.running = true
      }
      root._clipboardCode = ""
    }
  }

  Process { id: clearProcess; running: false; command: [] }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function clearClipboard(): string { root.clearClipboardIfOurs(); return "ok" }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(8)

        RowLayout {
          width: parent.width
          spacing: Style.space(6)

          Rectangle {
            Layout.preferredHeight: Style.space(22)
            Layout.preferredWidth: addLabel.implicitWidth + Style.space(12)
            radius: Style.space(6)
            color: root.accent
            Text {
              id: addLabel
              anchors.centerIn: parent
              text: "+ Add"
              color: "white"
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openApp()
            }
          }

          Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Style.space(22)
            radius: Style.space(6)
            color: Qt.rgba(1, 1, 1, 0.06)
            border.width: 1
            border.color: root.accent
            RowLayout {
              anchors.fill: parent
              anchors.leftMargin: Style.space(8)
              anchors.rightMargin: Style.space(8)
              Text {
                text: "⌕"
                color: root.dim
                font.pixelSize: Style.font.caption
              }
              TextInput {
                id: searchInput
                Layout.fillWidth: true
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                clip: true
                text: root.searchText
                onTextChanged: root.searchText = text
                Keys.onPressed: function(event) {
                  if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true }
                  else if (event.key === Qt.Key_Down) { root.moveCursor(1); event.accepted = true }
                  else if (event.key === Qt.Key_Up) { root.moveCursor(-1); event.accepted = true }
                  else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    root.activateCursor(); event.accepted = true
                  }
                }
              }
            }
          }

          Text {
            text: "⚙"
            color: root.dim
            font.pixelSize: Style.font.body
            MouseArea {
              anchors.fill: parent
              anchors.margins: -6
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openApp()
            }
          }
        }

        Text {
          width: parent.width
          text: root.statusLine
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Repeater {
          model: root.visibleEntries
          delegate: Rectangle {
            required property var modelData
            required property int index
            width: column.width
            height: Style.space(64)
            radius: Style.space(8)
            color: {
              var key = Model.entryKey(modelData)
              if (root.copiedKey === key) return Qt.rgba(0.42, 0.29, 1.0, 0.22)
              if (root.cursorActive && index === root.selectedIndex) return Qt.rgba(1, 1, 1, 0.10)
              return Qt.rgba(1, 1, 1, 0.05)
            }
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.08)

            RowLayout {
              anchors.fill: parent
              anchors.margins: Style.space(8)
              spacing: Style.space(8)

              Column {
                Layout.fillWidth: true
                spacing: 2
                Text {
                  width: parent.width
                  text: String(modelData.issuer || "Account")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }
                Text {
                  width: parent.width
                  text: String(modelData.account || "")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                  visible: text.length > 0
                }
                Text {
                  text: Model.formatCode(modelData.code)
                  // Last 5 seconds of the period: warn with the theme's urgent colour.
                  color: Number(modelData.remaining) <= 5 ? root.urgent : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.subtitle
                  font.bold: true
                }
              }

              Column {
                spacing: 2
                Layout.alignment: Qt.AlignVCenter
                Item {
                  width: Style.space(18)
                  height: Style.space(18)
                  anchors.horizontalCenter: parent.horizontalCenter
                  Canvas {
                    id: ring
                    anchors.fill: parent
                    property real ratio: Model.remainingFraction(modelData.remaining, modelData.period)
                    onRatioChanged: requestPaint()
                    onPaint: {
                      var ctx = getContext("2d")
                      ctx.reset()
                      var cx = width / 2, cy = height / 2, r = Math.min(width, height) / 2 - 1
                      ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.15)
                      ctx.lineWidth = 2
                      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke()
                      ctx.strokeStyle = root.accent
                      ctx.beginPath()
                      ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * ratio)
                      ctx.stroke()
                    }
                    Text {
                      anchors.centerIn: parent
                      text: String(modelData.remaining)
                      color: root.foreground
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }
                }
                Text {
                  anchors.horizontalCenter: parent.horizontalCenter
                  text: "Next"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Text {
                  anchors.horizontalCenter: parent.horizontalCenter
                  text: Model.formatCode(modelData.nextCode)
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }
            }

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              hoverEnabled: true
              onEntered: { root.cursorActive = true; root.selectedIndex = index }
              onClicked: root.copyEntry(modelData)
            }
          }
        }
      }
    }
  }
}

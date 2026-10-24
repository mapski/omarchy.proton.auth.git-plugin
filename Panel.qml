import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  readonly property string pluginId: "io.github.mapski.proton-authenticator"
  moduleName: pluginId
  ipcTarget: pluginId
  manageIpc: false
  property var anchorItem: null
  property var hostWidget: null
  readonly property var sharedService: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor(pluginId) : null
  readonly property var svc: sharedService || localService.item
  readonly property bool healthy: svc ? svc.ok : false
  readonly property bool privacy: svc ? svc.privacy : true
  readonly property color foreground: bar && bar.foreground !== undefined ? bar.foreground : Commons.Color.foreground
  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.65)
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property string fontFamily: bar && bar.fontFamily ? bar.fontFamily : Style.font.family
  readonly property int clipboardClearSec: Math.max(5, Math.min(120, Number(setting("clipboardClearSec", 20)) || 20))
  readonly property bool closeOnCopy: setting("closeOnCopy", true) !== false
  readonly property int staleHours: Math.max(1, Math.min(168, Number(setting("staleHours", 24)) || 24))
  property string searchText: ""
  property string selectedId: ""
  property string localRevealId: ""
  readonly property int selectedIndex: Model.findIndexById(visibleEntries, selectedId)
  property bool cursorActive: false
  property var recentSnapshot: null
  property string copiedKey: ""
  property string feedback: ""
  property string copyingId: ""
  property int viewGeneration: 0
  property int copyGeneration: 0
  property int syncGeneration: 0
  property bool syncing: false
  property real clockMs: Date.now()
  readonly property var visibleEntries: Model.filterAndOrder(svc ? svc.entries : [], searchText,
    svc ? svc.prefs : {}, recentSnapshot)
  readonly property string statusLine: {
    if (!svc || (svc.refreshing && svc.entries.length === 0)) return "Loading…"
    if (!svc.ok && svc.error) return Model.errorHint(svc.error)
    if (visibleEntries.length === 0) return searchText.length ? "No matches" : "No accounts"
    return visibleEntries.length + (searchText ? " matches" : " accounts")
      + (setting("demoMode", false) === true ? " · DEMO" : privacy ? " · codes hidden until selected" : "")
  }
  readonly property string freshness: {
    if (!svc || !svc.vaultMtime || setting("demoMode", false) === true) return ""
    var age = Math.max(0, clockMs / 1000 - svc.vaultMtime)
    return age >= staleHours * 3600 ? "Vault unchanged for " + Math.floor(age / 3600) + "h" : ""
  }
  Loader {
    id: localService
    active: !root.sharedService
    sourceComponent: Component { Service { settings: root.settings } }
    onLoaded: if (root.opened) item.panelOpened(root, root.settings)
  }
  onSettingsChanged: if (svc) svc.settings = settings
  onSharedServiceChanged: if (opened && sharedService) sharedService.panelOpened(root, settings)
  Component.onDestruction: if (svc) svc.panelClosed(root)
  onOpenedChanged: {
    viewGeneration++
    copyingId = ""; feedback = ""; copiedKey = ""; localRevealId = ""
    if (opened) {
      searchText = ""; cursorActive = false; selectedId = ""
      recentSnapshot = svc && svc.prefsLoaded ? svc.prefs.recent.slice() : null
      viewport.contentY = 0
      if (svc) svc.panelOpened(root, settings)
      Qt.callLater(function() { if (root.opened) searchInput.forceActiveFocus() })
    } else {
      if (svc) svc.panelClosed(root)
      displayModel.clear()
      refreshTimer.stop()
    }
  }
  Connections {
    target: root.svc
    function onPrefsChanged() {
      if (root.opened && root.recentSnapshot === null && root.svc.prefsLoaded)
        root.recentSnapshot = root.svc.prefs.recent.slice()
    }
    function onCopyFinished(accountId, success, usedNext) {
      if (!root.opened || root.copyGeneration !== root.viewGeneration || root.copyingId !== accountId) return
      root.copyingId = ""
      root.copiedKey = success ? accountId : ""
      root.feedback = success ? (usedNext ? "Next code copied" : "Code copied") : "Copy failed — retry"
      feedbackTimer.restart()
    }
  }
  function switchPanel(direction) {
    return bar && typeof bar.switchPanelFrom === "function" ? bar.switchPanelFrom(hostWidget || root, direction) : false
  }
  function refresh() { if (svc) svc.refresh() }
  function syncRows() {
    if (!opened) return
    // Update existing identities in place. Replacing the Repeater's array can
    // transiently shrink its height and clamp contentY during an OTP refresh.
    var y = viewport.contentY
    syncing = true
    for (var i = 0; i < visibleEntries.length; i++) {
      var e = visibleEntries[i]
      var found = -1
      for (var j = i; j < displayModel.count; j++) if (displayModel.get(j).rowData.id === e.id) { found = j; break }
      if (found < 0) displayModel.insert(i, {rowData:e})
      else {
        if (found !== i) displayModel.move(found, i, 1)
        displayModel.setProperty(i, "rowData", e)
      }
    }
    if (displayModel.count > visibleEntries.length) displayModel.remove(visibleEntries.length, displayModel.count - visibleEntries.length)
    selectedId = Model.selectionAfterRefresh(visibleEntries, selectedId)
    syncing = false
    var token = ++syncGeneration
    Qt.callLater(function() {
      if (token === root.syncGeneration) viewport.contentY = Math.max(0, Math.min(y, viewport.contentHeight - viewport.height))
    })
    scheduleRefresh()
  }
  onVisibleEntriesChanged: syncRows()
  onSearchTextChanged: {
    selectedId = ""
    viewport.contentY = 0
    syncRows()
  }
  function scheduleRefresh() {
    if (!svc || !opened) return
    var delay = Model.refreshDelayMs(svc.entries, svc.now, Date.now())
    refreshTimer.stop()
    if (delay >= 0) { refreshTimer.interval = Math.max(100, delay); refreshTimer.start() }
  }
  function revealSelection() {
    var card = cards.itemAt(selectedIndex)
    if (!card) return
    var y = card.y
    if (y < viewport.contentY) viewport.contentY = y
    else if (y + card.height > viewport.contentY + viewport.height)
      viewport.contentY = y + card.height - viewport.height
  }
  function choose(index) {
    if (!visibleEntries.length) return
    index = Math.max(0, Math.min(visibleEntries.length - 1, index))
    cursorActive = true
    selectedId = visibleEntries[index].id
    if (privacy && svc) { localRevealId = selectedId; svc.reveal(selectedId, root) }
    Qt.callLater(revealSelection)
  }
  function moveCursor(delta) { choose(Math.max(0, selectedIndex) + delta) }
  function copyEntry(entry) {
    if (!entry || entry.kind === "unsupported" || !svc || copyingId) return
    if (privacy && (!entry.code || localRevealId !== entry.id || svc.revealOwner !== root || svc.revealId !== entry.id)) {
      choose(Model.findIndexById(visibleEntries, entry.id)); return
    }
    selectedId = entry.id
    copyingId = entry.id
    copyGeneration = viewGeneration
    svc.copy(entry.id, clipboardClearSec)
  }
  function activateCursor() { if (selectedIndex >= 0) copyEntry(visibleEntries[selectedIndex]) }
  function pinSelected() { if (svc && selectedId) svc.editPrefs("pin", selectedId) }
  function togglePrivacy() { localRevealId = ""; if (svc) svc.editPrefs("privacy", !privacy) }
  function handleKey(event) {
    var ctrl = event.modifiers & Qt.ControlModifier
    if (ctrl && event.key === Qt.Key_P) { pinSelected(); event.accepted = true }
    else if (ctrl && event.key === Qt.Key_H) { togglePrivacy(); event.accepted = true }
    else if (event.key === Qt.Key_Down || (ctrl && event.key === Qt.Key_J)) { moveCursor(1); event.accepted = true }
    else if (event.key === Qt.Key_Up || (ctrl && event.key === Qt.Key_K)) { moveCursor(-1); event.accepted = true }
    else if (event.key === Qt.Key_PageDown) { moveCursor(5); event.accepted = true }
    else if (event.key === Qt.Key_PageUp) { moveCursor(-5); event.accepted = true }
    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      if (selectedIndex >= 0) activateCursor()
      else if (svc) svc.refresh(true)
      event.accepted = true
    } else if (event.key === Qt.Key_Escape) {
      if (searchText) searchText = ""; else close()
      event.accepted = true
    } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
      switchPanel(event.modifiers & Qt.ShiftModifier ? -1 : 1); event.accepted = true
    }
  }
  function openApp() {
    Quickshell.execDetached({command:["/usr/bin/uwsm-app", "--", "/usr/bin/env", "WEBKIT_DISABLE_DMABUF_RENDERER=1", "/usr/bin/proton-authenticator"]})
    close()
  }
  Timer { id: refreshTimer; repeat: false; onTriggered: root.refresh() }
  Timer { interval: 1000; repeat: true; running: root.opened; onTriggered: root.clockMs = Date.now() }
  Timer {
    id: feedbackTimer
    interval: 1200
    onTriggered: { if (root.copiedKey && root.closeOnCopy) root.close(); root.feedback = ""; root.copiedKey = "" }
  }
  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    // Safe runtime provenance only: never labels, IDs, codes or helper output.
    function status(): string { return JSON.stringify({version:"1.1.0", opened:root.opened, privacy:root.privacy, demo:root.setting("demoMode", false) === true}) }
  }
  TextMetrics { id: nextCodeMetrics; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; text: "8888 8888" }
  ListModel { id: displayModel; dynamicRoles: true }
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: searchInput
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(header.implicitHeight + status.implicitHeight
      + Math.min(column.implicitHeight, Style.space(340)) + footer.implicitHeight + Style.space(24), Style.space(520))
    Item {
      id: keyCatcher
      anchors.fill: parent
      Keys.onPressed: event => root.handleKey(event)
      Column {
        id: header
        width: parent.width
        spacing: Style.space(6)
        RowLayout {
          width: parent.width
          Text { text:"Proton Authenticator"; color:root.foreground; font.family:root.fontFamily; font.pixelSize:Style.font.body; font.bold:true; Layout.fillWidth:true }
          Text { text:root.privacy ? "Hidden" : "Visible"; color:root.accent; font.family:root.fontFamily; font.pixelSize:Style.font.caption
            MouseArea { anchors.fill:parent; anchors.margins:-4; cursorShape:Qt.PointingHandCursor; onClicked:root.togglePrivacy() } }
          Text { text:"+ Add"; color:root.accent; font.family:root.fontFamily; font.pixelSize:Style.font.caption
            MouseArea { anchors.fill:parent; anchors.margins:-4; cursorShape:Qt.PointingHandCursor; onClicked:root.openApp() } }
        }
        Rectangle {
          width:parent.width; height:Style.space(28); radius:Style.space(6)
          color:Qt.rgba(root.foreground.r,root.foreground.g,root.foreground.b,0.05)
          border.width:1; border.color:root.accent
          TextInput {
            id: searchInput
            anchors.fill:parent; anchors.margins:Style.space(5)
            color:root.foreground; font.family:root.fontFamily; font.pixelSize:Style.font.bodySmall
            clip:true; text:root.searchText; onTextChanged:root.searchText = text
            Keys.onPressed: event => root.handleKey(event)
            Text { text:"Search accounts…"; visible:!searchInput.text && !searchInput.preeditText; color:root.dim; font:searchInput.font }
          }
        }
      }
      Text {
        id: status
        anchors.top:header.bottom; anchors.topMargin:Style.space(6)
        width:parent.width; text:root.statusLine; textFormat:Text.PlainText
        color:root.dim; font.family:root.fontFamily; font.pixelSize:Style.font.caption; wrapMode:Text.WordWrap
        MouseArea { anchors.fill:parent; enabled:root.svc && !root.svc.ok; cursorShape:Qt.PointingHandCursor; onClicked:root.svc.refresh(true) }
      }
      Flickable {
        id: viewport
        anchors.top:status.bottom; anchors.topMargin:Style.space(6)
        anchors.bottom:footer.top; anchors.bottomMargin:Style.space(6)
        width:parent.width
        contentWidth:width; contentHeight:column.implicitHeight
        clip:true; flickableDirection:Flickable.VerticalFlick; boundsBehavior:Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {
          width:Style.space(4)
          policy:ScrollBar.AsNeeded
          active:viewport.contentHeight > viewport.height
        }
        Column {
          id: column
          width:viewport.width; spacing:Style.space(8)
          Repeater {
            id: cards
            model:displayModel
            delegate: Rectangle {
              required property var rowData
              required property int index
              readonly property var modelData: rowData
              readonly property bool hidden: root.privacy && (!root.svc || root.svc.revealOwner !== root || root.localRevealId !== modelData.id || root.svc.revealId !== modelData.id)
              readonly property var shown: hidden ? {code:null, nextCode:null, period:null, remaining:null} : modelData
              readonly property var remaining: shown.code ? Model.liveRemaining(shown, root.svc ? root.svc.now : 0, Math.floor(root.clockMs / 1000)) : null
              width:column.width
              height:Math.max(Style.space(64), cardContent.implicitHeight + Style.space(8) * 2)
              radius:Style.space(8)
              color: root.copiedKey === modelData.id ? Qt.rgba(root.accent.r,root.accent.g,root.accent.b,0.18)
                : root.cursorActive && root.selectedId === modelData.id ? Qt.rgba(root.foreground.r,root.foreground.g,root.foreground.b,0.10)
                : Qt.rgba(root.foreground.r,root.foreground.g,root.foreground.b,0.04)
              border.width:1; border.color:Qt.rgba(root.foreground.r,root.foreground.g,root.foreground.b,0.1)
              RowLayout {
                id: cardContent
                anchors.fill:parent; anchors.margins:Style.space(8); spacing:Style.space(8)
                Column {
                  Layout.fillWidth:true; spacing:2
                  Text { width:parent.width; text:Model.issuerGlyph(modelData.issuer) + "  " + String(modelData.issuer || "Account")
                      + (root.svc && root.svc.prefs.pinned.indexOf(modelData.id) >= 0 ? " ★" : "")
                    textFormat:Text.PlainText; color:root.foreground; font.family:root.fontFamily; font.pixelSize:Style.font.body; font.bold:true; elide:Text.ElideRight }
                  Text { width:parent.width; text:String(modelData.account || ""); textFormat:Text.PlainText
                    color:root.dim; font.family:root.fontFamily; font.pixelSize:Style.font.caption; elide:Text.ElideRight; visible:text.length > 0 }
                  Text { text:modelData.kind === "unsupported" ? "Unsupported account" : !shown.code ? "••• •••" : Model.formatCode(shown.code)
                    textFormat:Text.PlainText; color:remaining !== null && remaining <= 5 ? root.urgent : root.foreground
                    font.family:root.fontFamily; font.pixelSize:Style.font.subtitle; font.bold:true }
                }
                Column {
                  Layout.preferredWidth:Math.ceil(nextCodeMetrics.width); Layout.minimumWidth:Layout.preferredWidth
                  Layout.alignment:Qt.AlignVCenter; spacing:2
                  Item {
                    width:Style.space(20); height:Style.space(20); anchors.horizontalCenter:parent.horizontalCenter
                    Canvas {
                      id:ring
                      anchors.fill:parent
                      property real ratio: Model.remainingFraction(remaining, shown.period)
                      onRatioChanged:requestPaint()
                      onPaint: {
                        var ctx = getContext("2d"); ctx.reset()
                        var r = Math.min(width,height) / 2 - 1
                        ctx.lineWidth = 2; ctx.strokeStyle = root.dim
                        ctx.beginPath(); ctx.arc(width/2,height/2,r,0,Math.PI*2); ctx.stroke()
                        ctx.strokeStyle = root.accent
                        ctx.beginPath(); ctx.arc(width/2,height/2,r,-Math.PI/2,-Math.PI/2+Math.PI*2*ratio); ctx.stroke()
                      }
                      Text { anchors.centerIn:parent; text:remaining === null ? "–" : String(remaining); textFormat:Text.PlainText
                        color:root.foreground; font.family:root.fontFamily; font.pixelSize:Style.font.caption }
                    }
                  }
                  Text { anchors.horizontalCenter:parent.horizontalCenter; text:"Next"; color:root.dim; font.family:root.fontFamily; font.pixelSize:Style.font.caption }
                  Text { anchors.horizontalCenter:parent.horizontalCenter; text:shown.nextCode ? Model.formatCode(shown.nextCode) : "••• •••"
                    textFormat:Text.PlainText; color:root.dim; font.family:root.fontFamily; font.pixelSize:Style.font.bodySmall }
                }
              }
              MouseArea {
                anchors.fill:parent; cursorShape:Qt.PointingHandCursor; acceptedButtons:Qt.LeftButton | Qt.RightButton
                onClicked: function(mouse) {
                  root.cursorActive = true; root.selectedId = modelData.id
                  if (mouse.button === Qt.RightButton) root.pinSelected()
                  else root.copyEntry(modelData)
                }
              }
            }
          }
        }
      }
      Text {
        id: footer
        anchors.bottom:parent.bottom; width:parent.width
        text:root.feedback || (root.svc ? root.svc.prefsError : "") || root.freshness || "Enter copy/reveal · Ctrl+P pin · Ctrl+H hide · Add opens Proton"
        textFormat:Text.PlainText; color:root.dim; font.family:root.fontFamily; font.pixelSize:Style.font.caption; wrapMode:Text.WordWrap
        MouseArea { anchors.fill:parent; enabled:root.svc && !!root.svc.prefsError; cursorShape:Qt.PointingHandCursor; onClicked:root.svc.retryPrefs() }
      }
    }
  }
}

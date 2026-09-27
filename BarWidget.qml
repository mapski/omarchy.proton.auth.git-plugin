import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Manifest entry point (kind "bar-widget"). Draws the bar icon, loads
// Panel.qml and forwards the panel lifecycle Quattro uses for clicks and
// `omarchy-shell shell summon|hide`, as in Omarchy's plugin develop guide.
BarWidget {
  id: root
  moduleName: "io.github.mapski.proton-authenticator"

  readonly property bool opened: panelLoader.item
    ? panelLoader.item.opened === true
    : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property bool healthy: panelLoader.item ? panelLoader.item.healthy === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function refresh() {
    if (panelLoader.item) panelLoader.item.refresh()
  }

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.bar = root.bar
    panelLoader.item.settings = root.settings
    panelLoader.item.anchorItem = button
    panelLoader.item.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: if (panelLoader.item) panelLoader.item.settings = root.settings

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: "Proton Authenticator"
    iconComponent: Component {
      Item {
        ProtonAuthIcon {
          anchors.centerIn: parent
          iconSize: Style.space(14)
          // Same convention as Omarchy's Dropbox widget: bar foreground when
          // working, dimmed foreground when it is not.
          color: root.healthy ? root.foreground : root.dim
          iconOpacity: root.healthy ? 1.0 : 0.6
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.refresh()
      else root.toggle()
    }
  }
}

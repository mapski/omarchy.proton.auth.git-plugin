import QtQuick
import Quickshell
import qs.Commons
import "../../" as Plugin

// Run by layout.test.js against the installed Omarchy components, using only
// demo data. No vault reads, clipboard writes or desktop settings changes.
ShellRoot {
  Plugin.Panel {
    id: plugin
    settings: ({demoMode: true})
    Component.onCompleted: open()
  }

  property int phase: 0
  property var failures: []
  property var demo: []

  function descendants(object) {
    var result = [], seen = []
    function visit(o) {
      if (!o || seen.indexOf(o) >= 0) return
      seen.push(o)
      result.push(o)
      var children = o.data || o.children || []
      for (var i = 0; i < children.length; i++) visit(children[i])
    }
    visit(object)
    return result
  }

  function check(condition, message) {
    if (!condition) failures.push(message)
  }

  Timer {
    interval: 700
    running: true
    repeat: true
    onTriggered: {
      var objects = descendants(plugin)
      var service = objects.find(o => o.helperPath !== undefined)
      var popup = objects.find(o => o.fittedContentHeight !== undefined)
      var cards = objects.filter(o => o.modelData !== undefined && o.index !== undefined)
      var viewport = objects.find(o => o.contentY !== undefined && o.flickableDirection !== undefined)
      console.log("LAYOUT_PHASE", phase, "opened", plugin.opened, "cards", cards.length)
      if (phase === 0) {
        if (service && service.refreshing) return
        check(service && service.demoMode && cards.length > 0, "demo panel loaded")
        if (!cards.length) { console.log("LAYOUT_FAIL " + JSON.stringify(failures)); Qt.quit(); return }
        demo = service.entries.slice()
        // More accounts than fit in the popup, all copied from public demo data.
        var many = []
        for (var i = 0; i < 12; i++) many.push(Object.assign({}, demo[i % demo.length], {id: "demo-" + i}))
        service.entries = many
        // Stop periodic refresh without changing production code.
        objects.filter(o => o.repeat === true && o.interval === 1000).forEach(o => o.stop())
      } else if (phase === 1) {
        check(cards.length === 12, "all twelve demo cards rendered")
        check(viewport && viewport.clip && viewport.contentHeight > viewport.height,
              "long account list must scroll inside a clipped viewport")
        for (var card of cards) {
          var row = card.children[0]
          check(row.height >= row.implicitHeight, "card border must enclose its text and countdown")
        }
        // Mixed six/eight-digit demo codes must share one countdown/Next column.
        var centers = cards.map(card => {
          var row = card.children[0]
          var next = row.children[1]
          return next.mapToItem(card, next.width / 2, 0).x
        })
        check(Math.max.apply(null, centers) - Math.min.apply(null, centers) <= 1,
              "Next codes and countdowns must align across mixed code lengths")
        check(popup.contentHeight <= Style.space(520), "popup respects screen/height cap")
        var preview = Quickshell.env("PROTONAUTH_LAYOUT_PREVIEW")
        if (preview) {
          var surface = objects.find(o => o.contentTopInset !== undefined && o.width === popup.contentWidth)
          check(!!surface, "popup border surface exists")
          if (surface) surface.grabToImage(result => result.saveToFile(preview))
        }
        plugin.moveCursor(11)
      } else if (phase === 2) {
        var selected = cards.find(o => o.index === plugin.selectedIndex)
        check(plugin.selectedIndex === 11, "keyboard selected the last card")
        check(!!selected, "selected delegate exists")
        if (viewport && selected) {
          var point = selected.mapToItem(viewport, 0, 0)
          check(point.y >= -1 && point.y + selected.height <= viewport.height + 1,
                "keyboard selection must scroll the last card into view")
        }
        plugin.searchText = "no-demo-account-matches-this"
      } else if (phase === 3) {
        check(cards.length === 0, "search filters the cards")
        check(popup.contentHeight < Style.space(200), "border shrinks for no matches")
        if (viewport) check(viewport.contentY === 0, "search resets scroll position")
        console.log((failures.length ? "LAYOUT_FAIL " : "LAYOUT_PASS ") + JSON.stringify(failures))
        plugin.close()
        Qt.quit()
      }
      phase++
    }
  }
}

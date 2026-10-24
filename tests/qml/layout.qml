import QtQuick
import Quickshell
import qs.Commons
import QtTest
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
  property real savedScroll: 0
  property int listCount: Math.max(6, Math.min(64, parseInt(Quickshell.env("PROTONAUTH_LAYOUT_COUNT") || "16", 10)))
  property int rollovers: 0
  property int inputPhase: 0
  property int startupChecks: 0
  TestCase { id: inputProbe; when: false; name: "PluginInput" }

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
        startupChecks++
        if (service && (service.refreshing || service.current || service.pending && service.pending.length)) return
        // A helper reply can settle before Loader/Repeater delegates have
        // appeared. Wait for observable demo readiness, not one timer tick.
        if (!cards.length && startupChecks < 8 && (!service || !service.error)) return
        check(service && service.demoMode && cards.length > 0, "demo panel loaded")
        if (!cards.length) { console.log("LAYOUT_FAIL " + JSON.stringify(failures)); Qt.quit(); return }
        if (inputPhase === 0) {
          var editor = objects.find(o => o.preeditText !== undefined && o.cursorPosition !== undefined)
          check(editor && editor.activeFocus, "search receives focus on open")
          inputProbe.parent = editor
          inputProbe.keyClick(Qt.Key_G)
          inputProbe.keyClick(Qt.Key_H)
          inputPhase++
          return
        } else if (inputPhase === 1) {
          check(plugin.searchText === "gh" && plugin.visibleEntries.length === 1,
                "real keyboard input uses fuzzy search")
          inputProbe.keyClick(Qt.Key_Escape)
          check(plugin.opened && plugin.searchText === "", "Escape first clears search")
          inputProbe.keyClick(Qt.Key_H, Qt.ControlModifier)
          check(!service.privacy, "Ctrl+H toggles privacy")
          inputProbe.keyClick(Qt.Key_P, Qt.ControlModifier)
          check(service.prefs.pinned.indexOf(plugin.selectedId) >= 0, "Ctrl+P pins selected account")
          inputProbe.keyClick(Qt.Key_H, Qt.ControlModifier)
          inputPhase++
          return
        }
        demo = service.entries.slice()
        // Fixture rows below are test-owned. Keep navigation from starting a
        // real demo reload that would replace sixteen fixtures with six rows.
        if (service.owners !== undefined) service.owners = []
        // More accounts than fit in the popup, all copied from public demo data.
        var many = []
        for (var i = 0; i < listCount; i++) many.push(Object.assign({}, demo[i % demo.length], {
          id: "demo-" + i, kind: "totp", code: i % 2 ? "12345678" : "123456",
          nextCode: i % 2 ? "87654321" : "654321", period: 30, remaining: 24, digits: i % 2 ? 8 : 6
        }))
        service.entries = many
        // Stop periodic refresh without changing production code.
        objects.filter(o => o.repeat === true && o.interval === 1000).forEach(o => o.stop())
      } else if (phase === 1) {
        check(cards.length === listCount, "all requested demo cards rendered")
        check(viewport && viewport.clip && viewport.contentHeight > viewport.height,
              "long account list must scroll inside a clipped viewport")
        check(objects.some(o => o.policy !== undefined && o.active === true),
              "long account list has a visibly active scrollbar")
        for (var card of cards) {
          var row = card.children[0]
          if (card.index === 0) console.log("LAYOUT_CARD_DIM", card.height, row.height, row.implicitHeight)
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
        plugin.moveCursor(listCount - 1)
      } else if (phase === 2) {
        var selected = cards.find(o => o.index === plugin.selectedIndex)
        check(plugin.selectedIndex === listCount - 1, "keyboard selected the last card")
        check(!!selected, "selected delegate exists")
        if (viewport && selected) {
          var point = selected.mapToItem(viewport, 0, 0)
          check(point.y >= -1 && point.y + selected.height <= viewport.height + 1,
                "keyboard selection must scroll the last card into view")
        }
        savedScroll = viewport ? viewport.contentY : 0
        check(savedScroll > 0, "test actually scrolled away from the top")
        // Also exercise manual scrolling with an unrelated selection: refresh
        // must not helpfully reveal the first row and undo the user's scroll.
        plugin.cursorActive = false
        plugin.selectedId = plugin.visibleEntries[0].id
        // Replacing helper JSON with fresh codes used to destroy every Repeater
        // delegate and momentarily shrink its content, resetting contentY to zero.
        service.entries = service.entries.map(e => Object.assign({}, e, {code: e.digits === 8 ? "11223344" : "112233", remaining: 30}))
      } else if (phase === 3) {
        check(viewport && Math.abs(viewport.contentY - savedScroll) < 2,
              "OTP rollover preserves the scrolled viewport")
        if (rollovers++ < 3) {
          service.entries = service.entries.map(e => Object.assign({}, e, {remaining: 30 - rollovers}))
          return
        }
        var extra = service.entries.slice()
        for (var j = listCount; j < listCount + 5; j++) extra.push(Object.assign({}, extra[j % listCount], {id: "demo-" + j}))
        service.entries = extra
      } else if (phase === 4) {
        check(cards.length === listCount + 5, "adding five accounts renders every card")
        check(viewport && Math.abs(viewport.contentY - savedScroll) < 2,
              "adding accounts preserves the scrolled viewport")
        check(popup.contentHeight <= Style.space(520) && viewport.clip,
              "adding five accounts keeps the capped frame and scrolling")
        plugin.searchText = "no-demo-account-matches-this"
      } else if (phase === 5) {
        check(cards.length === 0, "search filters the cards")
        check(popup.contentHeight < Style.space(200), "border shrinks for no matches")
        if (viewport) check(viewport.contentY === 0, "search resets scroll position")
        // Start a real demo helper request, then close before its exit signal.
        if (service.owners !== undefined) service.owners = [plugin]
        service.refresh()
        plugin.close()
      } else if (phase === 6) {
        check(service && service.entries.length === 0, "closed panel rejects in-flight code reply")
        check(service && service._out === "", "closed panel clears transient helper output")
        console.log((failures.length ? "LAYOUT_FAIL " : "LAYOUT_PASS ") + JSON.stringify(failures))
        Qt.quit()
      }
      phase++
    }
  }
}

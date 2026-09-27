import QtQuick
import QtQuick.Shapes
import qs.Commons

// Monochrome bar glyph (open ring + keyhole), drawn natively with
// QtQuick.Shapes the same way Omarchy's built-in Dropbox/Tailscale marks are:
// no bitmap, no tile, no background. It is filled with `color`, which the
// panel binds to the bar foreground so it follows every theme.
//
// Path data is identical to assets/icon.svg (24×24 viewBox). Keep them in sync.
Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Color.foreground
  property real iconOpacity: 1.0

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  Shape {
    id: glyph
    width: 24
    height: 24
    scale: root.iconSize / 24
    transformOrigin: Item.TopLeft
    opacity: root.iconOpacity
    antialiasing: true
    preferredRendererType: Shape.CurveRenderer

    ShapePath {
      fillColor: root.color
      fillRule: ShapePath.WindingFill
      strokeWidth: -1
      strokeColor: "transparent"
      PathSvg { path: "M21.848 10.264A10 10 0 1 1 15.420 2.603A2 2 0 0 1 14.052 6.362A6 6 0 1 0 17.909 10.958A2 2 0 0 1 21.848 10.264Z" }
      PathSvg { path: "M12 8.6A2.2 2.2 0 1 1 12 13A2.2 2.2 0 1 1 12 8.6Z" }
      PathSvg { path: "M11.1 12.2L12.9 12.2L13.5 16.4L10.5 16.4Z" }
    }
  }
}

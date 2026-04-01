"""
sbs/overlays.py
All overlay widgets for the SBS image editor:
  CropOverlay, ShapePlacerOverlay, DrawOverlay,
  TransformOverlay, MovableRectOverlay, MovableLassoOverlay, MagicWandOverlay.
"""
import math

from PyQt6.QtWidgets import QWidget, QInputDialog
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QPen, QBrush, QPolygon,
    QCursor, QPainterPath, QFont, QImage
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, pyqtSignal

try:
    from PIL import Image as PILImage, ImageFilter, ImageDraw, ImageChops
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .utils import SHAPE_LIBRARY, draw_shape_on_pil, _scale_pts, pil_to_qpixmap

class CropOverlay(QWidget):
    """
    Transparent overlay widget for crop mode.

    OVERLAY CONCEPT:
    Instead of modifying the image directly, an invisible widget is placed
    over the canvas. This widget intercepts all mouse events.
    The actual PIL image is only cropped after the mouse button is released.
    Non-destructive: the user sees a preview before anything is committed.

    Mode 'rect':  click and drag → rectangle selection
    Mode 'lasso': freehand draw → polygon mask

    Signals (Signal/Slot pattern):
      rect_selected  → ImageEditor._do_rect_crop()
      lasso_selected → ImageEditor._do_lasso_crop()
      cancelled      → status bar "Cancelled"
    """
    rect_selected  = pyqtSignal(QRect)   # rectangle coordinates ready
    lasso_selected = pyqtSignal(list)    # lasso points ready
    cancelled      = pyqtSignal()        # ESC pressed

    def __init__(self, parent, mode: str = "rect"):
        """
        Create the crop overlay covering the given parent widget.

        Parameters
        ----------
        parent : Parent widget (ImageCanvas); the overlay covers it completely.
        mode   : 'rect' for rectangle selection, 'lasso' for freehand polygon.
        """
        super().__init__(parent)
        self.mode      = mode          # "rect" or "lasso"
        self.start_pt  = None          # start point (rectangle)
        self.end_pt    = None          # end point (rectangle)
        self.lasso_pts = []            # lasso points
        self.drawing   = False

        # Full-size, transparent, always on top
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        """Escape → cancel; emits the cancelled signal and closes the overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    # ── Rectangle mode ──────────────────────────
    def mousePressEvent(self, event):
        """Left mouse button: begin drawing, set start and end point."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drawing  = True
            self.start_pt = event.pos()
            self.end_pt   = event.pos()
            self.lasso_pts = [event.pos()]
            self.update()

    def mouseMoveEvent(self, event):
        """Mouse moved: update end point; in lasso mode also append point to the list."""
        if self.drawing:
            self.end_pt = event.pos()
            if self.mode == "lasso":
                self.lasso_pts.append(event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        """
        Mouse button released: finalise the selection.
        If the selection is large enough, the appropriate signal is emitted.
        Selections that are too small emit the cancelled signal instead.
        """
        if event.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.drawing = False
            if self.mode == "rect" and self.start_pt and self.end_pt:
                rect = QRect(self.start_pt, self.end_pt).normalized()
                # Minimum size 10×10 px to prevent accidental selections
                if rect.width() > 10 and rect.height() > 10:
                    self.rect_selected.emit(rect)
                else:
                    self.cancelled.emit()
            elif self.mode == "lasso" and len(self.lasso_pts) > 5:
                self.lasso_selected.emit(self.lasso_pts)
            else:
                self.cancelled.emit()
            self.close()

    def paintEvent(self, event):
        """Draw the selection marker on the transparent overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark dimming overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        pen = QPen(QColor(79, 195, 247), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)

        if self.mode == "rect" and self.start_pt and self.end_pt:
            sel_rect = QRect(self.start_pt, self.end_pt).normalized()
            # Brighten the selected area
            painter.fillRect(sel_rect, QColor(255, 255, 255, 30))
            painter.drawRect(sel_rect)
            # Show dimensions
            painter.setPen(QPen(QColor(79, 195, 247)))
            painter.setFont(QFont("Monospace", 10))
            painter.drawText(sel_rect.bottomLeft() + QPoint(4, 16),
                             f"{sel_rect.width()} × {sel_rect.height()} px")

        elif self.mode == "lasso" and len(self.lasso_pts) > 1:
            pen2 = QPen(QColor(79, 195, 247), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen2)
            for i in range(len(self.lasso_pts) - 1):
                painter.drawLine(self.lasso_pts[i], self.lasso_pts[i + 1])
            # Connect closing point back to start
            if len(self.lasso_pts) > 2:
                painter.setPen(QPen(QColor(79, 195, 247, 120), 1, Qt.PenStyle.DotLine))
                painter.drawLine(self.lasso_pts[-1], self.lasso_pts[0])


# ══════════════════════════════════════════════════════════════
#  SHAPE PLACER OVERLAY: place a shape on the image
# ══════════════════════════════════════════════════════════════

class ShapePlacerOverlay(QWidget):
    """
    Overlay for the text-to-drawing feature.
    Shows a live preview of the chosen shape under the mouse cursor.
    Click → shape is permanently drawn on the image at that position.
    Escape → cancel.
    """
    shape_placed = pyqtSignal(str, int, int)  # shape_key, x, y (image coordinates)
    cancelled    = pyqtSignal()

    def __init__(self, parent, shape_key: str, size: int,
                 color: QColor, zoom: float):
        """
        Create the shape placer overlay.

        Parameters
        ----------
        parent    : Parent widget (ImageCanvas).
        shape_key : Key of the shape to place, looked up in SHAPE_LIBRARY.
        size      : Desired shape size in image pixels.
        color     : Drawing colour (QColor).
        zoom      : Current canvas zoom factor, used to convert coordinates.
        """
        super().__init__(parent)
        self.shape_key  = shape_key
        self.shape_size = size
        self.color      = color
        self.zoom       = zoom
        self._mouse_pos = QPoint(0, 0)

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        """Escape → cancel; emits the cancelled signal and closes the overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()

    def mouseMoveEvent(self, event):
        """Track the mouse cursor to update the live shape preview."""
        self._mouse_pos = event.pos()
        self.update()

    def mousePressEvent(self, event):
        """
        Left mouse click: place the shape.
        Overlay coordinates are divided by zoom to obtain true image pixels.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # Overlay position → image coordinates by dividing by the zoom factor
            ix = int(event.pos().x() / self.zoom)
            iy = int(event.pos().y() / self.zoom)
            self.shape_placed.emit(self.shape_key, ix, iy)
            self.close()

    def paintEvent(self, event):
        """
        Draw the shape preview under the mouse cursor.
        Semi-transparent so the image below remains visible.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Slightly darken the background
        painter.fillRect(self.rect(), QColor(0, 0, 0, 40))

        mx, my = self._mouse_pos.x(), self._mouse_pos.y()
        half   = self.shape_size * self.zoom // 2

        # Crosshair guide lines
        pen = QPen(QColor(79, 195, 247, 100), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(mx, 0, mx, self.height())
        painter.drawLine(0, my, self.width(), my)

        # Shape bounding box
        pen2 = QPen(QColor(79, 195, 247, 180), 1, Qt.PenStyle.DotLine)
        painter.setPen(pen2)
        painter.drawRect(int(mx - half), int(my - half),
                         int(self.shape_size * self.zoom),
                         int(self.shape_size * self.zoom))

        # Info label
        painter.setPen(QPen(QColor(79, 195, 247)))
        painter.setFont(QFont("Monospace", 10))
        painter.drawText(mx + int(half) + 8, my - 4,
                         f"{self.shape_key}  {self.shape_size}px  — Klick zum Platzieren  |  ESC = Abbruch")


# ══════════════════════════════════════════════════════════════
#  HELPER FUNCTION: Catmull-Rom spline (for the curve tool)
# ══════════════════════════════════════════════════════════════

def _catmull_rom_pts(pts, n_per_seg: int = 12) -> list:
    """
    Generate a smooth list of interpolated points through all control points
    using a Catmull-Rom spline. Returns a list of QPoints.

    Used by the curve tool:
      - n_per_seg : number of interpolated points per segment.
      - Higher values produce smoother curves at increased computational cost
        (12 is a good balance).

    Parameters
    ----------
    pts      : List of QPoint control points.
    n_per_seg: Interpolated steps between each pair of control points.

    Returns
    -------
    List of QPoint objects tracing the smooth spline.
    """
    if len(pts) < 2:
        return list(pts)
    ext = [pts[0]] + list(pts) + [pts[-1]]  # duplicate boundary points
    result = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i-1], ext[i], ext[i+1], ext[i+2]
        for j in range(n_per_seg):
            t  = j / n_per_seg
            t2 = t * t
            t3 = t2 * t
            x = int(0.5 * ((2*p1.x()) + (-p0.x()+p2.x())*t
                           + (2*p0.x()-5*p1.x()+4*p2.x()-p3.x())*t2
                           + (-p0.x()+3*p1.x()-3*p2.x()+p3.x())*t3))
            y = int(0.5 * ((2*p1.y()) + (-p0.y()+p2.y())*t
                           + (2*p0.y()-5*p1.y()+4*p2.y()-p3.y())*t2
                           + (-p0.y()+3*p1.y()-3*p2.y()+p3.y())*t3))
            result.append(QPoint(x, y))
    result.append(pts[-1])
    return result


# ══════════════════════════════════════════════════════════════
#  DRAW OVERLAY: draw directly on the image
# ══════════════════════════════════════════════════════════════

class DrawOverlay(QWidget):
    """
    Drawing overlay — implements Paint/GIMP-style tools.

    ARCHITECTURE (two-layer model):
    ┌─────────────────────────────────────────┐
    │  Layer 1: self._preview (QPixmap)       │  ← Temporary, preview only
    │  Freehand strokes are rendered here     │    while the mouse is moving
    ├─────────────────────────────────────────┤
    │  Layer 2: PIL Image (permanent)         │  ← Real image, only updated
    │  Updated after mouseRelease             │    when drawing_done fires
    └─────────────────────────────────────────┘

    TOOLS and their PIL implementation:
      'pen'     → ImageDraw.line(), width = brush_size / zoom
      'brush'   → like pen, but alpha=160 (semi-transparent) + 3× wider
      'eraser'  → like pen, but colour=white (paints over)
      'line'    → ImageDraw.line() from start to end point
      'rect'    → ImageDraw.rectangle() outline only
      'ellipse' → ImageDraw.ellipse() outline only
      'text'    → QInputDialog → ImageDraw.text()

    ZOOM CORRECTION:
    Overlay coordinates are in screen pixels (zoomed).
    PIL needs true image pixels → divide by the zoom factor.
    Example: click at x=200, zoom=2.0 → image pixel x=100

    Signal:
      drawing_done(fn) → ImageEditor._apply_draw_fn()
      fn is a callable that transforms a PIL Image → PIL Image
    """
    drawing_done = pyqtSignal(object)  # PIL drawing function as a callable

    def __init__(self, parent, tool: str, color: QColor,
                 size: int, zoom: float, texture=None):
        """
        Create the drawing overlay.

        Parameters
        ----------
        parent  : Parent widget (ImageCanvas).
        tool    : Tool name: 'pen', 'brush', 'eraser', 'line', 'rect',
                  'ellipse', 'text', 'blur', 'curve', 'texture_brush'.
        color   : Current drawing colour (QColor).
        size    : Brush size in overlay pixels.
        zoom    : Canvas zoom factor, used to convert coordinates.
        texture : Optional PIL RGBA image used as a texture for the texture brush.
        """
        super().__init__(parent)
        self.tool        = tool
        self.color       = color
        self.brush_size  = size
        self.zoom        = zoom          # current canvas zoom factor
        self.texture     = texture       # PIL RGBA texture image (or None)

        self.drawing     = False
        self.start_pt    = None
        self.last_pt     = None
        self.stroke_pts  = []            # points of the current stroke
        self.bezier_pts  = []            # control points for the curve tool

        # Temporary QPixmap for the preview while drawing
        self._preview    = QPixmap(parent.size())
        self._preview.fill(QColor(0, 0, 0, 0))

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")

        # Cursor shape per tool
        cursors = {
            "pen":             Qt.CursorShape.CrossCursor,
            "brush":           Qt.CursorShape.CrossCursor,
            "eraser":          Qt.CursorShape.CrossCursor,
            "line":            Qt.CursorShape.CrossCursor,
            "rect":            Qt.CursorShape.CrossCursor,
            "ellipse":         Qt.CursorShape.CrossCursor,
            "text":            Qt.CursorShape.IBeamCursor,
            "blur":            Qt.CursorShape.CrossCursor,
            "curve":           Qt.CursorShape.CrossCursor,
            "texture_brush":   Qt.CursorShape.CrossCursor,
        }
        self.setCursor(QCursor(cursors.get(tool, Qt.CursorShape.CrossCursor)))
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        """Escape → close the overlay and exit drawing mode."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    # ── Mouse events ─────────────────────────

    def mousePressEvent(self, event):
        """
        Mouse button pressed: begin drawing.
        Curve tool: left click = add point, right click = finalise.
        All other tools: left click starts the stroke.
        """
        # ── Curve tool: left = add point, right = finalise curve
        if self.tool == "curve":
            if event.button() == Qt.MouseButton.LeftButton:
                self.bezier_pts.append(event.pos())
                self.update()
            elif event.button() == Qt.MouseButton.RightButton:
                if len(self.bezier_pts) >= 2:
                    self.drawing_done.emit(self._make_curve_fn(
                        list(self.bezier_pts), self.color, self.brush_size, self.zoom))
                self.bezier_pts = []
                self.update()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.drawing  = True
        self.start_pt = event.pos()
        self.last_pt  = event.pos()
        self.stroke_pts = [event.pos()]

        if self.tool == "text":
            # Text tool: open dialog immediately
            self._do_text(event.pos())
            return

        # Freehand tools: draw the first point
        if self.tool in ("pen", "brush", "eraser", "blur", "texture_brush"):
            self._draw_point(event.pos())

    def mouseMoveEvent(self, event):
        """
        Handle mouse movement:
        - Freehand tools: draw a stroke segment onto the preview layer.
        - Shape tools (line, rect, ellipse): update the preview only.
        - Curve tool: show a live guide line from the last point to the cursor.
        """
        if self.tool == "curve":
            # Live preview of the next segment line (before click)
            self.last_pt = event.pos()
            self.update()
            return
        if not self.drawing:
            return
        pos = event.pos()

        if self.tool in ("pen", "brush", "eraser", "blur", "texture_brush"):
            # Freehand: draw a line from the last point to the current point
            self._draw_line(self.last_pt, pos)
            self.stroke_pts.append(pos)
            self.last_pt = pos
        else:
            # Shape tools: preview only, no permanent points yet
            self.last_pt = pos
            self.update()

    def mouseReleaseEvent(self, event):
        """
        Mouse button released: finalise the stroke and create the PIL draw function.
        The drawing_done signal is emitted with a callable that will be applied
        to the PIL image.
        """
        if event.button() != Qt.MouseButton.LeftButton or not self.drawing:
            return
        self.drawing = False

        tex = self.texture
        if self.tool in ("pen", "brush", "eraser"):
            self.drawing_done.emit(self._make_freehand_fn(
                list(self.stroke_pts), self.tool,
                self.color, self.brush_size, self.zoom
            ))
        elif self.tool == "texture_brush":
            self.drawing_done.emit(self._make_texture_fn(
                list(self.stroke_pts), tex, self.brush_size, self.zoom
            ))
        elif self.tool == "blur":
            self.drawing_done.emit(self._make_blur_fn(
                list(self.stroke_pts), self.brush_size, self.zoom
            ))
        elif self.tool == "line":
            self.drawing_done.emit(self._make_line_fn(
                self.start_pt, event.pos(),
                self.color, self.brush_size, self.zoom
            ))
        elif self.tool == "rect":
            self.drawing_done.emit(self._make_rect_fn(
                self.start_pt, event.pos(),
                self.color, self.brush_size, self.zoom
            ))
        elif self.tool == "ellipse":
            self.drawing_done.emit(self._make_ellipse_fn(
                self.start_pt, event.pos(),
                self.color, self.brush_size, self.zoom
            ))

        # Clear the preview layer
        self._preview.fill(QColor(0, 0, 0, 0))
        self.update()

    # ── Drawing onto the preview layer ─────────

    def _pen_for_tool(self, tool: str, color: QColor, size: int) -> QPen:
        """
        Return the appropriate QPen for the given tool.

        Parameters
        ----------
        tool  : Tool name string.
        color : Drawing colour.
        size  : Base brush size in pixels.

        Returns
        -------
        QPen configured for the tool's visual style.
        """
        if tool == "eraser":
            pen = QPen(QColor(255, 255, 255, 255), size * 2,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                       Qt.PenJoinStyle.RoundJoin)
        elif tool == "brush":
            pen = QPen(QColor(color.red(), color.green(), color.blue(), 160),
                       size * 3, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        elif tool == "blur":
            pen = QPen(QColor(100, 200, 255, 70), max(4, size * 2),
                       Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        else:
            pen = QPen(color, size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        return pen

    def _draw_point(self, pos: QPoint):
        """Draw a single point onto the preview layer (e.g. on the first click)."""
        painter = QPainter(self._preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen_for_tool(self.tool, self.color, self.brush_size))
        painter.drawPoint(pos)
        painter.end()
        self.update()

    def _draw_line(self, p1: QPoint, p2: QPoint):
        """Draw a line segment between two points onto the preview layer."""
        painter = QPainter(self._preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen_for_tool(self.tool, self.color, self.brush_size))
        painter.drawLine(p1, p2)
        painter.end()
        self.update()

    def paintEvent(self, event):
        """
        Draw the preview layer and any shape preview.
        Freehand strokes come from _preview; shapes are rendered directly here.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Freehand preview
        painter.drawPixmap(0, 0, self._preview)

        # Shape preview (line, rectangle, ellipse)
        if self.drawing and self.start_pt and self.last_pt:
            pen = self._pen_for_tool(self.tool, self.color, self.brush_size)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            if self.tool == "line":
                painter.drawLine(self.start_pt, self.last_pt)
            elif self.tool == "rect":
                painter.drawRect(QRect(self.start_pt, self.last_pt).normalized())
            elif self.tool == "ellipse":
                painter.drawEllipse(QRect(self.start_pt, self.last_pt).normalized())

        # Curve preview: spline through all placed points so far
        if self.tool == "curve" and self.bezier_pts:
            pen = self._pen_for_tool("pen", self.color, self.brush_size)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Mark each control point with a small circle
            dot_pen = QPen(QColor(79, 195, 247), 1)
            painter.setPen(dot_pen)
            for pt in self.bezier_pts:
                painter.drawEllipse(pt, 5, 5)
            # Draw the spline when at least 2 points are placed
            if len(self.bezier_pts) >= 2:
                spline = _catmull_rom_pts(self.bezier_pts)
                pen2 = self._pen_for_tool("pen", self.color, self.brush_size)
                painter.setPen(pen2)
                for i in range(len(spline) - 1):
                    painter.drawLine(spline[i], spline[i + 1])
            # Guide line from the last point to the cursor
            if self.last_pt:
                painter.setPen(QPen(QColor(79, 195, 247, 120), 1,
                                    Qt.PenStyle.DashLine))
                painter.drawLine(self.bezier_pts[-1], self.last_pt)
            # Usage instructions
            painter.setPen(QPen(QColor(79, 195, 247)))
            painter.setFont(QFont("Monospace", 9))
            painter.drawText(10, 20,
                f"Kurve: {len(self.bezier_pts)} Punkte — Linksklick = Punkt hinzufügen "
                f"| Rechtsklick = Abschließen | ESC = Abbrechen")

    # ── PIL draw functions (permanent, applied to the image) ──

    @staticmethod
    def _make_freehand_fn(pts, tool, color, size, zoom):
        """
        Return a function that draws a freehand stroke onto a PIL image.
        zoom is used to convert overlay coordinates to image pixels.

        Parameters
        ----------
        pts   : List of QPoint stroke points in overlay (screen) coordinates.
        tool  : Tool name ('pen', 'brush', or 'eraser').
        color : Drawing colour (QColor).
        size  : Brush size in overlay pixels.
        zoom  : Canvas zoom factor for coordinate conversion.

        Returns
        -------
        Callable that accepts and returns a PIL Image.
        """
        def draw(img):
            draw_obj = ImageDraw.Draw(img, "RGBA")
            r, g, b = color.red(), color.green(), color.blue()
            if tool == "eraser":
                fill = (255, 255, 255, 255)
                w = int(size * 2 / zoom)
            elif tool == "brush":
                fill = (r, g, b, 160)
                w = int(size * 3 / zoom)
            else:
                fill = (r, g, b, 255)
                w = max(1, int(size / zoom))
            # Convert points to image coordinates
            img_pts = [(int(p.x() / zoom), int(p.y() / zoom)) for p in pts]
            if len(img_pts) == 1:
                x, y = img_pts[0]
                draw_obj.ellipse([x - w, y - w, x + w, y + w], fill=fill)
            else:
                for i in range(len(img_pts) - 1):
                    draw_obj.line([img_pts[i], img_pts[i + 1]],
                                  fill=fill, width=max(1, w))
            return img
        return draw

    @staticmethod
    def _make_line_fn(p1, p2, color, size, zoom):
        """Return a function that draws a straight line onto a PIL image."""
        def draw(img):
            d = ImageDraw.Draw(img, "RGBA")
            x1, y1 = int(p1.x() / zoom), int(p1.y() / zoom)
            x2, y2 = int(p2.x() / zoom), int(p2.y() / zoom)
            d.line([(x1, y1), (x2, y2)],
                   fill=(color.red(), color.green(), color.blue(), 255),
                   width=max(1, int(size / zoom)))
            return img
        return draw

    @staticmethod
    def _make_rect_fn(p1, p2, color, size, zoom):
        """Return a function that draws an outlined rectangle onto a PIL image."""
        def draw(img):
            d = ImageDraw.Draw(img, "RGBA")
            x1, y1 = int(p1.x() / zoom), int(p1.y() / zoom)
            x2, y2 = int(p2.x() / zoom), int(p2.y() / zoom)
            if x1 > x2: x1, x2 = x2, x1
            if y1 > y2: y1, y2 = y2, y1
            d.rectangle([(x1, y1), (x2, y2)],
                        outline=(color.red(), color.green(), color.blue(), 255),
                        width=max(1, int(size / zoom)))
            return img
        return draw

    @staticmethod
    def _make_ellipse_fn(p1, p2, color, size, zoom):
        """Return a function that draws an outlined ellipse onto a PIL image."""
        def draw(img):
            d = ImageDraw.Draw(img, "RGBA")
            x1, y1 = int(p1.x() / zoom), int(p1.y() / zoom)
            x2, y2 = int(p2.x() / zoom), int(p2.y() / zoom)
            if x1 > x2: x1, x2 = x2, x1
            if y1 > y2: y1, y2 = y2, y1
            d.ellipse([(x1, y1), (x2, y2)],
                      outline=(color.red(), color.green(), color.blue(), 255),
                      width=max(1, int(size / zoom)))
            return img
        return draw

    @staticmethod
    def _make_blur_fn(pts, size, zoom):
        """
        Return a function implementing a blur brush.

        Applies a Gaussian blur to small patches along the stroke path.
        Each stroke point blurs a 2×brush_size region of the image.
        """
        def draw(img):
            img = img.convert("RGBA")
            r = max(2, int(size * 2 / zoom))
            step = max(1, len(pts) // 50)  # sub-sample for performance
            for pt in pts[::step]:
                x, y = int(pt.x() / zoom), int(pt.y() / zoom)
                x1, y1 = max(0, x - r), max(0, y - r)
                x2, y2 = min(img.width, x + r), min(img.height, y + r)
                if x2 <= x1 or y2 <= y1:
                    continue
                patch = img.crop((x1, y1, x2, y2))
                blurred = patch.filter(ImageFilter.GaussianBlur(radius=max(1, r // 2)))
                img.paste(blurred, (x1, y1))
            return img
        return draw

    @staticmethod
    def _make_curve_fn(pts, color, size, zoom):
        """
        Return a function that draws a smooth Catmull-Rom curve through all
        control points onto the PIL image.  The curve is rendered as a dense
        sequence of short line segments.
        """
        def draw(img):
            spline = _catmull_rom_pts(pts, n_per_seg=16)
            img_pts = [(int(p.x() / zoom), int(p.y() / zoom)) for p in spline]
            d = ImageDraw.Draw(img, "RGBA")
            w = max(1, int(size / zoom))
            fill = (color.red(), color.green(), color.blue(), 255)
            for i in range(len(img_pts) - 1):
                d.line([img_pts[i], img_pts[i + 1]], fill=fill, width=w)
            return img
        return draw

    @staticmethod
    def _make_texture_fn(pts, texture, size, zoom):
        """
        Return a function implementing a texture brush.

        Stamps the texture image repeatedly along the stroke path.  The stamp
        interval is half the texture width so that no gaps appear.
        If no texture is loaded, the function is a no-op.
        """
        def draw(img):
            if texture is None:
                return img
            stamp_size = max(8, int(size * 2 / zoom))
            tex = texture.copy().convert("RGBA")
            tex = tex.resize((stamp_size, stamp_size), PILImage.Resampling.LANCZOS)
            step = max(1, stamp_size // 2)
            img_pts = [(int(p.x() / zoom), int(p.y() / zoom)) for p in pts]
            for pt in img_pts[::step]:
                x = pt[0] - stamp_size // 2
                y = pt[1] - stamp_size // 2
                # Use the texture's alpha channel as the paste mask
                mask = tex.split()[3]
                img.paste(tex, (x, y), mask)
            return img
        return draw

    def _do_text(self, pos: QPoint):
        """
        Text tool: open an input dialog and draw the entered text at the clicked image position.

        Font size is proportional to brush size (size × 3, minimum 12 pt).
        """
        text, ok = QInputDialog.getText(self, "Text einfügen", "Text:")
        if ok and text:
            zoom = self.zoom
            color = self.color
            size = self.brush_size
            # Convert click position from overlay coordinates to real image pixels
            x = int(pos.x() / zoom)
            y = int(pos.y() / zoom)

            def draw(img):
                d = ImageDraw.Draw(img, "RGBA")
                font_size = max(12, int(size * 3))
                try:
                    from PIL import ImageFont
                    # Try to load the system font (macOS path; falls back to PIL's default on other OS)
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
                except Exception:
                    font = None   # PIL will use its built-in bitmap font
                d.text((x, y), text,
                       fill=(color.red(), color.green(), color.blue(), 255),
                       font=font)
                return img

            self.drawing_done.emit(draw)
        self.close()


# ══════════════════════════════════════════════════════════════
#  TRANSFORM OVERLAY: move and scale a layer excerpt
# ══════════════════════════════════════════════════════════════
class TransformOverlay(QWidget):
    """
    Interactive overlay for moving and uniformly scaling a layer.

    Controls
    --------
    • Left-click + drag → move the object
    • Mouse wheel       → scale up / down
    • Enter             → confirm changes
    • ESC               → cancel (restore original position)
    """
    transform_done = pyqtSignal(int, int, float)   # x, y, scale
    cancelled      = pyqtSignal()

    def __init__(self, parent, pil_img, layer_x: int, layer_y: int, zoom: float):
        """
        Create the transform overlay.

        Parameters
        ----------
        parent  : Parent widget (ImageCanvas).
        pil_img : PIL RGBA image of the layer to transform.
        layer_x : Current X position of the layer in image pixels.
        layer_y : Current Y position of the layer in image pixels.
        zoom    : Current zoom factor of the canvas.
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        self._pil       = pil_img          # original PIL RGBA image
        self._zoom      = zoom
        self._ox        = layer_x          # starting position in image coordinates
        self._oy        = layer_y
        self._dx        = 0.0              # displacement in overlay pixels
        self._dy        = 0.0
        self._scale     = 1.0
        self._drag_start: QPoint | None = None
        self._drag_orig: tuple[float, float] = (0.0, 0.0)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_pixmap()
        self.show()
        self.setFocus()

    # ── Helpers ──────────────────────────────────────────────

    def _update_pixmap(self):
        """Scale the PIL image by the current scale factor and cache it as a QPixmap."""
        w = max(1, int(self._pil.width  * self._scale))
        h = max(1, int(self._pil.height * self._scale))
        scaled = self._pil.resize((w, h), PILImage.Resampling.LANCZOS)
        self._pix = pil_to_qpixmap(scaled)

    def _screen_rect(self) -> QRect:
        """Return the bounding rectangle of the object in overlay (screen) coordinates."""
        # Object origin in overlay px = (ox + dx/zoom) * zoom = ox*zoom + dx
        sx = int(self._ox * self._zoom + self._dx)
        sy = int(self._oy * self._zoom + self._dy)
        w  = int(self._pil.width  * self._scale * self._zoom)
        h  = int(self._pil.height * self._scale * self._zoom)
        return QRect(sx, sy, w, h)

    # ── Events ────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """Left-click: begin dragging — record the start position and current offset."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self._drag_orig  = (self._dx, self._dy)

    def mouseMoveEvent(self, event):
        """Mouse move: compute offset relative to drag start and refresh the preview."""
        if self._drag_start is not None:
            delta = event.pos() - self._drag_start
            self._dx = self._drag_orig[0] + delta.x()
            self._dy = self._drag_orig[1] + delta.y()
            self.update()

    def mouseReleaseEvent(self, event):
        """Mouse button released: end drag mode."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None

    def wheelEvent(self, event):
        """
        Mouse wheel: adjust the scale factor.
        Factor 1.1 per scroll step (120 delta units = 1 step).
        Clamped to the range 0.05–20× (5 % to 2000 % of original size).
        """
        steps  = event.angleDelta().y() / 120
        factor = 1.1 ** steps
        self._scale = max(0.05, min(self._scale * factor, 20.0))
        self._update_pixmap()
        self.update()

    def keyPressEvent(self, event):
        """
        Enter → confirm transform: convert overlay displacement to image coordinates
        and emit the transform_done signal.
        Escape → cancel.
        """
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            # Convert overlay displacement to image coordinates by dividing by zoom
            new_x = int(self._ox + self._dx / self._zoom)
            new_y = int(self._oy + self._dy / self._zoom)
            self.transform_done.emit(new_x, new_y, self._scale)
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        """Draw the scaled image with a blue dashed border and a hint text on the overlay."""
        p = QPainter(self)
        r = self._screen_rect()
        # Draw the image scaled to the current zoom level
        rz = QRect(r.x(), r.y(),
                   int(self._pix.width()  * self._zoom),
                   int(self._pix.height() * self._zoom))
        p.drawPixmap(rz.x(), rz.y(),
                     self._pix.scaled(rz.width(), rz.height(),
                                      Qt.AspectRatioMode.IgnoreAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        # Draw blue dashed border around the object
        p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.PenStyle.DashLine))
        p.drawRect(rz)
        # Hint text above the object
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 9))
        p.drawText(rz.x(), rz.y() - 6,
                   "Drag=Move  |  Scroll=Scale  |  Enter=OK  |  ESC=Cancel")
        p.end()


# ══════════════════════════════════════════════════════════════
#  MOVABLE RECT OVERLAY: Auswahlrechteck verschieben & skalieren
# ══════════════════════════════════════════════════════════════

class MovableRectOverlay(QWidget):
    """
    Displays the selection rectangle after drawing and allows moving
    and resizing (corner/edge handles) before the final crop is applied.

    Enter = confirm crop  |  ESC = cancel
    """
    confirmed = pyqtSignal(object)   # QRect in overlay coordinates
    cancelled = pyqtSignal()

    _HS = 10   # Handle size in pixels

    def __init__(self, parent, rect: QRect):
        """
        Create the movable selection rectangle overlay.

        Parameters
        ----------
        parent : QWidget
            Parent widget (ImageCanvas).
        rect : QRect
            Initial selection rectangle in overlay coordinates.
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rect      = QRect(rect).normalized()
        self._mode: str | None = None
        self._drag_pt   = QPoint()
        self._orig_rect = QRect()
        self.show()
        self.setFocus()

    # ── Handles ──────────────────────────────────────────────

    def _handles(self) -> dict:
        """
        Return a dictionary of the 8 resize handles for the selection rectangle.
        Each handle is a small QRect centred on the corresponding corner or edge midpoint.
        Keys: 'tl', 'tm', 'tr', 'ml', 'mr', 'bl', 'bm', 'br'
        (t=top, b=bottom, l=left, r=right, m=middle)
        """
        r  = self._rect
        h  = self._HS
        cx = (r.left() + r.right())  // 2
        cy = (r.top()  + r.bottom()) // 2
        def hr(x, y): return QRect(x - h//2, y - h//2, h, h)
        return {
            'tl': hr(r.left(),  r.top()),    'tm': hr(cx,        r.top()),
            'tr': hr(r.right(), r.top()),    'ml': hr(r.left(),  cy),
            'mr': hr(r.right(), cy),         'bl': hr(r.left(),  r.bottom()),
            'bm': hr(cx,        r.bottom()), 'br': hr(r.right(), r.bottom()),
        }

    _CURSORS = {
        'tl': Qt.CursorShape.SizeFDiagCursor, 'br': Qt.CursorShape.SizeFDiagCursor,
        'tr': Qt.CursorShape.SizeBDiagCursor, 'bl': Qt.CursorShape.SizeBDiagCursor,
        'tm': Qt.CursorShape.SizeVerCursor,   'bm': Qt.CursorShape.SizeVerCursor,
        'ml': Qt.CursorShape.SizeHorCursor,   'mr': Qt.CursorShape.SizeHorCursor,
    }

    def _hit(self, pos: QPoint) -> str | None:
        """Return the name of the handle under *pos*, or None if no handle was hit."""
        for name, h in self._handles().items():
            if h.contains(pos): return name
        return None

    # ── Events ────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """
        Mouse button pressed: determine interaction mode.
        Handle hit → resize; inside rectangle → move; outside → do nothing.
        """
        pos = event.pos()
        h   = self._hit(pos)
        if h:
            self._mode = h
        elif self._rect.contains(pos):
            self._mode = 'move'
        else:
            self._mode = None; return
        self._drag_pt   = pos
        self._orig_rect = QRect(self._rect)

    def mouseMoveEvent(self, event):
        """
        Mouse move: update cursor shape (when not dragging) or adjust the rectangle
        (while dragging). Move, corner, and edge interactions are all handled here.
        """
        pos = event.pos()
        if self._mode is None:
            h = self._hit(pos)
            if h:
                self.setCursor(QCursor(self._CURSORS[h]))
            elif self._rect.contains(pos):
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
        d = pos - self._drag_pt
        r = QRect(self._orig_rect)
        m = self._mode
        if   m == 'move': r.translate(d)
        elif m == 'tl':   r.setTopLeft(r.topLeft() + d)
        elif m == 'tm':   r.setTop(r.top() + d.y())
        elif m == 'tr':   r.setTopRight(r.topRight() + d)
        elif m == 'ml':   r.setLeft(r.left() + d.x())
        elif m == 'mr':   r.setRight(r.right() + d.x())
        elif m == 'bl':   r.setBottomLeft(r.bottomLeft() + d)
        elif m == 'bm':   r.setBottom(r.bottom() + d.y())
        elif m == 'br':   r.setBottomRight(r.bottomRight() + d)
        r = r.normalized()
        if r.width() > 4 and r.height() > 4:
            self._rect = r
        self.update()

    def mouseReleaseEvent(self, _event):
        """Mouse button released: end drag mode."""
        self._mode = None

    def keyPressEvent(self, event):
        """Enter → confirm crop (emits confirmed signal). Escape → cancel."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirmed.emit(self._rect)
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        """
        Draw the overlay: dimmed area outside the selection (4 rectangles), a dashed
        selection border, blue square resize handles, and a hint bar at the bottom.
        """
        p = QPainter(self)
        r = self._rect
        # Dim the area outside the selection with 4 surrounding rectangles
        dim = QColor(0, 0, 0, 100)
        p.fillRect(0, 0, self.width(), r.top(),                      dim)
        p.fillRect(0, r.bottom(), self.width(), self.height(),        dim)
        p.fillRect(0, r.top(), r.left(), r.height(),                  dim)
        p.fillRect(r.right(), r.top(), self.width() - r.right(), r.height(), dim)
        # Dashed selection border
        p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        # Resize handles
        p.setBrush(QBrush(QColor("#4fc3f7")))
        p.setPen(QPen(QColor("#ffffff"), 1))
        for h in self._handles().values():
            p.drawRect(h)
        # Hint bar at the bottom
        bar_y = self.height() - 22
        p.fillRect(0, bar_y, self.width(), 22, QColor(0, 0, 0, 180))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 9))
        p.drawText(8, self.height() - 6,
                   "Drag=Move  |  Corners=Resize  |  Enter=Crop  |  ESC=Cancel")
        p.end()


# ══════════════════════════════════════════════════════════════
#  MOVABLE LASSO OVERLAY: Lasso-Auswahl verschieben
# ══════════════════════════════════════════════════════════════

class MovableLassoOverlay(QWidget):
    """
    After the lasso has been drawn: drag the polygon to reposition it
    before the crop is applied.

    Enter = crop  |  ESC = cancel
    """
    confirmed = pyqtSignal(list)   # list of QPoint (shifted)
    cancelled = pyqtSignal()

    def __init__(self, parent, points: list):
        """
        Create the movable lasso overlay.

        Parameters
        ----------
        parent : QWidget
            Parent widget (ImageCanvas).
        points : list of QPoint
            Points that define the lasso polygon.
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pts        = list(points)
        self._offset     = QPoint(0, 0)
        self._drag_start: QPoint | None = None
        self._drag_orig  = QPoint(0, 0)
        self.show()
        self.setFocus()

    def _poly_path(self) -> QPainterPath:
        """Build a QPainterPath from the lasso points, shifted by the current drag offset."""
        path = QPainterPath()
        if not self._pts: return path
        ox, oy = self._offset.x(), self._offset.y()
        p0 = self._pts[0]
        path.moveTo(p0.x() + ox, p0.y() + oy)
        for pt in self._pts[1:]:
            path.lineTo(pt.x() + ox, pt.y() + oy)
        path.closeSubpath()
        return path

    def _inside(self, pos: QPoint) -> bool:
        """Return True if *pos* lies inside the lasso polygon (used for drag detection)."""
        return self._poly_path().contains(QPointF(pos.x(), pos.y()))

    def mousePressEvent(self, event):
        """Click inside the polygon to start dragging; clicks outside are ignored."""
        if self._inside(event.pos()):
            self._drag_start = event.pos()
            self._drag_orig  = QPoint(self._offset)

    def mouseMoveEvent(self, event):
        """Update the polygon offset while dragging, or update the cursor shape otherwise."""
        if self._drag_start is not None:
            d = event.pos() - self._drag_start
            self._offset = self._drag_orig + d
            self.update()
        else:
            # Move cursor inside the polygon, arrow cursor outside
            cursor = Qt.CursorShape.SizeAllCursor if self._inside(event.pos()) \
                     else Qt.CursorShape.ArrowCursor
            self.setCursor(QCursor(cursor))

    def mouseReleaseEvent(self, _event):
        """End drag mode."""
        self._drag_start = None

    def keyPressEvent(self, event):
        """Enter → confirm lasso with the current offset. Escape → cancel."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ox, oy = self._offset.x(), self._offset.y()
            # Pass the shifted points to the confirmed signal
            self.confirmed.emit([QPoint(p.x()+ox, p.y()+oy) for p in self._pts])
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        """Draw the shifted lasso polygon with a dashed border and a hint bar at the bottom."""
        p = QPainter(self)
        p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._poly_path())
        bar_y = self.height() - 22
        p.fillRect(0, bar_y, self.width(), 22, QColor(0, 0, 0, 180))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 9))
        p.drawText(8, self.height() - 6,
                   "Drag=Move  |  Enter=Crop  |  ESC=Cancel")
        p.end()


# ══════════════════════════════════════════════════════════════
#  MAGIC WAND OVERLAY: Tolerance-based colour selection
# ══════════════════════════════════════════════════════════════

class MagicWandOverlay(QWidget):
    """
    Magic wand selection tool (similar to Photoshop Magic Wand).

    HOW IT WORKS (flood-fill with colour tolerance):
    1. User clicks a pixel → seed colour is determined.
    2. PIL's floodfill() expands outward from that pixel:
       neighbouring pixels are added to the selection if their colour
       is within the tolerance (sum |R-diff| + |G-diff| + |B-diff|).
    3. Result: a PIL mask (mode "L", 255 = selected, 0 = not selected).
    4. Shift+click = add another region to the existing selection.

    Signals
    -------
    selection_ready(mask) : emit the selection mask to the ImageEditor.
    cancelled             : abort the tool.
    """
    selection_ready = pyqtSignal(object)   # PIL "L"-Bild (Maske)
    cancelled       = pyqtSignal()

    def __init__(self, parent, pil_image, tolerance: int, zoom: float):
        """
        Create the magic wand overlay.

        Parameters
        ----------
        parent : QWidget
            Parent widget (ImageCanvas).
        pil_image : PIL.Image
            The image on which colour selection is performed.
        tolerance : int
            Colour tolerance for the flood-fill (0 = exact match, 100 = very wide).
        zoom : float
            Current canvas zoom factor, used to convert click coordinates to image pixels.
        """
        super().__init__(parent)
        self._pil      = pil_image.convert("RGBA")
        self._tol      = tolerance
        self._zoom     = zoom
        self._mask     = PILImage.new("L", pil_image.size, 0)
        self._ovr_pix  = None        # QPixmap of the selection overlay

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(False)
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        """Escape → cancel selection. Enter → confirm mask and pass it to the ImageEditor."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()
        elif event.key() == Qt.Key.Key_Return:
            self.selection_ready.emit(self._mask); self.close()

    def mousePressEvent(self, event):
        """
        Click on the image: run flood-fill from the clicked position.
        Shift held: extend the existing selection (additive mode).
        Without Shift: replace the existing selection with the new one.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Convert overlay coordinates to image pixels (correcting for zoom)
        ix = max(0, min(int(event.pos().x() / self._zoom), self._pil.width  - 1))
        iy = max(0, min(int(event.pos().y() / self._zoom), self._pil.height - 1))
        add = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        new_mask = self._flood_fill(ix, iy)
        if add:
            # Additive selection: union of both masks (brighter value wins)
            self._mask = ImageChops.lighter(self._mask, new_mask)
        else:
            self._mask = new_mask
        self._build_overlay()
        self.update()

    def _flood_fill(self, sx: int, sy: int) -> "PILImage.Image":
        """
        Flood-fill using PIL's built-in floodfill() (C-level, fast).
        Trick: fill a copy of the image with a marker colour, then diff the
        original against the copy — changed pixels are the filled region.
        """
        rgb   = self._pil.convert("RGB")
        temp  = rgb.copy()
        # Marker colour: a very specific magenta extremely unlikely to appear in photos
        marker = (254, 1, 254)
        ImageDraw.floodfill(temp, (sx, sy), marker, thresh=self._tol * 3)
        # Pixels that changed → those were filled by floodfill
        diff  = ImageChops.difference(rgb, temp).convert("L")
        mask  = diff.point([0] + [255] * 255)
        return mask

    def _build_overlay(self):
        """Composite a semi-transparent blue overlay onto the selected region."""
        blue  = PILImage.new("RGBA", self._mask.size, (79, 195, 247, 100))
        empty = PILImage.new("RGBA", self._mask.size, (0,  0,   0,   0))
        ovr   = PILImage.composite(blue, empty, self._mask)
        sw = max(1, int(self._mask.width  * self._zoom))
        sh = max(1, int(self._mask.height * self._zoom))
        self._ovr_pix = pil_to_qpixmap(ovr.resize((sw, sh), PILImage.Resampling.NEAREST))

    def paintEvent(self, event):
        """Draw the blue selection overlay and the usage hint text."""
        painter = QPainter(self)
        if self._ovr_pix:
            painter.drawPixmap(0, 0, self._ovr_pix)
        painter.setPen(QPen(QColor(79, 195, 247)))
        painter.setFont(QFont("Monospace", 9))
        painter.drawText(10, 20,
            "Magic Wand: Click = select  |  Shift+Click = add to selection  "
            "|  Enter = apply  |  ESC = cancel")
        painter.end()


# ══════════════════════════════════════════════════════════════
#  HISTOGRAMM-WIDGET: Live R/G/B Verteilung
# ══════════════════════════════════════════════════════════════

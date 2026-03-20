"""
sbs/overlays.py
Alle Overlay-Widgets für den SBS Bildeditor:
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
    Transparentes Overlay-Widget für den Zuschnitt-Modus.

    KONZEPT 'Overlay':
    Statt das Bild direkt zu verändern, wird ein unsichtbares Widget
    über den Canvas gelegt. Dieses Widget fängt alle Mausereignisse ab.
    Erst beim Loslassen der Maus wird das echte PIL-Bild zugeschnitten.
    → Nicht-destruktiv: Der Nutzer sieht eine Vorschau bevor etwas passiert.

    Modus 'rect':  Klicken + Ziehen → Rechteck
    Modus 'lasso': Freihand zeichnen → Polygon-Maske

    Signale (Signal/Slot-Prinzip):
      rect_selected  → ImageEditor._do_rect_crop()
      lasso_selected → ImageEditor._do_lasso_crop()
      cancelled      → Statusleiste "Abgebrochen"
    """
    rect_selected  = pyqtSignal(QRect)   # Rechteck-Koordinaten fertig
    lasso_selected = pyqtSignal(list)    # Lasso-Punkte fertig
    cancelled      = pyqtSignal()        # ESC gedrückt

    def __init__(self, parent, mode: str = "rect"):
        """
        Erstellt das Crop-Overlay über dem angegebenen Eltern-Widget.

        Parameter:
          parent – Eltern-Widget (ImageCanvas); das Overlay bedeckt es vollständig
          mode   – 'rect' für Rechteck-Auswahl, 'lasso' für Freihand-Polygon
        """
        super().__init__(parent)
        self.mode      = mode          # "rect" oder "lasso"
        self.start_pt  = None          # Startpunkt (Rechteck)
        self.end_pt    = None          # Endpunkt (Rechteck)
        self.lasso_pts = []            # Lasso-Punkte
        self.drawing   = False

        # Vollflächig, transparent, immer oben
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        """Escape → Abbruch; sendet cancelled-Signal und schließt das Overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    # ── Rechteck-Modus ──────────────────────────
    def mousePressEvent(self, event):
        """Linker Mausklick: Zeichnen beginnen, Start- und Endpunkt setzen."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drawing  = True
            self.start_pt = event.pos()
            self.end_pt   = event.pos()
            self.lasso_pts = [event.pos()]
            self.update()

    def mouseMoveEvent(self, event):
        """Mausbewegung: Endpunkt aktualisieren; im Lasso-Modus Punkt zur Liste hinzufügen."""
        if self.drawing:
            self.end_pt = event.pos()
            if self.mode == "lasso":
                self.lasso_pts.append(event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        """
        Maustaste losgelassen: Auswahl abschließen.
        Ist die Auswahl groß genug, wird das entsprechende Signal gesendet.
        Zu kleine Auswahl → cancelled-Signal.
        """
        if event.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.drawing = False
            if self.mode == "rect" and self.start_pt and self.end_pt:
                rect = QRect(self.start_pt, self.end_pt).normalized()
                # Mindestgröße 10×10 px damit keine versehentliche Auswahl entsteht
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
        """Zeichnet die Auswahlmarkierung auf das transparente Overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dunkle Überlagerung
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        pen = QPen(QColor(79, 195, 247), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)

        if self.mode == "rect" and self.start_pt and self.end_pt:
            sel_rect = QRect(self.start_pt, self.end_pt).normalized()
            # Ausgewählter Bereich aufhellen
            painter.fillRect(sel_rect, QColor(255, 255, 255, 30))
            painter.drawRect(sel_rect)
            # Maße anzeigen
            painter.setPen(QPen(QColor(79, 195, 247)))
            painter.setFont(QFont("Monospace", 10))
            painter.drawText(sel_rect.bottomLeft() + QPoint(4, 16),
                             f"{sel_rect.width()} × {sel_rect.height()} px")

        elif self.mode == "lasso" and len(self.lasso_pts) > 1:
            pen2 = QPen(QColor(79, 195, 247), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen2)
            for i in range(len(self.lasso_pts) - 1):
                painter.drawLine(self.lasso_pts[i], self.lasso_pts[i + 1])
            # Schlusspunkt verbinden
            if len(self.lasso_pts) > 2:
                painter.setPen(QPen(QColor(79, 195, 247, 120), 1, Qt.PenStyle.DotLine))
                painter.drawLine(self.lasso_pts[-1], self.lasso_pts[0])


# ══════════════════════════════════════════════════════════════
#  SHAPE PLACER OVERLAY: Form auf dem Bild platzieren
# ══════════════════════════════════════════════════════════════

class ShapePlacerOverlay(QWidget):
    """
    Overlay für das Text-to-Drawing Feature.
    Zeigt eine Vorschau der gewählten Form unter dem Mauszeiger.
    Klick → Form wird an dieser Position permanent auf das Bild gezeichnet.
    Escape → Abbruch.
    """
    shape_placed = pyqtSignal(str, int, int)  # shape_key, x, y (Bildkoordinaten)
    cancelled    = pyqtSignal()

    def __init__(self, parent, shape_key: str, size: int,
                 color: QColor, zoom: float):
        """
        Erstellt das Shape-Placer-Overlay.

        Parameter:
          parent    – Eltern-Widget (ImageCanvas)
          shape_key – Schlüssel der zu platzierenden Form aus SHAPE_LIBRARY
          size      – Gewünschte Formgröße in Bildpixeln
          color     – Zeichenfarbe (QColor)
          zoom      – Aktueller Zoom-Faktor des Canvas (zum Umrechnen der Koordinaten)
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
        """Escape → Abbruch; sendet cancelled-Signal und schließt das Overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()

    def mouseMoveEvent(self, event):
        """Mauszeiger verfolgen für Live-Vorschau der Form unter dem Cursor."""
        self._mouse_pos = event.pos()
        self.update()

    def mousePressEvent(self, event):
        """
        Linker Mausklick: Form platzieren.
        Overlay-Koordinaten werden durch zoom geteilt um echte Bildpixel zu erhalten.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # Overlay-Position → Bildkoordinaten durch Division mit Zoom-Faktor
            ix = int(event.pos().x() / self.zoom)
            iy = int(event.pos().y() / self.zoom)
            self.shape_placed.emit(self.shape_key, ix, iy)
            self.close()

    def paintEvent(self, event):
        """
        Zeichnet die Form-Vorschau unter dem Mauszeiger.
        Halbtransparent um das Bild darunter sichtbar zu lassen.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Hintergrund leicht abdunkeln
        painter.fillRect(self.rect(), QColor(0, 0, 0, 40))

        mx, my = self._mouse_pos.x(), self._mouse_pos.y()
        half   = self.shape_size * self.zoom // 2

        # Hilfslinien (Fadenkreuz)
        pen = QPen(QColor(79, 195, 247, 100), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(mx, 0, mx, self.height())
        painter.drawLine(0, my, self.width(), my)

        # Bounding Box der Form
        pen2 = QPen(QColor(79, 195, 247, 180), 1, Qt.PenStyle.DotLine)
        painter.setPen(pen2)
        painter.drawRect(int(mx - half), int(my - half),
                         int(self.shape_size * self.zoom),
                         int(self.shape_size * self.zoom))

        # Info-Label
        painter.setPen(QPen(QColor(79, 195, 247)))
        painter.setFont(QFont("Monospace", 10))
        painter.drawText(mx + int(half) + 8, my - 4,
                         f"{self.shape_key}  {self.shape_size}px  — Klick zum Platzieren  |  ESC = Abbruch")


# ══════════════════════════════════════════════════════════════
#  HILFSFUNKTION: Catmull-Rom Spline (für Kurven-Werkzeug)
# ══════════════════════════════════════════════════════════════

def _catmull_rom_pts(pts, n_per_seg: int = 12) -> list:
    """
    Erzeugt eine glatte Linienpunktliste durch alle Kontrollpunkte
    (Catmull-Rom Spline). Gibt eine Liste von QPoints zurück.

    Wird vom Kurven-Werkzeug genutzt:
      - n_per_seg = Anzahl interpolierter Punkte pro Segment
      - Mehr = glattere Kurve, mehr Rechenaufwand (12 reicht gut)
    """
    if len(pts) < 2:
        return list(pts)
    ext = [pts[0]] + list(pts) + [pts[-1]]  # Randpunkte verdoppeln
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
#  DRAW OVERLAY: Zeichnen direkt auf dem Bild
# ══════════════════════════════════════════════════════════════

class DrawOverlay(QWidget):
    """
    Zeichen-Overlay — implementiert Paint/GIMP-ähnliche Werkzeuge.

    ARCHITEKTUR (2-Schichten-Modell):
    ┌─────────────────────────────────────────┐
    │  Schicht 1: self._preview (QPixmap)     │  ← Temporär, nur Vorschau
    │  Freihand-Striche werden hier gerendert │    während Maus bewegt wird
    ├─────────────────────────────────────────┤
    │  Schicht 2: PIL-Image (permanent)       │  ← Echtes Bild, wird nur
    │  Erst nach mouseRelease aktualisiert    │    bei drawing_done geändert
    └─────────────────────────────────────────┘

    WERKZEUGE und ihre PIL-Implementierung:
      'pen'     → ImageDraw.line(), Breite = brush_size / zoom
      'brush'   → wie pen, aber Alpha=160 (halbtransparent) + 3× breiter
      'eraser'  → wie pen, aber Farbe=Weiß (übermalt)
      'line'    → ImageDraw.line() von Start- zu Endpunkt
      'rect'    → ImageDraw.rectangle() nur Umriss
      'ellipse' → ImageDraw.ellipse() nur Umriss
      'text'    → QInputDialog → ImageDraw.text()

    ZOOM-KORREKTUR:
    Overlay-Koordinaten sind in Bildschirmpixeln (zoomed).
    PIL braucht echte Bildpixel → Division durch zoom-Faktor.
    Beispiel: Klick bei x=200, zoom=2.0 → Bildpixel x=100

    Signal:
      drawing_done(fn) → ImageEditor._apply_draw_fn()
      fn ist eine Lambda-Funktion die PIL-Image → PIL-Image transformiert
    """
    drawing_done = pyqtSignal(object)  # PIL-Zeichenfunktion als Callable

    def __init__(self, parent, tool: str, color: QColor,
                 size: int, zoom: float, texture=None):
        """
        Erstellt das Zeichen-Overlay.

        Parameter:
          parent  – Eltern-Widget (ImageCanvas)
          tool    – Werkzeug-Name: 'pen', 'brush', 'eraser', 'line', 'rect',
                    'ellipse', 'text', 'blur', 'curve', 'texture_brush'
          color   – Aktuelle Zeichenfarbe (QColor)
          size    – Pinselgröße in Overlay-Pixeln
          zoom    – Zoom-Faktor des Canvas (für Koordinatenumrechnung)
          texture – Optional: PIL RGBA-Bild als Textur für den Textur-Pinsel
        """
        super().__init__(parent)
        self.tool        = tool
        self.color       = color
        self.brush_size  = size
        self.zoom        = zoom          # Aktueller Zoom-Faktor des Canvas
        self.texture     = texture       # PIL RGBA Textur-Bild (oder None)

        self.drawing     = False
        self.start_pt    = None
        self.last_pt     = None
        self.stroke_pts  = []            # Punkte des aktuellen Strichs
        self.bezier_pts  = []            # Kontrollpunkte für Kurven-Werkzeug

        # Temporäres QPixmap für die Vorschau während des Zeichnens
        self._preview    = QPixmap(parent.size())
        self._preview.fill(QColor(0, 0, 0, 0))

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")

        # Cursor je nach Werkzeug
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
        """Escape → Overlay schließen und Zeichenmodus beenden."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    # ── Maus-Ereignisse ─────────────────────────

    def mousePressEvent(self, event):
        """
        Maustaste gedrückt: Zeichnen beginnen.
        Für das Kurven-Werkzeug: Linksklick = Punkt hinzufügen, Rechtsklick = abschließen.
        Für alle anderen Werkzeuge: Linksklick startet den Strich.
        """
        # ── Kurven-Werkzeug: Links = Punkt hinzufügen, Rechts = Kurve abschließen
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
            # Text-Tool: sofort Dialog öffnen
            self._do_text(event.pos())
            return

        # Freihand-Werkzeuge: ersten Punkt zeichnen
        if self.tool in ("pen", "brush", "eraser", "blur", "texture_brush"):
            self._draw_point(event.pos())

    def mouseMoveEvent(self, event):
        """
        Mausbewegung verarbeiten:
        - Freihand-Werkzeuge: Strich-Segment auf den Vorschau-Layer zeichnen
        - Form-Werkzeuge (Linie, Rect, Ellipse): Nur Vorschau aktualisieren
        - Kurven-Werkzeug: Live-Vorschau der Segment-Linie zur Mausposition
        """
        if self.tool == "curve":
            # Live-Vorschau der letzten Segmentlinie (vor Klick)
            self.last_pt = event.pos()
            self.update()
            return
        if not self.drawing:
            return
        pos = event.pos()

        if self.tool in ("pen", "brush", "eraser", "blur", "texture_brush"):
            # Freihand: Linie vom letzten zum aktuellen Punkt
            self._draw_line(self.last_pt, pos)
            self.stroke_pts.append(pos)
            self.last_pt = pos
        else:
            # Form-Werkzeuge: nur Vorschau, keine permanenten Punkte
            self.last_pt = pos
            self.update()

    def mouseReleaseEvent(self, event):
        """
        Maustaste losgelassen: Strich abschließen und PIL-Zeichenfunktion erzeugen.
        Das drawing_done-Signal wird mit der Lambda-Funktion gesendet,
        die später auf das PIL-Bild angewendet wird.
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

        # Vorschau-Layer leeren
        self._preview.fill(QColor(0, 0, 0, 0))
        self.update()

    # ── Zeichnen auf den Vorschau-Layer ─────────

    def _pen_for_tool(self, tool: str, color: QColor, size: int) -> QPen:
        """Erstellt den passenden QPen für das gewählte Werkzeug."""
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
        """Zeichnet einen einzelnen Punkt auf den Vorschau-Layer (z.B. beim ersten Klick)."""
        painter = QPainter(self._preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen_for_tool(self.tool, self.color, self.brush_size))
        painter.drawPoint(pos)
        painter.end()
        self.update()

    def _draw_line(self, p1: QPoint, p2: QPoint):
        """Zeichnet ein Liniensegment zwischen zwei Punkten auf den Vorschau-Layer."""
        painter = QPainter(self._preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen_for_tool(self.tool, self.color, self.brush_size))
        painter.drawLine(p1, p2)
        painter.end()
        self.update()

    def paintEvent(self, event):
        """
        Zeichnet den Vorschau-Layer und die Form-Vorschau.
        Freihand: aus _preview, Formen: direkt hier gerendert.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Freihand-Vorschau
        painter.drawPixmap(0, 0, self._preview)

        # Form-Vorschau (Linie, Rechteck, Ellipse)
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

        # Kurven-Vorschau: Spline durch alle bisher gesetzten Punkte
        if self.tool == "curve" and self.bezier_pts:
            pen = self._pen_for_tool("pen", self.color, self.brush_size)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Punkte als kleine Kreise markieren
            dot_pen = QPen(QColor(79, 195, 247), 1)
            painter.setPen(dot_pen)
            for pt in self.bezier_pts:
                painter.drawEllipse(pt, 5, 5)
            # Spline zeichnen wenn ≥2 Punkte
            if len(self.bezier_pts) >= 2:
                spline = _catmull_rom_pts(self.bezier_pts)
                pen2 = self._pen_for_tool("pen", self.color, self.brush_size)
                painter.setPen(pen2)
                for i in range(len(spline) - 1):
                    painter.drawLine(spline[i], spline[i + 1])
            # Hilfslinie vom letzten Punkt zur Maus
            if self.last_pt:
                painter.setPen(QPen(QColor(79, 195, 247, 120), 1,
                                    Qt.PenStyle.DashLine))
                painter.drawLine(self.bezier_pts[-1], self.last_pt)
            # Anleitung anzeigen
            painter.setPen(QPen(QColor(79, 195, 247)))
            painter.setFont(QFont("Monospace", 9))
            painter.drawText(10, 20,
                f"Kurve: {len(self.bezier_pts)} Punkte — Linksklick = Punkt hinzufügen "
                f"| Rechtsklick = Abschließen | ESC = Abbrechen")

    # ── PIL-Zeichenfunktionen (permanent auf Bild) ──

    @staticmethod
    def _make_freehand_fn(pts, tool, color, size, zoom):
        """
        Gibt eine Funktion zurück die einen Freihand-Strich
        auf ein PIL-Image zeichnet.
        zoom wird genutzt um Overlay-Koordinaten in Bildpixel umzurechnen.
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
            # Punkte in Bildkoordinaten umrechnen
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
        """Gibt eine Funktion zurück die eine Linie auf PIL zeichnet."""
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
        """Gibt eine Funktion zurück die ein Rechteck auf PIL zeichnet."""
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
        """Gibt eine Funktion zurück die eine Ellipse auf PIL zeichnet."""
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
        Weichzeichnerpinsel: Gauss-Blur auf kleinen Patches entlang des Strichs.
        Für jeden Strichpunkt wird ein Bildausschnitt (2×brush_size) unscharf gezeichnet.
        """
        def draw(img):
            img = img.convert("RGBA")
            r = max(2, int(size * 2 / zoom))
            step = max(1, len(pts) // 50)  # Abtastrate für Performance
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
        Gibt eine Funktion zurück die eine glatte Catmull-Rom-Kurve
        durch alle Kontrollpunkte auf das PIL-Bild zeichnet.
        Die Kurve wird als dichte Folge von Liniensegmenten gerendert.
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
        Textur-Pinsel: Stempelt das Textur-Bild entlang des Strichs.
        Der Stempel-Abstand ist halb so groß wie die Textur-Breite,
        damit keine Lücken entstehen.
        Ist keine Textur geladen, wird nichts gezeichnet.
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
                # Transparenz-Maske aus Alpha-Kanal der Textur
                mask = tex.split()[3]
                img.paste(tex, (x, y), mask)
            return img
        return draw

    def _do_text(self, pos: QPoint):
        """
        Text-Werkzeug: Öffnet einen Eingabe-Dialog und zeichnet den eingegebenen
        Text an der angeklickten Bildposition.
        Schriftgröße wird proportional zur Pinselgröße berechnet (size × 3, mindestens 12 pt).
        """
        text, ok = QInputDialog.getText(self, "Text einfügen", "Text:")
        if ok and text:
            zoom = self.zoom
            color = self.color
            size = self.brush_size
            # Klickposition von Overlay-Koordinaten in echte Bildpixel umrechnen
            x = int(pos.x() / zoom)
            y = int(pos.y() / zoom)

            def draw(img):
                d = ImageDraw.Draw(img, "RGBA")
                font_size = max(12, int(size * 3))
                try:
                    from PIL import ImageFont
                    # System-Schriftart laden (macOS-Pfad; auf anderen Systemen Fallback auf Standardschrift)
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
                except Exception:
                    font = None   # PIL nutzt intern eine Standardschrift
                d.text((x, y), text,
                       fill=(color.red(), color.green(), color.blue(), 255),
                       font=font)
                return img

            self.drawing_done.emit(draw)
        self.close()


# ══════════════════════════════════════════════════════════════
#  TRANSFORM-OVERLAY: Ausschnitt verschieben & skalieren
# ══════════════════════════════════════════════════════════════
class TransformOverlay(QWidget):
    """
    Interaktives Overlay um eine ausgeschnittene Ebene zu verschieben
    und gleichmäßig zu skalieren.

    Bedienung:
      • Linksklick + Ziehen  → Objekt verschieben
      • Mausrad             → Skalieren (+ / -)
      • Enter               → Änderungen bestätigen
      • ESC                 → Abbrechen (Originalposition)
    """
    transform_done = pyqtSignal(int, int, float)   # x, y, scale
    cancelled      = pyqtSignal()

    def __init__(self, parent, pil_img, layer_x: int, layer_y: int, zoom: float):
        """
        Erstellt das Transform-Overlay.

        Parameter:
          parent   – Eltern-Widget (ImageCanvas)
          pil_img  – PIL RGBA-Bild der zu transformierenden Ebene
          layer_x  – Aktuelle X-Position der Ebene in Bildpixeln
          layer_y  – Aktuelle Y-Position der Ebene in Bildpixeln
          zoom     – Aktueller Zoom-Faktor des Canvas
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        self._pil       = pil_img          # Original PIL RGBA
        self._zoom      = zoom
        self._ox        = layer_x          # Startposition in Bildkoordinaten
        self._oy        = layer_y
        self._dx        = 0.0              # Verschiebung in Overlay-Pixeln
        self._dy        = 0.0
        self._scale     = 1.0
        self._drag_start: QPoint | None = None
        self._drag_orig: tuple[float, float] = (0.0, 0.0)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_pixmap()
        self.show()
        self.setFocus()

    # ── Hilfsmethoden ────────────────────────────────────────

    def _update_pixmap(self):
        """Skaliertes PIL-Bild → QPixmap für die Anzeige."""
        w = max(1, int(self._pil.width  * self._scale))
        h = max(1, int(self._pil.height * self._scale))
        scaled = self._pil.resize((w, h), PILImage.Resampling.LANCZOS)
        self._pix = pil_to_qpixmap(scaled)

    def _screen_rect(self) -> QRect:
        """Begrenzungsrahmen des Objekts in Overlay-Koordinaten."""
        # Objekt-Startpunkt in Overlay-px = (ox + dx/zoom) * zoom = ox*zoom + dx
        sx = int(self._ox * self._zoom + self._dx)
        sy = int(self._oy * self._zoom + self._dy)
        w  = int(self._pil.width  * self._scale * self._zoom)
        h  = int(self._pil.height * self._scale * self._zoom)
        return QRect(sx, sy, w, h)

    # ── Events ────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """Linker Mausklick: Verschieben beginnen; Startposition und -versatz merken."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self._drag_orig  = (self._dx, self._dy)

    def mouseMoveEvent(self, event):
        """Mausbewegung: Versatz relativ zum Startpunkt berechnen und Vorschau aktualisieren."""
        if self._drag_start is not None:
            delta = event.pos() - self._drag_start
            self._dx = self._drag_orig[0] + delta.x()
            self._dy = self._drag_orig[1] + delta.y()
            self.update()

    def mouseReleaseEvent(self, event):
        """Maustaste losgelassen: Verschieben beenden."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None

    def wheelEvent(self, event):
        """
        Mausrad: Skalierung anpassen.
        Faktor 1.1 pro Scroll-Schritt (10° = 1 Schritt).
        Bereich 0.05–20× (5% bis 2000% des Originals).
        """
        steps  = event.angleDelta().y() / 120
        factor = 1.1 ** steps
        self._scale = max(0.05, min(self._scale * factor, 20.0))
        self._update_pixmap()
        self.update()

    def keyPressEvent(self, event):
        """
        Enter → Transform bestätigen: Overlay-Verschiebung in Bildkoordinaten umrechnen
        und transform_done-Signal senden.
        Escape → Abbruch.
        """
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            # Overlay-Verschiebung → Bildkoordinaten durch Division mit Zoom-Faktor
            new_x = int(self._ox + self._dx / self._zoom)
            new_y = int(self._oy + self._dy / self._zoom)
            self.transform_done.emit(new_x, new_y, self._scale)
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        """Zeichnet das skalierte Bild mit blauem Rahmen und Hinweis-Text auf das Overlay."""
        p = QPainter(self)
        r = self._screen_rect()
        # Bild zeichnen (mit Zoom)
        rz = QRect(r.x(), r.y(),
                   int(self._pix.width()  * self._zoom),
                   int(self._pix.height() * self._zoom))
        p.drawPixmap(rz.x(), rz.y(),
                     self._pix.scaled(rz.width(), rz.height(),
                                      Qt.AspectRatioMode.IgnoreAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        # Blauen Rahmen zeichnen
        p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.PenStyle.DashLine))
        p.drawRect(rz)
        # Hinweis-Text
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 9))
        p.drawText(rz.x(), rz.y() - 6,
                   "Ziehen=Verschieben  |  Mausrad=Skalieren  |  Enter=OK  |  ESC=Abbruch")
        p.end()


# ══════════════════════════════════════════════════════════════
#  MOVABLE RECT OVERLAY: Auswahlrechteck verschieben & skalieren
# ══════════════════════════════════════════════════════════════

class MovableRectOverlay(QWidget):
    """
    Zeigt das Auswahlrechteck nach dem Zeichnen und erlaubt Verschieben
    und Skalieren (Ecken/Kanten ziehen) vor dem finalen Zuschneiden.

    Enter = Zuschneiden bestätigen  |  ESC = Abbrechen
    """
    confirmed = pyqtSignal(object)   # QRect in Overlay-Koordinaten
    cancelled = pyqtSignal()

    _HS = 10   # Handle-Größe in Pixeln

    def __init__(self, parent, rect: QRect):
        """
        Erstellt das verschiebbare Auswahlrechteck.

        Parameter:
          parent – Eltern-Widget (ImageCanvas)
          rect   – Initiales Auswahlrechteck in Overlay-Koordinaten
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
        Gibt ein Dictionary mit den 8 Skalierungs-Handles des Auswahlrechtecks zurück.
        Jeder Handle ist ein kleines QRect an der entsprechenden Ecke oder Kante.
        Schlüssel: 'tl', 'tm', 'tr', 'ml', 'mr', 'bl', 'bm', 'br'
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
        """Prüft ob ein Handle an der Mauszeigerposition liegt. Gibt Handle-Name oder None zurück."""
        for name, h in self._handles().items():
            if h.contains(pos): return name
        return None

    # ── Events ────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """
        Maustaste gedrückt: Modus bestimmen.
        Handle getroffen → skalieren; Innenbereich → verschieben; außen → nichts.
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
        Mausbewegung: Cursor anpassen (kein Drag) oder Rechteck anpassen (während Drag).
        Verschieben, Ecken und Kanten werden alle hier behandelt.
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
        """Maustaste losgelassen: Drag-Modus beenden."""
        self._mode = None

    def keyPressEvent(self, event):
        """Enter → Zuschneiden bestätigen (sendet confirmed-Signal). Escape → Abbruch."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirmed.emit(self._rect)
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        """
        Zeichnet das Overlay: Außenbereich abgedunkelt (4 Rechtecke), Auswahlrahmen
        gestrichelt, Skalierungs-Handles als blaue Quadrate, Hinweis-Leiste unten.
        """
        p = QPainter(self)
        r = self._rect
        # Aussenbereich abdunkeln (4 Rechtecke um die Auswahl)
        dim = QColor(0, 0, 0, 100)
        p.fillRect(0, 0, self.width(), r.top(),                      dim)
        p.fillRect(0, r.bottom(), self.width(), self.height(),        dim)
        p.fillRect(0, r.top(), r.left(), r.height(),                  dim)
        p.fillRect(r.right(), r.top(), self.width() - r.right(), r.height(), dim)
        # Auswahlrahmen
        p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        # Handles
        p.setBrush(QBrush(QColor("#4fc3f7")))
        p.setPen(QPen(QColor("#ffffff"), 1))
        for h in self._handles().values():
            p.drawRect(h)
        # Hinweis-Leiste
        bar_y = self.height() - 22
        p.fillRect(0, bar_y, self.width(), 22, QColor(0, 0, 0, 180))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 9))
        p.drawText(8, self.height() - 6,
                   "Ziehen=Verschieben  |  Ecken=Skalieren  |  Enter=Zuschneiden  |  ESC=Abbruch")
        p.end()


# ══════════════════════════════════════════════════════════════
#  MOVABLE LASSO OVERLAY: Lasso-Auswahl verschieben
# ══════════════════════════════════════════════════════════════

class MovableLassoOverlay(QWidget):
    """
    Nach dem Zeichnen des Lassos: Polygon per Ziehen verschieben,
    bevor der Zuschnitt angewendet wird.

    Enter = Zuschneiden  |  ESC = Abbrechen
    """
    confirmed = pyqtSignal(list)   # list of QPoint (verschoben)
    cancelled = pyqtSignal()

    def __init__(self, parent, points: list):
        """
        Erstellt das verschiebbare Lasso-Overlay.

        Parameter:
          parent – Eltern-Widget (ImageCanvas)
          points – Liste von QPoint-Objekten, die das Lasso-Polygon definieren
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
        """Erstellt einen QPainterPath aus den Lasso-Punkten unter Berücksichtigung des aktuellen Versatzes."""
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
        """Prüft ob ein Punkt innerhalb des Lasso-Polygons liegt (für Drag-Erkennung)."""
        return self._poly_path().contains(QPointF(pos.x(), pos.y()))

    def mousePressEvent(self, event):
        """Klick innerhalb des Polygons startet das Verschieben; außerhalb wird ignoriert."""
        if self._inside(event.pos()):
            self._drag_start = event.pos()
            self._drag_orig  = QPoint(self._offset)

    def mouseMoveEvent(self, event):
        """Versatz des Polygons aktualisieren (Drag) oder Cursor-Form anpassen (kein Drag)."""
        if self._drag_start is not None:
            d = event.pos() - self._drag_start
            self._offset = self._drag_orig + d
            self.update()
        else:
            # Cursor: Verschieben-Symbol innerhalb, Pfeil außerhalb
            cursor = Qt.CursorShape.SizeAllCursor if self._inside(event.pos()) \
                     else Qt.CursorShape.ArrowCursor
            self.setCursor(QCursor(cursor))

    def mouseReleaseEvent(self, _event):
        """Drag beenden."""
        self._drag_start = None

    def keyPressEvent(self, event):
        """Enter → Lasso mit aktuellem Versatz bestätigen. Escape → Abbruch."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ox, oy = self._offset.x(), self._offset.y()
            # Verschobene Punkte an confirmed-Signal übergeben
            self.confirmed.emit([QPoint(p.x()+ox, p.y()+oy) for p in self._pts])
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
        """Zeichnet das verschobene Lasso-Polygon mit gestricheltem Rahmen und Hinweis-Leiste."""
        p = QPainter(self)
        p.setPen(QPen(QColor("#4fc3f7"), 2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._poly_path())
        bar_y = self.height() - 22
        p.fillRect(0, bar_y, self.width(), 22, QColor(0, 0, 0, 180))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 9))
        p.drawText(8, self.height() - 6,
                   "Ziehen=Verschieben  |  Enter=Zuschneiden  |  ESC=Abbruch")
        p.end()


# ══════════════════════════════════════════════════════════════
#  ZAUBERSTAB-OVERLAY: Toleranzbasierte Farbauswahl
# ══════════════════════════════════════════════════════════════

class MagicWandOverlay(QWidget):
    """
    Zauberstab-Werkzeug (wie Photoshop Magic Wand).

    FUNKTIONSWEISE (Flood-Fill mit Farb-Toleranz):
    1. Nutzer klickt auf einen Pixel → Startfarbe ermitteln
    2. BFS (Breiten-Suche) von diesem Pixel ausgehend:
       Nachbarpixel werden zur Auswahl hinzugefügt wenn ihre Farbe
       innerhalb der Toleranz liegt (Summe |R-diff|+|G-diff|+|B-diff|)
    3. Ergebnis: PIL-Maske (L-Mode, 255=ausgewählt, 0=nicht ausgewählt)
    4. Shift+Klick = weiteren Bereich zur Auswahl hinzufügen

    Signale:
      selection_ready(mask) → Auswahl-Maske an ImageEditor übergeben
      cancelled             → Werkzeug abbrechen
    """
    selection_ready = pyqtSignal(object)   # PIL "L"-Bild (Maske)
    cancelled       = pyqtSignal()

    def __init__(self, parent, pil_image, tolerance: int, zoom: float):
        """
        Erstellt das Zauberstab-Overlay.

        Parameter:
          parent    – Eltern-Widget (ImageCanvas)
          pil_image – PIL-Bild, auf dem die Farb-Auswahl durchgeführt wird
          tolerance – Farb-Toleranz für den Flood-Fill (0 = exakt, 100 = sehr breit)
          zoom      – Aktueller Zoom-Faktor des Canvas (für Koordinatenumrechnung)
        """
        super().__init__(parent)
        self._pil      = pil_image.convert("RGBA")
        self._tol      = tolerance
        self._zoom     = zoom
        self._mask     = PILImage.new("L", pil_image.size, 0)
        self._ovr_pix  = None        # QPixmap der Auswahl-Überlagerung

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(False)
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        """Escape → Auswahl abbrechen. Enter → Maske bestätigen und an ImageEditor übergeben."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()
        elif event.key() == Qt.Key.Key_Return:
            self.selection_ready.emit(self._mask); self.close()

    def mousePressEvent(self, event):
        """
        Klick auf das Bild: Flood-Fill ab der angeklickten Position ausführen.
        Shift-gedrückt: bestehende Auswahl erweitern (additive Auswahl).
        Ohne Shift: neue Auswahl ersetzen die alte.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Overlay-Koordinaten in echte Bildpixel umrechnen (Zoom-Korrektur)
        ix = max(0, min(int(event.pos().x() / self._zoom), self._pil.width  - 1))
        iy = max(0, min(int(event.pos().y() / self._zoom), self._pil.height - 1))
        add = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        new_mask = self._flood_fill(ix, iy)
        if add:
            # Additive Auswahl: Vereinigung beider Masken (heller Wert gewinnt)
            self._mask = ImageChops.lighter(self._mask, new_mask)
        else:
            self._mask = new_mask
        self._build_overlay()
        self.update()

    def _flood_fill(self, sx: int, sy: int) -> "PILImage.Image":
        """
        Flood-Fill mit PIL's eigener floodfill()-Funktion (C-Ebene, schnell).
        Trick: Fülle ein Hilfsbild mit einer Marker-Farbe → Differenzbild
        ergibt die Maske aller gefüllten Pixel.
        """
        rgb   = self._pil.convert("RGB")
        temp  = rgb.copy()
        # Marker-Farbe: sehr spezifisches Magenta (in Fotos extrem selten)
        marker = (254, 1, 254)
        ImageDraw.floodfill(temp, (sx, sy), marker, thresh=self._tol * 3)
        # Wo hat sich das Bild verändert → dort wurde gefüllt
        diff  = ImageChops.difference(rgb, temp).convert("L")
        mask  = diff.point([0] + [255] * 255)
        return mask

    def _build_overlay(self):
        """Blau-transparentes Overlay über die Auswahl legen."""
        blue  = PILImage.new("RGBA", self._mask.size, (79, 195, 247, 100))
        empty = PILImage.new("RGBA", self._mask.size, (0,  0,   0,   0))
        ovr   = PILImage.composite(blue, empty, self._mask)
        sw = max(1, int(self._mask.width  * self._zoom))
        sh = max(1, int(self._mask.height * self._zoom))
        self._ovr_pix = pil_to_qpixmap(ovr.resize((sw, sh), PILImage.Resampling.NEAREST))

    def paintEvent(self, event):
        """Zeichnet das blaue Auswahl-Overlay und den Bedienungs-Hinweis-Text."""
        painter = QPainter(self)
        if self._ovr_pix:
            painter.drawPixmap(0, 0, self._ovr_pix)
        painter.setPen(QPen(QColor(79, 195, 247)))
        painter.setFont(QFont("Monospace", 9))
        painter.drawText(10, 20,
            "Zauberstab: Klick = auswählen  |  Shift+Klick = hinzufügen  "
            "|  Enter = übernehmen  |  ESC = abbrechen")
        painter.end()


# ══════════════════════════════════════════════════════════════
#  HISTOGRAMM-WIDGET: Live R/G/B Verteilung
# ══════════════════════════════════════════════════════════════

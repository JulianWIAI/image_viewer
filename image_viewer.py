"""
==============================================================
 SBS Bildeditor v4 – Bachelor Professional
 Autor   : [Dein Name]
 Datum   : März 2026
 Neu v4  : Text-to-Drawing Feature – 8 vorgezeichnete Formen
           (Haus, Sonne, Stern, Herz, Blume, Auto, Baum, Pfeil)
           werden per Dropdown + Klick auf dem Bild platziert.
           Skalierbar, in Zeichenfarbe gefärbt.
==============================================================
"""

import sys, base64, json, urllib.request, io, math
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QFileDialog, QScrollArea, QStatusBar, QSizePolicy, QToolBar,
    QFrame, QVBoxLayout, QHBoxLayout, QSlider, QGroupBox,
    QDockWidget, QMessageBox, QDialog, QDialogButtonBox,
    QSpinBox, QComboBox, QTextEdit, QSizePolicy,
    QColorDialog, QInputDialog
)
from PyQt6.QtGui import (
    QPixmap, QIcon, QAction, QKeySequence, QFont, QColor,
    QPalette, QImage, QPainter, QPen, QBrush, QPolygon,
    QCursor
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QRect, QPoint

try:
    from PIL import Image as PILImage, ImageFilter, ImageEnhance, ImageOps, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ══════════════════════════════════════════════════════════════
#  KONVERTIERUNGSFUNKTIONEN
# ══════════════════════════════════════════════════════════════

def pil_to_qpixmap(img: "PILImage.Image") -> QPixmap:
    """Konvertiert PIL-Image → QPixmap für die Anzeige."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qi = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qi)


# ══════════════════════════════════════════════════════════════
#  FORM-BIBLIOTHEK: Text-to-Drawing
#  Jede Form ist als normalisiertes Polygon (0.0–1.0) definiert.
#  Beim Zeichnen werden die Punkte auf die gewünschte Größe
#  skaliert und an der Klickposition positioniert.
# ══════════════════════════════════════════════════════════════

def _scale_pts(pts, cx, cy, size):
    """
    Hilfsfunktion: Normalisierte Punkte (0–1) auf echte Pixelkoordinaten
    skalieren. cx/cy = Mittelpunkt, size = Breite/Höhe in Pixeln.
    """
    half = size / 2
    return [(int(cx + (x - 0.5) * size),
             int(cy + (y - 0.5) * size)) for x, y in pts]


# Jede Form ist ein Dict mit:
#   'label'  : Anzeigename im Dropdown
#   'emoji'  : Emoji für Button
#   'type'   : 'polygon', 'lines' oder 'compound'
#   'parts'  : Liste von (type, points)-Tupeln für Compound-Formen
SHAPE_LIBRARY = {

    "🏠 Haus": {
        "label": "🏠 Haus",
        "parts": [
            # Grundriss (Rechteck)
            ("polygon", [(0.1, 0.5), (0.9, 0.5), (0.9, 0.95), (0.1, 0.95)]),
            # Dach (Dreieck)
            ("polygon", [(0.0, 0.52), (0.5, 0.05), (1.0, 0.52)]),
            # Tür
            ("polygon", [(0.38, 0.95), (0.62, 0.95), (0.62, 0.68), (0.38, 0.68)]),
            # Fenster links
            ("polygon", [(0.15, 0.6), (0.32, 0.6), (0.32, 0.75), (0.15, 0.75)]),
            # Fenster rechts
            ("polygon", [(0.68, 0.6), (0.85, 0.6), (0.85, 0.75), (0.68, 0.75)]),
        ]
    },

    "☀️ Sonne": {
        "label": "☀️ Sonne",
        "parts": [
            # Kreis (als 32-Eck approximiert)
            ("circle", (0.5, 0.5, 0.28)),   # cx, cy, radius (normalisiert)
            # 8 Strahlen
            ("line", [(0.50, 0.08), (0.50, 0.18)]),
            ("line", [(0.50, 0.82), (0.50, 0.92)]),
            ("line", [(0.08, 0.50), (0.18, 0.50)]),
            ("line", [(0.82, 0.50), (0.92, 0.50)]),
            ("line", [(0.18, 0.18), (0.25, 0.25)]),
            ("line", [(0.75, 0.75), (0.82, 0.82)]),
            ("line", [(0.82, 0.18), (0.75, 0.25)]),
            ("line", [(0.18, 0.82), (0.25, 0.75)]),
        ]
    },

    "⭐ Stern": {
        "label": "⭐ Stern",
        "parts": [
            # 5-zackiger Stern (äußere und innere Punkte abwechselnd)
            ("polygon", [
                (0.500, 0.050),  # oben
                (0.594, 0.345),
                (0.905, 0.345),  # rechts oben
                (0.655, 0.527),
                (0.755, 0.820),  # rechts unten
                (0.500, 0.645),
                (0.245, 0.820),  # links unten
                (0.345, 0.527),
                (0.095, 0.345),  # links oben
                (0.406, 0.345),
            ]),
        ]
    },

    "❤️ Herz": {
        "label": "❤️ Herz",
        "parts": [
            # Herz als Bézierkurve-Annäherung (36 Punkte)
            ("polygon", [
                (0.500, 0.850),
                (0.150, 0.520),
                (0.050, 0.380),
                (0.050, 0.260),
                (0.120, 0.160),
                (0.250, 0.130),
                (0.350, 0.160),
                (0.430, 0.230),
                (0.500, 0.320),
                (0.570, 0.230),
                (0.650, 0.160),
                (0.750, 0.130),
                (0.880, 0.160),
                (0.950, 0.260),
                (0.950, 0.380),
                (0.850, 0.520),
            ]),
        ]
    },

    "🌸 Blume": {
        "label": "🌸 Blume",
        "parts": [
            # Mittelkreis
            ("circle", (0.5, 0.5, 0.12)),
            # 6 Blütenblätter als Ellipsen (cx, cy, rx, ry, angle)
            ("petal", (0.50, 0.25, 0.10, 0.18, 0)),
            ("petal", (0.50, 0.75, 0.10, 0.18, 0)),
            ("petal", (0.25, 0.50, 0.18, 0.10, 0)),
            ("petal", (0.75, 0.50, 0.18, 0.10, 0)),
            ("petal", (0.29, 0.29, 0.10, 0.18, 45)),
            ("petal", (0.71, 0.71, 0.10, 0.18, 45)),
            ("petal", (0.71, 0.29, 0.10, 0.18, -45)),
            ("petal", (0.29, 0.71, 0.10, 0.18, -45)),
            # Stiel
            ("line", [(0.50, 0.88), (0.50, 1.0)]),
            ("line", [(0.50, 0.95), (0.35, 0.82)]),   # Blatt links
            ("line", [(0.50, 0.92), (0.65, 0.79)]),   # Blatt rechts
        ]
    },

    "🚗 Auto": {
        "label": "🚗 Auto",
        "parts": [
            # Karosserie unten (Rechteck)
            ("polygon", [(0.05, 0.55), (0.95, 0.55), (0.95, 0.80), (0.05, 0.80)]),
            # Dach (abgerundetes Trapez)
            ("polygon", [(0.20, 0.55), (0.80, 0.55), (0.70, 0.30), (0.30, 0.30)]),
            # Windschutzscheibe
            ("polygon", [(0.32, 0.53), (0.50, 0.53), (0.50, 0.33), (0.35, 0.33)]),
            # Heckscheibe
            ("polygon", [(0.52, 0.53), (0.68, 0.53), (0.65, 0.33), (0.52, 0.33)]),
            # Rad links (Kreis)
            ("circle", (0.22, 0.82, 0.11)),
            # Rad rechts
            ("circle", (0.78, 0.82, 0.11)),
            # Felge links
            ("circle", (0.22, 0.82, 0.05)),
            # Felge rechts
            ("circle", (0.78, 0.82, 0.05)),
        ]
    },

    "🌲 Baum": {
        "label": "🌲 Baum",
        "parts": [
            # Stamm
            ("polygon", [(0.42, 0.75), (0.58, 0.75), (0.58, 0.95), (0.42, 0.95)]),
            # Unteres Dreieck (groß)
            ("polygon", [(0.10, 0.75), (0.90, 0.75), (0.50, 0.45)]),
            # Mittleres Dreieck
            ("polygon", [(0.18, 0.52), (0.82, 0.52), (0.50, 0.25)]),
            # Oberes Dreieck (klein)
            ("polygon", [(0.26, 0.32), (0.74, 0.32), (0.50, 0.08)]),
        ]
    },

    "➡️ Pfeil": {
        "label": "➡️ Pfeil",
        "parts": [
            # Pfeilschaft
            ("polygon", [(0.05, 0.38), (0.60, 0.38), (0.60, 0.62), (0.05, 0.62)]),
            # Pfeilspitze
            ("polygon", [(0.60, 0.18), (0.95, 0.50), (0.60, 0.82)]),
        ]
    },
}


def draw_shape_on_pil(img: "PILImage.Image", shape_key: str,
                      cx: int, cy: int, size: int,
                      color: tuple, line_width: int = 2) -> "PILImage.Image":
    """
    Zeichnet eine Form aus der SHAPE_LIBRARY auf ein PIL-Image.

    Parameter:
      img        – Ziel-PIL-Image (wird verändert zurückgegeben)
      shape_key  – Schlüssel in SHAPE_LIBRARY (z.B. '🏠 Haus')
      cx, cy     – Mittelpunkt der Form in Bildpixeln
      size       – Breite/Höhe der Form in Pixeln
      color      – RGBA-Tuple (r, g, b, a)
      line_width – Strichbreite
    """
    shape = SHAPE_LIBRARY.get(shape_key)
    if not shape:
        return img

    d = ImageDraw.Draw(img, "RGBA")
    fill_color = (color[0], color[1], color[2], 80)   # Halbtr. Füllung
    outline    = (color[0], color[1], color[2], 255)  # Voller Umriss

    for part_type, data in shape["parts"]:

        if part_type == "polygon":
            # Normalisierte Punkte → Bildpixel
            pts = _scale_pts(data, cx, cy, size)
            d.polygon(pts, outline=outline, fill=fill_color)

        elif part_type == "line":
            pts = _scale_pts(data, cx, cy, size)
            d.line(pts, fill=outline, width=max(1, line_width))

        elif part_type == "circle":
            # data = (norm_cx, norm_cy, norm_radius)
            ncx, ncy, nr = data
            px = int(cx + (ncx - 0.5) * size)
            py = int(cy + (ncy - 0.5) * size)
            pr = int(nr * size)
            d.ellipse([px - pr, py - pr, px + pr, py + pr],
                      outline=outline, fill=fill_color)

        elif part_type == "petal":
            # data = (norm_cx, norm_cy, norm_rx, norm_ry, angle_deg)
            ncx, ncy, nrx, nry, angle = data
            px  = int(cx + (ncx - 0.5) * size)
            py  = int(cy + (ncy - 0.5) * size)
            prx = int(nrx * size)
            pry = int(nry * size)
            d.ellipse([px - prx, py - pry, px + prx, py + pry],
                      outline=outline, fill=fill_color)

    return img


# ══════════════════════════════════════════════════════════════
#  KI-WORKER (Moondream via Ollama)
# ══════════════════════════════════════════════════════════════

class AIWorker(QThread):
    """
    Hintergrund-Thread für die Moondream KI-Analyse.
    Sendet das aktuelle Bild an Ollama und gibt die Beschreibung zurück.
    Voraussetzung: ollama pull moondream
    """
    result_ready   = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, pil_image: "PILImage.Image"):
        super().__init__()
        self.pil_image = pil_image.copy()

    def run(self):
        try:
            img = self.pil_image.copy()
            img.thumbnail((512, 512), PILImage.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

            payload = {
                "model": "moondream",
                "prompt": "Describe this image in detail. What objects, people, or scenes are visible? Answer in 2-3 sentences.",
                "images": [b64],
                "stream": False,
                "options": {"num_predict": 120, "temperature": 0.1}
            }
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
                desc = data.get("response", "Keine Antwort.").strip()
                print(f"[Moondream]: {desc}")
                self.result_ready.emit(desc)
        except ConnectionRefusedError:
            self.error_occurred.emit("Ollama läuft nicht. Bitte Terminal öffnen und 'ollama serve' ausführen.")
        except Exception as e:
            self.error_occurred.emit(f"KI-Fehler: {e}")


# ══════════════════════════════════════════════════════════════
#  CROP OVERLAY: Zeichnet Rechteck/Lasso auf dem Bild
# ══════════════════════════════════════════════════════════════

class CropOverlay(QWidget):
    """
    Transparentes Overlay über dem Bildbetrachter.
    Modus 'rect':  Rechteck-Zuschnitt durch Klicken und Ziehen.
    Modus 'lasso': Freihand-Zuschnitt durch Zeichnen mit der Maus.
    Gibt die gewählte Region als Signal zurück.
    """
    rect_selected  = pyqtSignal(QRect)     # Rechteck fertig
    lasso_selected = pyqtSignal(list)      # Lasso-Punkte fertig (Liste von QPoint)
    cancelled      = pyqtSignal()          # Abbruch via Escape

    def __init__(self, parent, mode: str = "rect"):
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
        """Escape → Abbruch."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    # ── Rechteck-Modus ──────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drawing  = True
            self.start_pt = event.pos()
            self.end_pt   = event.pos()
            self.lasso_pts = [event.pos()]
            self.update()

    def mouseMoveEvent(self, event):
        if self.drawing:
            self.end_pt = event.pos()
            if self.mode == "lasso":
                self.lasso_pts.append(event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.drawing = False
            if self.mode == "rect" and self.start_pt and self.end_pt:
                rect = QRect(self.start_pt, self.end_pt).normalized()
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
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()

    def mouseMoveEvent(self, event):
        """Mauszeiger verfolgen für Live-Vorschau."""
        self._mouse_pos = event.pos()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Overlay-Position → Bildkoordinaten
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
#  DRAW OVERLAY: Zeichnen direkt auf dem Bild
# ══════════════════════════════════════════════════════════════

class DrawOverlay(QWidget):
    """
    Transparentes Overlay über dem Canvas für alle Zeichen-Werkzeuge.

    Werkzeuge:
      'pen'      – Freihand-Stift (dünne Linie)
      'brush'    – Freihand-Pinsel (dicke, weiche Linie)
      'eraser'   – Radierer (zeichnet mit Hintergrundfarbe)
      'line'     – Gerade Linie (Vorschau beim Ziehen)
      'rect'     – Rechteck (Vorschau beim Ziehen)
      'ellipse'  – Ellipse / Kreis (Vorschau beim Ziehen)
      'text'     – Text einfügen (Klick = Position)

    Zeichenergebnis wird als Signal an den Editor gegeben,
    der es auf das PIL-Bild überträgt (permanent).
    """

    # Signal: Fertige Zeichnung als QPixmap-Overlay übergeben
    drawing_done   = pyqtSignal(object)   # PIL-Zeichenfunktion
    stroke_done    = pyqtSignal()         # Pinselstrich abgeschlossen

    def __init__(self, parent, tool: str, color: QColor,
                 size: int, zoom: float):
        super().__init__(parent)
        self.tool       = tool
        self.color      = color
        self.brush_size = size
        self.zoom       = zoom          # Aktueller Zoom-Faktor des Canvas

        self.drawing    = False
        self.start_pt   = None
        self.last_pt    = None
        self.stroke_pts = []            # Punkte des aktuellen Strichs

        # Temporäres QPixmap für die Vorschau während des Zeichnens
        self._preview   = QPixmap(parent.size())
        self._preview.fill(QColor(0, 0, 0, 0))

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")

        # Cursor je nach Werkzeug
        cursors = {
            "pen":     Qt.CursorShape.CrossCursor,
            "brush":   Qt.CursorShape.CrossCursor,
            "eraser":  Qt.CursorShape.CrossCursor,
            "line":    Qt.CursorShape.CrossCursor,
            "rect":    Qt.CursorShape.CrossCursor,
            "ellipse": Qt.CursorShape.CrossCursor,
            "text":    Qt.CursorShape.IBeamCursor,
        }
        self.setCursor(QCursor(cursors.get(tool, Qt.CursorShape.CrossCursor)))
        self.show()
        self.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    # ── Maus-Ereignisse ─────────────────────────

    def mousePressEvent(self, event):
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
        if self.tool in ("pen", "brush", "eraser"):
            self._draw_point(event.pos())

    def mouseMoveEvent(self, event):
        if not self.drawing:
            return
        pos = event.pos()

        if self.tool in ("pen", "brush", "eraser"):
            # Freihand: Linie vom letzten zum aktuellen Punkt
            self._draw_line(self.last_pt, pos)
            self.stroke_pts.append(pos)
            self.last_pt = pos
        else:
            # Form-Werkzeuge: nur Vorschau, keine permanenten Punkte
            self.last_pt = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.drawing:
            return
        self.drawing = False

        if self.tool in ("pen", "brush", "eraser"):
            # Freihand-Strich abschließen
            self.drawing_done.emit(self._make_freehand_fn(
                list(self.stroke_pts), self.tool,
                self.color, self.brush_size, self.zoom
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
        else:
            pen = QPen(color, size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        return pen

    def _draw_point(self, pos: QPoint):
        painter = QPainter(self._preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen_for_tool(self.tool, self.color, self.brush_size))
        painter.drawPoint(pos)
        painter.end()
        self.update()

    def _draw_line(self, p1: QPoint, p2: QPoint):
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

    def _do_text(self, pos: QPoint):
        """Text-Tool: Dialog öffnen, Text auf Bild zeichnen."""
        text, ok = QInputDialog.getText(self, "Text einfügen", "Text:")
        if ok and text:
            zoom = self.zoom
            color = self.color
            size = self.brush_size
            x = int(pos.x() / zoom)
            y = int(pos.y() / zoom)

            def draw(img):
                d = ImageDraw.Draw(img, "RGBA")
                font_size = max(12, int(size * 3))
                try:
                    from PIL import ImageFont
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
                except Exception:
                    font = None
                d.text((x, y), text,
                       fill=(color.red(), color.green(), color.blue(), 255),
                       font=font)
                return img

            self.drawing_done.emit(draw)
        self.close()


# ══════════════════════════════════════════════════════════════
#  BILDFLÄCHE mit Zoom und Overlay-Unterstützung
# ══════════════════════════════════════════════════════════════

class ImageCanvas(QLabel):
    """
    Bildanzeige-Widget.
    Trägt das Bild, verwaltet Zoom und hostet das CropOverlay.
    """

    def __init__(self):
        super().__init__()
        self._pix_orig   = None    # QPixmap des aktuellen Bildes
        self._zoom       = 1.0
        self._overlay    = None    # Aktives CropOverlay

        self.grabGesture(Qt.GestureType.PinchGesture)
        self.setText("Kein Bild geladen\n\nDatei → Öffnen  oder  Strg+O")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("color: #555; font-size: 15px;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, pix: QPixmap):
        self._pix_orig = pix
        self._zoom     = 1.0
        self._render()

    def update_image(self, pix: QPixmap):
        self._pix_orig = pix
        self._render()

    def set_zoom(self, z: float):
        self._zoom = max(0.05, min(z, 20.0))
        self._render()

    def get_zoom(self) -> float:
        return self._zoom

    def _render(self):
        if not self._pix_orig:
            return
        w = int(self._pix_orig.width()  * self._zoom)
        h = int(self._pix_orig.height() * self._zoom)
        scaled = self._pix_orig.scaled(w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
        self.resize(scaled.width(), scaled.height())
        self.setStyleSheet("")
        # Overlay aktualisieren falls aktiv
        if self._overlay:
            self._overlay.setGeometry(self.rect())

    def start_crop_overlay(self, mode: str) -> CropOverlay:
        """Neues Crop-Overlay starten."""
        if self._overlay:
            self._overlay.close()
        self._overlay = CropOverlay(self, mode)
        self._overlay.cancelled.connect(self._on_overlay_done)
        return self._overlay

    def start_draw_overlay(self, tool: str, color: QColor,
                           size: int) -> DrawOverlay:
        """
        Neues Zeichen-Overlay starten.
        Gibt das Overlay zurück damit der Editor Signale verbinden kann.
        """
        if self._overlay:
            self._overlay.close()
        self._overlay = DrawOverlay(self, tool, color, size, self._zoom)
        self._overlay.setGeometry(self.rect())
        return self._overlay

    def start_shape_placer(self, shape_key: str, size: int,
                           color: QColor) -> ShapePlacerOverlay:
        """
        Shape-Placer-Overlay starten.
        Nutzer sieht eine Live-Vorschau und platziert die Form per Klick.
        """
        if self._overlay:
            self._overlay.close()
        self._overlay = ShapePlacerOverlay(self, shape_key, size, color, self._zoom)
        self._overlay.setGeometry(self.rect())
        return self._overlay

    def _on_overlay_done(self):
        self._overlay = None

    def event(self, event):
        if event.type() == event.Type.Gesture:
            pinch = event.gesture(Qt.GestureType.PinchGesture)
            if pinch:
                self.set_zoom(self._zoom * pinch.scaleFactor())
                p = self.parent()
                if p and hasattr(p.parent(), "_update_status"):
                    p.parent()._update_status()
            return True
        return super().event(event)


# ══════════════════════════════════════════════════════════════
#  SLIDER-WIDGET
# ══════════════════════════════════════════════════════════════

class LabeledSlider(QWidget):
    """Wiederverwendbarer Slider mit Label und Wertanzeige."""
    value_changed = pyqtSignal(int)

    def __init__(self, label: str, mn: int, mx: int, default: int):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #bbb; font-size: 11px;")
        self.val_lbl = QLabel(str(default))
        self.val_lbl.setStyleSheet("color: #4fc3f7; font-size: 11px; font-weight: bold;")
        self.val_lbl.setFixedWidth(32)
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(lbl); row.addStretch(); row.addWidget(self.val_lbl)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(mn, mx); self.slider.setValue(default)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal { height:4px; background:#2d2d2d; border-radius:2px; }
            QSlider::handle:horizontal { width:13px; height:13px; margin:-5px 0;
                background:#4fc3f7; border-radius:7px; }
            QSlider::sub-page:horizontal { background:#4fc3f7; border-radius:2px; }
        """)
        self.slider.valueChanged.connect(lambda v: (self.val_lbl.setText(str(v)), self.value_changed.emit(v)))
        layout.addWidget(self.slider)

    def value(self): return self.slider.value()

    def reset(self, val=100):
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.val_lbl.setText(str(val))
        self.slider.blockSignals(False)


# ══════════════════════════════════════════════════════════════
#  HAUPTFENSTER
# ══════════════════════════════════════════════════════════════

class ImageEditor(QMainWindow):
    """
    SBS Bildeditor v2 – Hauptfenster.
    Photoshop-ähnliches Layout: Toolbar oben, Canvas Mitte,
    Einstellungspanel rechts.
    """
    ZOOM_STEP = 0.15

    def __init__(self):
        super().__init__()
        self.current_file = None
        self.original_pil = None   # Unverändertes Original (für Reset)
        self.current_pil  = None   # Arbeitskopie
        self.history      = []     # Undo-Stack (max. 20)
        self.ai_worker    = None

        # Zeichen-Zustand
        self.draw_tool    = "pen"           # Aktives Zeichen-Werkzeug
        self.draw_color   = QColor(255, 0, 0)  # Aktuelle Zeichenfarbe (Rot)
        self.draw_size    = 4               # Pinselgröße in Pixeln

        self._setup_window()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_central()
        self._setup_panel()
        self._setup_statusbar()
        self.grabGesture(Qt.GestureType.PinchGesture)

    # ── Fenster ─────────────────────────────────
    def _setup_window(self):
        self.setWindowTitle("SBS Bildeditor v3")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)
        self.setWindowIcon(QIcon("app_icon.png"))
        QApplication.instance().setWindowIcon(QIcon("app_icon.png"))

    # ── Menüleiste ──────────────────────────────
    def _setup_menu(self):
        mb = self.menuBar()
        mb.setStyleSheet("background:#1a1a1a; color:#ddd; font-size:13px;")

        def a(menu, txt, sc, fn):
            act = QAction(txt, self)
            if sc: act.setShortcut(QKeySequence(sc))
            act.triggered.connect(fn); menu.addAction(act)

        fm = mb.addMenu("Datei")
        a(fm, "📂 Öffnen",           "Ctrl+O", self.open_file)
        a(fm, "💾 Speichern",        "Ctrl+S", self.save_file)
        a(fm, "💾 Speichern unter",  "Ctrl+Shift+S", self.save_file_as)
        fm.addSeparator()
        a(fm, "✖ Beenden",           "Ctrl+Q", self.close)

        em = mb.addMenu("Bearbeiten")
        a(em, "↩ Rückgängig",        "Ctrl+Z", self.undo)
        a(em, "🔄 Original",          "",       self.reset_to_original)

        im = mb.addMenu("Bild")
        a(im, "↻ 90° rechts",        "",  self.rotate_cw)
        a(im, "↺ 90° links",         "",  self.rotate_ccw)
        a(im, "↔ H-Spiegeln",        "",  self.flip_horizontal)
        a(im, "↕ V-Spiegeln",        "",  self.flip_vertical)
        im.addSeparator()
        a(im, "✂ Rechteck-Crop",     "Ctrl+Shift+R", self.start_rect_crop)
        a(im, "🔮 Lasso-Crop",        "Ctrl+Shift+L", self.start_lasso_crop)

        dm = mb.addMenu("Zeichnen")
        a(dm, "✏️ Stift",             "Ctrl+Shift+P", lambda: self.set_draw_tool("pen"))
        a(dm, "🖌 Pinsel",            "Ctrl+Shift+B", lambda: self.set_draw_tool("brush"))
        a(dm, "⬜ Radierer",          "Ctrl+Shift+E", lambda: self.set_draw_tool("eraser"))
        a(dm, "╱ Linie",             "",             lambda: self.set_draw_tool("line"))
        a(dm, "▭ Rechteck",          "",             lambda: self.set_draw_tool("rect"))
        a(dm, "◯ Ellipse",           "",             lambda: self.set_draw_tool("ellipse"))
        a(dm, "T Text einfügen",     "",             lambda: self.set_draw_tool("text"))
        dm.addSeparator()
        a(dm, "🎨 Farbe wählen…",    "",             self.pick_color)

    # ── Toolbar ─────────────────────────────────
    def _setup_toolbar(self):
        tb = QToolBar(); tb.setMovable(False)
        tb.setStyleSheet("""
            QToolBar { background:#232323; border-bottom:1px solid #111; padding:3px 8px; spacing:3px; }
            QToolButton { color:#ddd; font-size:12px; padding:5px 9px;
                border-radius:4px; border:none; }
            QToolButton:hover   { background:#3a3a3a; }
            QToolButton:pressed { background:#1a1a1a; color:#4fc3f7; }
        """)
        self.addToolBar(tb)

        def add(lbl, tip, sc, fn):
            a = QAction(lbl, self); a.setToolTip(tip)
            if sc: a.setShortcut(QKeySequence(sc))
            a.triggered.connect(fn); tb.addAction(a)

        add("📂 Öffnen",    "Datei öffnen",      "Ctrl+O",       self.open_file)
        add("💾 Speichern", "Speichern",          "Ctrl+S",       self.save_file)
        add("↩ Undo",       "Rückgängig",         "Ctrl+Z",       self.undo)
        tb.addSeparator()
        add("🔍+ Zoom+",    "Reinzoomen",         "Ctrl++",       self.zoom_in)
        add("🔍− Zoom−",    "Rauszoomen",         "Ctrl+-",       self.zoom_out)
        add("⊡ 1:1",        "Originalgröße",      "Ctrl+0",       self.zoom_reset)
        add("⛶ Fit",        "Anpassen",           "Ctrl+F",       self.zoom_fit)
        tb.addSeparator()
        add("↻ 90°",        "Rechts drehen",      "",             self.rotate_cw)
        add("↺ 90°",        "Links drehen",       "",             self.rotate_ccw)
        add("↔ Spiegeln",   "Horizontal",         "",             self.flip_horizontal)
        tb.addSeparator()
        add("✂ Rect-Crop",  "Rechteck-Zuschnitt", "Ctrl+Shift+R", self.start_rect_crop)
        add("🔮 Lasso",      "Freihand-Zuschnitt", "Ctrl+Shift+L", self.start_lasso_crop)
        tb.addSeparator()
        add("⬛ S/W",       "Schwarz/Weiß",       "",             self.apply_grayscale)
        add("🌅 Sepia",     "Sepia",              "",             self.apply_sepia)
        add("🔄 Reset",     "Zurücksetzen",       "Ctrl+R",       self.reset_to_original)
        tb.addSeparator()
        # Zeichen-Werkzeuge
        add("✏️ Stift",     "Stift (Freihand)",   "Ctrl+Shift+P", lambda: self.set_draw_tool("pen"))
        add("🖌 Pinsel",    "Pinsel (weich)",     "Ctrl+Shift+B", lambda: self.set_draw_tool("brush"))
        add("⬜ Radierer",  "Radierer",           "Ctrl+Shift+E", lambda: self.set_draw_tool("eraser"))
        add("╱ Linie",      "Gerade Linie",       "",             lambda: self.set_draw_tool("line"))
        add("▭ Rect",       "Rechteck zeichnen",  "",             lambda: self.set_draw_tool("rect"))
        add("◯ Ellipse",    "Ellipse / Kreis",    "",             lambda: self.set_draw_tool("ellipse"))
        add("T Text",       "Text einfügen",      "",             lambda: self.set_draw_tool("text"))
        add("🎨 Farbe",     "Farbe wählen",       "",             self.pick_color)
        tb.addSeparator()
        add("🤖 KI",        "KI-Analyse",         "Ctrl+A",       self.run_ai_analysis)
        tb.addSeparator()
        add("✨ Form",       "Form platzieren",    "Ctrl+Shift+F", self.start_shape_placer)

    # ── Canvas (Mitte) ───────────────────────────
    def _setup_central(self):
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("""
            QScrollArea { background:#111; border:none; }
            QScrollBar:vertical, QScrollBar:horizontal {
                background:#1a1a1a; width:8px; height:8px; }
            QScrollBar::handle { background:#3a3a3a; border-radius:4px; }
        """)
        self.canvas = ImageCanvas()
        self.scroll.setWidget(self.canvas)
        self.setCentralWidget(self.scroll)

    # ── Einstellungspanel (rechts) ───────────────
    def _setup_panel(self):
        """
        Rechtes Dock-Panel mit Korrekturen, Filtern und KI.
        Scrollbar ermöglicht Platz für alle Bereiche.
        """
        dock = QDockWidget("Einstellungen", self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setStyleSheet("""
            QDockWidget { color:#ddd; }
            QDockWidget::title { background:#1a1a1a; padding:8px 12px;
                font-weight:bold; font-size:12px; letter-spacing:1px; }
        """)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background:#1e1e1e; border:none;")

        panel = QWidget(); panel.setStyleSheet("background:#1e1e1e;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Crop-Werkzeuge
        grp_crop = self._grp("✂  ZUSCHNEIDEN")
        cl = QVBoxLayout(grp_crop); cl.setSpacing(4)
        btn_s = """
            QPushButton { background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a;
                border-radius:4px; padding:7px; font-size:12px; text-align:left; }
            QPushButton:hover { background:#3a3a3a; border-color:#4fc3f7; color:#fff; }
        """
        b_rect = QPushButton("✂  Rechteck-Crop  (Strg+Shift+R)")
        b_rect.setStyleSheet(btn_s); b_rect.clicked.connect(self.start_rect_crop)
        b_lasso = QPushButton("🔮  Lasso-Crop  (Strg+Shift+L)")
        b_lasso.setStyleSheet(btn_s); b_lasso.clicked.connect(self.start_lasso_crop)
        hint = QLabel("Tipp: Nach Klick auf Crop-Knopf\ndas gewünschte Gebiet\nauf dem Bild markieren.")
        hint.setStyleSheet("color:#666; font-size:10px;")
        cl.addWidget(b_rect); cl.addWidget(b_lasso); cl.addWidget(hint)
        layout.addWidget(grp_crop)

        # ── Zeichen-Werkzeuge
        grp_draw = self._grp("✏️  ZEICHNEN")
        dl = QVBoxLayout(grp_draw); dl.setSpacing(5)

        # Werkzeug-Auswahl (2×4 Grid)
        draw_tools = [
            ("✏️ Stift",    "pen"),     ("🖌 Pinsel",   "brush"),
            ("⬜ Radierer", "eraser"),  ("╱ Linie",    "line"),
            ("▭ Rechteck", "rect"),    ("◯ Ellipse",  "ellipse"),
            ("T Text",     "text"),    ("🎨 Farbe…",  "__color__"),
        ]
        # Stil für aktiven/inaktiven Button
        self._draw_btns = {}
        grid_rows = [draw_tools[i:i+2] for i in range(0, len(draw_tools), 2)]
        for row_items in grid_rows:
            row = QHBoxLayout()
            for label, key in row_items:
                btn = QPushButton(label)
                btn.setCheckable(True)
                btn.setStyleSheet("""
                    QPushButton { background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a;
                        border-radius:4px; padding:6px 4px; font-size:11px; }
                    QPushButton:hover   { background:#3a3a3a; border-color:#4fc3f7; }
                    QPushButton:checked { background:#0e639c; color:#fff; border-color:#4fc3f7; }
                """)
                if key == "__color__":
                    btn.setCheckable(False)
                    btn.clicked.connect(self.pick_color)
                else:
                    btn.clicked.connect(lambda _, k=key: self.set_draw_tool(k))
                    self._draw_btns[key] = btn
                row.addWidget(btn)
            dl.addLayout(row)

        # Farbvorschau
        color_row = QHBoxLayout()
        color_lbl = QLabel("Farbe:")
        color_lbl.setStyleSheet("color:#bbb; font-size:11px;")
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(40, 20)
        self.color_preview.setStyleSheet(
            f"background:{self.draw_color.name()}; border:1px solid #555; border-radius:3px;")
        color_row.addWidget(color_lbl)
        color_row.addWidget(self.color_preview)
        color_row.addStretch()
        dl.addLayout(color_row)

        # Pinselgröße
        self.sl_brush_size = LabeledSlider("Pinselgröße", 1, 50, self.draw_size)

        def on_size_change(v):
            self.draw_size = v
            # Overlay sofort mit neuer Größe neu starten
            if self.btn_draw_start.isChecked() and self.current_pil:
                if self.canvas._overlay:
                    self.canvas._overlay.blockSignals(True)
                    self.canvas._overlay.close()
                    self.canvas._overlay = None
                self._start_draw_overlay()

        self.sl_brush_size.value_changed.connect(on_size_change)
        dl.addWidget(self.sl_brush_size)

        # Zeichnen starten/stoppen Button
        self.btn_draw_start = QPushButton("▶  Zeichnen aktivieren")
        self.btn_draw_start.setCheckable(True)
        self.btn_draw_start.setStyleSheet("""
            QPushButton { background:#1a3a1a; color:#90ee90; border:1px solid #2a5a2a;
                border-radius:4px; padding:7px; font-size:12px; }
            QPushButton:checked { background:#0e639c; color:#fff; border-color:#4fc3f7; }
            QPushButton:hover   { background:#2a4a2a; }
        """)
        self.btn_draw_start.toggled.connect(self._on_draw_toggle)
        dl.addWidget(self.btn_draw_start)

        hint_draw = QLabel("Tipp: Werkzeug wählen →\nZeichnen aktivieren →\nauf dem Bild zeichnen.")
        hint_draw.setStyleSheet("color:#555; font-size:10px;")
        dl.addWidget(hint_draw)

        layout.addWidget(grp_draw)

        # ── Text-to-Drawing (Formen-Bibliothek)
        grp_shapes = self._grp("✨  TEXT-TO-DRAWING")
        sl_layout  = QVBoxLayout(grp_shapes); sl_layout.setSpacing(6)

        info_lbl = QLabel("Form wählen → Größe setzen\n→ auf dem Bild platzieren")
        info_lbl.setStyleSheet(
            "color:#888; font-size:10px; background:#141414; "
            "border-radius:3px; padding:5px;")
        sl_layout.addWidget(info_lbl)

        # Shape-Dropdown
        self.shape_combo = QComboBox()
        self.shape_combo.setStyleSheet("""
            QComboBox { background:#2d2d2d; color:#ddd; border:1px solid #3a3a3a;
                border-radius:4px; padding:6px 10px; font-size:13px; }
            QComboBox::drop-down { border:none; width:22px; }
            QComboBox QAbstractItemView { background:#252525; color:#ddd; font-size:13px;
                selection-background-color:#0e639c; border:1px solid #3a3a3a; }
        """)
        for key in SHAPE_LIBRARY:
            self.shape_combo.addItem(key)
        sl_layout.addWidget(self.shape_combo)

        # Formgröße-Slider
        self.sl_shape_size = LabeledSlider("Formgröße (px)", 30, 400, 120)
        sl_layout.addWidget(self.sl_shape_size)

        # Platzieren-Button
        btn_place = QPushButton("✨  Form auf Bild platzieren")
        btn_place.setStyleSheet("""
            QPushButton { background:#1a3050; color:#7ec8f7; border:1px solid #2a5080;
                border-radius:4px; padding:8px; font-size:12px; font-weight:bold; }
            QPushButton:hover    { background:#1e4070; border-color:#4fc3f7; color:#fff; }
            QPushButton:disabled { background:#1a1a1a; color:#444; }
        """)
        btn_place.clicked.connect(self.start_shape_placer)
        sl_layout.addWidget(btn_place)

        # Vorschau-Grid der 8 Formen als kleine Buttons
        preview_lbl = QLabel("Schnellwahl:")
        preview_lbl.setStyleSheet("color:#666; font-size:10px;")
        sl_layout.addWidget(preview_lbl)

        shape_keys = list(SHAPE_LIBRARY.keys())
        for row_start in range(0, len(shape_keys), 4):
            row = QHBoxLayout()
            for key in shape_keys[row_start:row_start + 4]:
                emoji = key.split()[0]
                btn   = QPushButton(emoji)
                btn.setFixedSize(44, 36)
                btn.setToolTip(key)
                btn.setStyleSheet("""
                    QPushButton { background:#252525; color:#ddd; border:1px solid #333;
                        border-radius:4px; font-size:16px; }
                    QPushButton:hover { background:#333; border-color:#4fc3f7; }
                """)
                btn.clicked.connect(lambda _, k=key: (
                    self.shape_combo.setCurrentText(k),
                    self.start_shape_placer()
                ))
                row.addWidget(btn)
            sl_layout.addLayout(row)

        layout.addWidget(grp_shapes)

        # ── Grundkorrekturen
        grp_basic = self._grp("◎  GRUNDKORREKTUREN")
        gl = QVBoxLayout(grp_basic); gl.setSpacing(5)
        self.sl_brightness = LabeledSlider("Helligkeit",  0, 200, 100)
        self.sl_contrast   = LabeledSlider("Kontrast",    0, 200, 100)
        self.sl_saturation = LabeledSlider("Sättigung",   0, 200, 100)
        self.sl_sharpness  = LabeledSlider("Schärfe",     0, 200, 100)
        for sl in [self.sl_brightness, self.sl_contrast,
                   self.sl_saturation, self.sl_sharpness]:
            sl.value_changed.connect(self._apply_adjustments)
            gl.addWidget(sl)
        layout.addWidget(grp_basic)

        # ── Transformationen
        grp_t = self._grp("⟳  TRANSFORMATIONEN")
        tl = QVBoxLayout(grp_t); tl.setSpacing(4)
        btn_s2 = btn_s  # selber Style
        r1 = QHBoxLayout()
        for txt, fn in [("↻ 90° rechts", self.rotate_cw), ("↺ 90° links", self.rotate_ccw)]:
            b = QPushButton(txt); b.setStyleSheet(btn_s2); b.clicked.connect(fn); r1.addWidget(b)
        tl.addLayout(r1)
        r2 = QHBoxLayout()
        for txt, fn in [("↔ H-Spiegeln", self.flip_horizontal), ("↕ V-Spiegeln", self.flip_vertical)]:
            b = QPushButton(txt); b.setStyleSheet(btn_s2); b.clicked.connect(fn); r2.addWidget(b)
        tl.addLayout(r2)
        layout.addWidget(grp_t)

        # ── Filter-Dropdown (20+ Filter)
        grp_f = self._grp("🎨  FILTER")
        fl = QVBoxLayout(grp_f); fl.setSpacing(6)

        self.filter_combo = QComboBox()
        self.filter_combo.setStyleSheet("""
            QComboBox { background:#2d2d2d; color:#ddd; border:1px solid #3a3a3a;
                border-radius:4px; padding:6px 10px; font-size:12px; }
            QComboBox::drop-down { border:none; width:20px; }
            QComboBox QAbstractItemView { background:#252525; color:#ddd;
                selection-background-color:#0e639c; border:1px solid #3a3a3a; }
        """)
        # Alle Filter in Gruppen
        filters = [
            ("── Farbfilter ──────────────", None),
            ("⬛  Schwarz / Weiß",          self.apply_grayscale),
            ("🌅  Sepia (Vintage)",          self.apply_sepia),
            ("🔵  Kühler Ton (Cool)",        self.apply_cool),
            ("🔴  Warmer Ton (Warm)",        self.apply_warm),
            ("🟣  Lila / Violett",           self.apply_purple),
            ("🟢  Grünstich",               self.apply_green),
            ("🎨  Farben invertieren",       self.apply_invert),
            ("── Schärfe & Weiche ────────", None),
            ("💡  Schärfen (Sharpen)",       self.apply_sharpen),
            ("✨  Stark schärfen",           self.apply_sharpen_strong),
            ("🌫  Weichzeichnen",            self.apply_blur),
            ("🌀  Starkes Weichzeichnen",    self.apply_blur_strong),
            ("── Effekte ─────────────────", None),
            ("✨  Emboss (Relief)",          self.apply_emboss),
            ("🌊  Kanten betonen",           self.apply_edges),
            ("🔆  Auto-Kontrast",            self.apply_autocontrast),
            ("📺  Rauschen (Noise)",         self.apply_noise),
            ("── Kreativ ─────────────────", None),
            ("🎭  Comic / Cartoon",          self.apply_comic),
            ("🐕  Hunde-Sicht",              self.apply_dog_vision),
            ("🌈  Psychedelic",              self.apply_psychedelic),
            ("🌙  Nacht-Modus",              self.apply_night),
            ("🖼  Alte Foto-Vignette",       self.apply_vignette),
            ("🎞  Film-Korn",                self.apply_film_grain),
            ("🌊  Aquarell",                 self.apply_watercolor),
            ("⚡  Hochkontrast Swatch",      self.apply_high_contrast),
        ]

        self._filter_map = {}
        for name, fn in filters:
            self.filter_combo.addItem(name)
            if fn is None:
                # Trennzeilen-Einträge deaktivieren
                idx = self.filter_combo.count() - 1
                self.filter_combo.model().item(idx).setEnabled(False)
                self.filter_combo.model().item(idx).setForeground(QColor("#4fc3f7"))
            else:
                self._filter_map[name] = fn

        fl.addWidget(self.filter_combo)

        btn_apply = QPushButton("▶  Filter anwenden")
        btn_apply.setStyleSheet("""
            QPushButton { background:#0e639c; color:white; border:none;
                border-radius:4px; padding:8px; font-size:12px; }
            QPushButton:hover { background:#1177bb; }
        """)
        btn_apply.clicked.connect(self._apply_selected_filter)
        fl.addWidget(btn_apply)
        layout.addWidget(grp_f)

        # ── Reset-Button
        btn_reset = QPushButton("🔄  Alles zurücksetzen")
        btn_reset.setStyleSheet("""
            QPushButton { background:#3a1e1e; color:#f08080; border:1px solid #5a2a2a;
                border-radius:4px; padding:7px; font-size:12px; }
            QPushButton:hover { background:#4a2525; }
        """)
        btn_reset.clicked.connect(self.reset_to_original)
        layout.addWidget(btn_reset)

        # ── KI-Analyse Panel (großzügig)
        grp_ai = self._grp("🤖  KI-ANALYSE  (Moondream)")
        al = QVBoxLayout(grp_ai); al.setSpacing(6)

        # Großes Textfeld für die Antwort
        self.ai_text = QTextEdit()
        self.ai_text.setReadOnly(True)
        self.ai_text.setMinimumHeight(120)
        self.ai_text.setMaximumHeight(180)
        self.ai_text.setStyleSheet("""
            QTextEdit { background:#141414; color:#ccc; border:1px solid #2d2d2d;
                border-radius:4px; padding:8px; font-size:12px; line-height:1.5; }
        """)
        self.ai_text.setPlainText("Noch kein Bild analysiert.\n\nKlicke auf 'Analysieren' um die KI zu starten.")
        al.addWidget(self.ai_text)

        self.btn_ai = QPushButton("🤖  Analysieren  (Strg+A)")
        self.btn_ai.setEnabled(False)
        self.btn_ai.setStyleSheet("""
            QPushButton { background:#0e639c; color:white; border:none;
                border-radius:4px; padding:9px; font-size:12px; font-weight:bold; }
            QPushButton:hover    { background:#1177bb; }
            QPushButton:disabled { background:#2a2a2a; color:#555; }
        """)
        self.btn_ai.clicked.connect(self.run_ai_analysis)
        al.addWidget(self.btn_ai)
        layout.addWidget(grp_ai)

        layout.addStretch()
        scroll.setWidget(panel)
        dock.setWidget(scroll)
        dock.setMinimumWidth(260)
        dock.setMaximumWidth(300)

    def _grp(self, title: str) -> QGroupBox:
        """Einheitlich gestaltete Gruppe."""
        g = QGroupBox(title)
        g.setStyleSheet("""
            QGroupBox { color:#4fc3f7; font-size:10px; font-weight:bold;
                letter-spacing:1px; border:1px solid #2a2a2a; border-radius:6px;
                margin-top:8px; padding-top:6px; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
        """)
        return g

    # ── Statusleiste ────────────────────────────
    def _setup_statusbar(self):
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            "background:#111; color:#666; font-size:11px; border-top:1px solid #2a2a2a;")
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "SBS Bildeditor v2  –  Öffne ein Bild  |  ✂ Rect/Lasso Crop  |  🎨 20+ Filter  |  🤖 KI")

    # ══════════════════════════════════════════════
    #  DATEI-OPERATIONEN
    # ══════════════════════════════════════════════

    def open_file(self):
        """Bild öffnen und als PIL-Image in den Editor laden."""
        if not PIL_AVAILABLE:
            QMessageBox.warning(self, "Fehler", "pip install Pillow"); return
        path, _ = QFileDialog.getOpenFileName(self, "Bild öffnen", "",
            "Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff);;Alle (*)")
        if not path: return
        try:
            pil = PILImage.open(path).convert("RGBA")
            self.original_pil = pil.copy()
            self.current_pil  = pil.copy()
            self.current_file = path
            self.history.clear()
            self.canvas.set_image(pil_to_qpixmap(pil))
            self._reset_sliders()
            self.btn_ai.setEnabled(True)
            self.ai_text.setPlainText("Bild geladen. Klicke 'Analysieren' für KI-Beschreibung.")
            self._update_status()
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))

    def save_file(self):
        if self.current_pil and self.current_file:
            self._save_to(self.current_file)
        else:
            self.save_file_as()

    def save_file_as(self):
        if not self.current_pil: return
        path, _ = QFileDialog.getSaveFileName(self, "Speichern unter", "",
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)")
        if path: self._save_to(path)

    def _save_to(self, path: str):
        try:
            img = self.current_pil.copy()
            if path.lower().endswith((".jpg", ".jpeg")):
                img = img.convert("RGB")
            img.save(path)
            self.status_bar.showMessage(f"✅ Gespeichert: {path.split('/')[-1]}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))

    # ══════════════════════════════════════════════
    #  UNDO / RESET
    # ══════════════════════════════════════════════

    def _push(self):
        """Aktuellen Zustand in Undo-Stack schieben."""
        if self.current_pil:
            self.history.append(self.current_pil.copy())
            if len(self.history) > 20:
                self.history.pop(0)

    def undo(self):
        if not self.history:
            self.status_bar.showMessage("Nichts zum Rückgängigmachen.", 2000); return
        self.current_pil = self.history.pop()
        self.original_pil = self.current_pil.copy()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))
        self._reset_sliders()
        self._update_status()

    def reset_to_original(self):
        if self.original_pil:
            self._push()
            self.current_pil = self.original_pil.copy()
            self.canvas.update_image(pil_to_qpixmap(self.current_pil))
            self._reset_sliders()
            self.status_bar.showMessage("🔄 Original wiederhergestellt", 2000)

    def _reset_sliders(self):
        for sl in [self.sl_brightness, self.sl_contrast,
                   self.sl_saturation, self.sl_sharpness]:
            sl.reset(100)

    # ══════════════════════════════════════════════
    #  CROP-WERKZEUGE
    # ══════════════════════════════════════════════

    def start_rect_crop(self):
        """
        Rechteck-Zuschnitt: Overlay starten, Nutzer zieht Rechteck.
        Das Overlay wird über dem Canvas angezeigt.
        """
        if not self.current_pil:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        self.status_bar.showMessage(
            "✂ Rechteck-Crop: Klicken und ziehen → loslassen zum Zuschneiden  |  ESC = Abbrechen")
        overlay = self.canvas.start_crop_overlay("rect")
        overlay.rect_selected.connect(self._do_rect_crop)
        overlay.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))

    def start_lasso_crop(self):
        """
        Lasso-Zuschnitt: Overlay starten, Nutzer zeichnet freie Form.
        Der Bereich außerhalb wird transparent/weiß.
        """
        if not self.current_pil:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        self.status_bar.showMessage(
            "🔮 Lasso-Crop: Mit gedrückter Maustaste zeichnen → loslassen zum Zuschneiden  |  ESC = Abbrechen")
        overlay = self.canvas.start_crop_overlay("lasso")
        overlay.lasso_selected.connect(self._do_lasso_crop)
        overlay.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))

    def _do_rect_crop(self, rect: QRect):
        """
        Rechteck-Koordinaten vom Overlay in Bildkoordinaten umrechnen
        und das Bild zuschneiden.
        """
        if not self.current_pil: return

        # Zoom-Faktor berücksichtigen (Overlay-Koordinaten → Bildkoordinaten)
        z = self.canvas.get_zoom()
        x1 = int(rect.left()   / z)
        y1 = int(rect.top()    / z)
        x2 = int(rect.right()  / z)
        y2 = int(rect.bottom() / z)

        # Auf Bildgrenzen begrenzen
        w, h = self.current_pil.width, self.current_pil.height
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 > x1 and y2 > y1:
            self._push()
            self.current_pil  = self.current_pil.crop((x1, y1, x2, y2))
            self.original_pil = self.current_pil.copy()
            self.canvas.update_image(pil_to_qpixmap(self.current_pil))
            self._update_status()
            self.status_bar.showMessage(
                f"✂ Zugeschnitten auf {x2-x1} × {y2-y1} px", 3000)

    def _do_lasso_crop(self, points: list):
        """
        Lasso-Punkte vom Overlay in Bildkoordinaten umrechnen.
        Erstellt eine Maske aus dem Polygon und schneidet den
        Begrenzungsrahmen des Polygons aus.
        """
        if not self.current_pil or len(points) < 3: return

        z = self.canvas.get_zoom()
        w, h = self.current_pil.width, self.current_pil.height

        # Overlay-Punkte → Bildpixel
        img_pts = [(int(p.x() / z), int(p.y() / z)) for p in points]

        # Begrenzungsrahmen des Polygons berechnen
        xs = [p[0] for p in img_pts]
        ys = [p[1] for p in img_pts]
        x1, y1 = max(0, min(xs)), max(0, min(ys))
        x2, y2 = min(w, max(xs)), min(h, max(ys))

        if x2 <= x1 or y2 <= y1: return

        self._push()

        # Maske erstellen: Polygon füllen
        mask = PILImage.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        draw.polygon(img_pts, fill=255)

        # Bild mit Maske kombinieren (außen transparent)
        rgba = self.current_pil.copy().convert("RGBA")
        result = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        result.paste(rgba, mask=mask)

        # Auf Begrenzungsrahmen zuschneiden
        self.current_pil  = result.crop((x1, y1, x2, y2))
        self.original_pil = self.current_pil.copy()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))
        self._update_status()
        self.status_bar.showMessage(
            f"🔮 Lasso-Crop: {x2-x1} × {y2-y1} px ausgeschnitten", 3000)

    # ══════════════════════════════════════════════
    #  GRUNDKORREKTUREN (Schieberegler)
    # ══════════════════════════════════════════════

    def _apply_adjustments(self):
        """
        Wendet Helligkeit/Kontrast/Sättigung/Schärfe auf das
        Original an. Wichtig: Immer vom Original starten,
        damit Werte nicht kumulieren.
        """
        if not self.original_pil: return
        img = self.original_pil.copy().convert("RGB")
        img = ImageEnhance.Brightness(img).enhance(self.sl_brightness.value() / 100)
        img = ImageEnhance.Contrast(img).enhance(self.sl_contrast.value()    / 100)
        img = ImageEnhance.Color(img).enhance(self.sl_saturation.value()  / 100)
        img = ImageEnhance.Sharpness(img).enhance(self.sl_sharpness.value()  / 100)
        self.current_pil = img.convert("RGBA")
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))

    # ══════════════════════════════════════════════
    #  TRANSFORMATIONEN
    # ══════════════════════════════════════════════

    def rotate_cw(self):   self._transform(lambda i: i.rotate(-90, expand=True))
    def rotate_ccw(self):  self._transform(lambda i: i.rotate( 90, expand=True))
    def flip_horizontal(self): self._transform(lambda i: ImageOps.mirror(i))
    def flip_vertical(self):   self._transform(lambda i: ImageOps.flip(i))

    def _transform(self, fn):
        if not self.current_pil: return
        self._push()
        self.current_pil  = fn(self.current_pil.convert("RGBA")).convert("RGBA")
        self.original_pil = self.current_pil.copy()
        self._reset_sliders()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))
        self._update_status()

    # ══════════════════════════════════════════════
    #  FILTER-DROPDOWN
    # ══════════════════════════════════════════════

    def _apply_selected_filter(self):
        """Wählt den aktiven Filter aus dem Dropdown und wendet ihn an."""
        name = self.filter_combo.currentText()
        fn   = self._filter_map.get(name)
        if fn:
            fn()
        else:
            self.status_bar.showMessage("Bitte einen Filter auswählen.", 2000)

    def _filt(self, fn):
        """Hilfsmethode: Filter anwenden mit Undo-Schritt."""
        if not self.current_pil: return
        self._push()
        self.current_pil  = fn(self.current_pil).convert("RGBA")
        self.original_pil = self.current_pil.copy()
        self._reset_sliders()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))
        self._update_status()

    # ── Farbfilter
    def apply_grayscale(self):
        self._filt(lambda i: ImageOps.grayscale(i).convert("RGBA"))

    def apply_sepia(self):
        def sepia(img):
            g = ImageOps.grayscale(img)
            r = g.point(lambda x: min(255, int(x * 1.1)))
            gch = g.point(lambda x: min(255, int(x * 0.9)))
            b = g.point(lambda x: min(255, int(x * 0.7)))
            return PILImage.merge("RGB", (r, gch, b))
        self._filt(sepia)

    def apply_cool(self):
        """Kühler Blauton – simuliert Kältefilter."""
        def cool(img):
            r, g, b, *a = img.convert("RGBA").split()
            r = r.point(lambda x: max(0, x - 20))
            b = b.point(lambda x: min(255, x + 30))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(cool)

    def apply_warm(self):
        """Warmer Orangeton – simuliert Wärmefilter."""
        def warm(img):
            r, g, b, *a = img.convert("RGBA").split()
            r = r.point(lambda x: min(255, x + 30))
            b = b.point(lambda x: max(0, x - 20))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(warm)

    def apply_purple(self):
        """Lila/Violett-Stich."""
        def purple(img):
            r, g, b, *a = img.convert("RGBA").split()
            r = r.point(lambda x: min(255, x + 20))
            b = b.point(lambda x: min(255, x + 40))
            g = g.point(lambda x: max(0, x - 10))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(purple)

    def apply_green(self):
        """Grünstich-Filter."""
        def green(img):
            r, g, b, *a = img.convert("RGBA").split()
            g = g.point(lambda x: min(255, x + 30))
            r = r.point(lambda x: max(0, x - 10))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(green)

    def apply_invert(self):
        self._filt(lambda i: ImageOps.invert(i.convert("RGB")).convert("RGBA"))

    # ── Schärfe/Weiche
    def apply_sharpen(self):
        self._filt(lambda i: i.filter(ImageFilter.SHARPEN))

    def apply_sharpen_strong(self):
        """Dreifaches Schärfen für deutlichen Effekt."""
        def sharpen3(img):
            for _ in range(3):
                img = img.filter(ImageFilter.SHARPEN)
            return img
        self._filt(sharpen3)

    def apply_blur(self):
        self._filt(lambda i: i.filter(ImageFilter.GaussianBlur(radius=2)))

    def apply_blur_strong(self):
        self._filt(lambda i: i.filter(ImageFilter.GaussianBlur(radius=6)))

    # ── Effekte
    def apply_emboss(self):
        self._filt(lambda i: i.filter(ImageFilter.EMBOSS))

    def apply_edges(self):
        self._filt(lambda i: i.filter(ImageFilter.EDGE_ENHANCE_MORE))

    def apply_autocontrast(self):
        self._filt(lambda i: ImageOps.autocontrast(i.convert("RGB")))

    def apply_noise(self):
        """Zufälliges Rauschen hinzufügen (analoger Look)."""
        import random
        def noise(img):
            img = img.convert("RGB")
            pixels = img.load()
            for y in range(img.height):
                for x in range(img.width):
                    n = random.randint(-30, 30)
                    r, g, b = pixels[x, y]
                    pixels[x, y] = (
                        max(0, min(255, r + n)),
                        max(0, min(255, g + n)),
                        max(0, min(255, b + n))
                    )
            return img
        self._filt(noise)

    # ── Kreativ-Filter
    def apply_comic(self):
        """
        Comic/Cartoon-Filter:
        Kanten betonen + Posterize (Farbstufen reduzieren) + Sättigung erhöhen.
        Ergibt einen gezeichneten, comicartigen Look.
        """
        def comic(img):
            rgb = img.convert("RGB")
            # Kanten extrahieren
            edges = rgb.filter(ImageFilter.EDGE_ENHANCE_MORE)
            # Posterize: auf 4 Farbstufen reduzieren
            poster = ImageOps.posterize(rgb, 4)
            # Sättigung erhöhen
            poster = ImageEnhance.Color(poster).enhance(2.5)
            # Schärfen
            poster = ImageEnhance.Sharpness(poster).enhance(3.0)
            return poster
        self._filt(comic)

    def apply_dog_vision(self):
        """
        Hunde-Sicht (Dichromacy):
        Hunde sehen kein Rot – nur Blau und Gelb.
        Rot-Kanal wird durch Grün ersetzt.
        """
        def dog(img):
            r, g, b, *a = img.convert("RGBA").split()
            # Rot durch Gelb/Grün ersetzen (Dichromacy-Simulation)
            new_r = g   # kein echtes Rot
            new_g = g
            new_b = b.point(lambda x: min(255, int(x * 1.3)))
            result = PILImage.merge("RGBA", (new_r, new_g, new_b, a[0]) if a else (new_r, new_g, new_b))
            return ImageEnhance.Contrast(result).enhance(0.8)
        self._filt(dog)

    def apply_psychedelic(self):
        """
        Psychedelic-Filter: Extreme Farbverschiebung durch
        Kanal-Rotation (R→G, G→B, B→R) + hohe Sättigung.
        """
        def psycho(img):
            r, g, b, *a = img.convert("RGBA").split()
            # Kanäle rotieren für Farbverfälschung
            result = PILImage.merge("RGBA", (g, b, r, a[0]) if a else (g, b, r))
            return ImageEnhance.Color(result).enhance(3.0)
        self._filt(psycho)

    def apply_night(self):
        """
        Nacht-Modus: Blaustich + Abdunkeln + leichtes Rauschen.
        Simuliert Nachtaufnahmen oder Nachtsichtgeräte.
        """
        def night(img):
            img = img.convert("RGB")
            img = ImageEnhance.Brightness(img).enhance(0.4)
            r, g, b = img.split()
            b = b.point(lambda x: min(255, int(x * 1.5)))
            g = g.point(lambda x: min(255, int(x * 1.2)))
            return PILImage.merge("RGB", (r, g, b))
        self._filt(night)

    def apply_vignette(self):
        """
        Foto-Vignette: Rand des Bildes dunkel, Mitte hell.
        Klassischer Retro-Fotoeffekt.
        """
        def vignette(img):
            img = img.convert("RGBA")
            w, h = img.width, img.height
            # Schwarze Vignette-Maske erstellen
            mask = PILImage.new("L", (w, h), 255)
            draw = ImageDraw.Draw(mask)
            # Gradient von Mitte nach außen
            cx, cy = w // 2, h // 2
            for i in range(min(w, h) // 2, 0, -1):
                alpha = int(255 * (1 - (i / (min(w, h) // 2)) ** 0.5))
                draw.ellipse([cx - i, cy - i, cx + i, cy + i], fill=alpha)
            dark = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
            dark.putalpha(PILImage.eval(mask, lambda x: 255 - x))
            result = PILImage.alpha_composite(img, dark)
            return result
        self._filt(vignette)

    def apply_film_grain(self):
        """Film-Korn: Feines Rauschen + leichte Sättigungsreduktion."""
        import random
        def grain(img):
            img = img.convert("RGB")
            pix = img.load()
            for y in range(img.height):
                for x in range(img.width):
                    n = random.randint(-15, 15)
                    r, g, b = pix[x, y]
                    pix[x, y] = (max(0,min(255,r+n)), max(0,min(255,g+n)), max(0,min(255,b+n)))
            return ImageEnhance.Color(img).enhance(0.85)
        self._filt(grain)

    def apply_watercolor(self):
        """
        Aquarell-Effekt: Weichzeichnen + Kanten betonen + Sättigung hoch.
        Ergibt einen gemalten, weichen Look.
        """
        def watercolor(img):
            img = img.convert("RGB")
            img = img.filter(ImageFilter.GaussianBlur(radius=1))
            img = img.filter(ImageFilter.SMOOTH_MORE)
            img = ImageEnhance.Color(img).enhance(1.8)
            img = ImageEnhance.Contrast(img).enhance(0.9)
            return img
        self._filt(watercolor)

    def apply_high_contrast(self):
        """Hochkontrast-Swatch: Extremes Posterize + Kontrasterhöhung."""
        def hc(img):
            img = ImageOps.posterize(img.convert("RGB"), 2)
            return ImageEnhance.Contrast(img).enhance(3.0)
        self._filt(hc)

    # ══════════════════════════════════════════════
    #  ZEICHEN-WERKZEUGE
    # ══════════════════════════════════════════════

    def set_draw_tool(self, tool: str):
        """
        Aktives Zeichen-Werkzeug wechseln.
        Schließt das laufende Overlay sofort und startet es
        mit dem neuen Werkzeug neu – kein verzögerter Wechsel.
        """
        self.draw_tool = tool

        # Alle Tool-Buttons: nur den gewählten aktivieren
        for k, btn in self._draw_btns.items():
            btn.setChecked(k == tool)

        # Overlay sofort neu starten falls Zeichenmodus aktiv ist
        if self.btn_draw_start.isChecked() and self.current_pil:
            # Altes Overlay sauber schließen (blockSignals verhindert Callback-Loop)
            if self.canvas._overlay:
                self.canvas._overlay.blockSignals(True)
                self.canvas._overlay.close()
                self.canvas._overlay = None
            # Neues Overlay mit neuem Werkzeug sofort starten
            self._start_draw_overlay()

        self.status_bar.showMessage(
            f"✏️  Werkzeug: {tool}  |  Farbe: {self.draw_color.name()}  |  Größe: {self.draw_size}px"
        )

    def pick_color(self):
        """Farbdialog öffnen. Startet Overlay sofort mit neuer Farbe neu."""
        color = QColorDialog.getColor(self.draw_color, self, "Farbe wählen")
        if color.isValid():
            self.draw_color = color
            self.color_preview.setStyleSheet(
                f"background:{color.name()}; border:1px solid #555; border-radius:3px;")
            # Overlay sofort mit neuer Farbe neu starten
            if self.btn_draw_start.isChecked() and self.current_pil:
                if self.canvas._overlay:
                    self.canvas._overlay.blockSignals(True)
                    self.canvas._overlay.close()
                    self.canvas._overlay = None
                self._start_draw_overlay()

    def _on_draw_toggle(self, active: bool):
        """
        Zeichenmodus ein- oder ausschalten.
        Bei Aktivierung: DrawOverlay starten.
        Bei Deaktivierung: Overlay schließen.
        """
        if not self.current_pil:
            self.btn_draw_start.setChecked(False)
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000)
            return

        if active:
            self.btn_draw_start.setText("⏹  Zeichnen beenden  (ESC)")
            self._start_draw_overlay()
        else:
            self.btn_draw_start.setText("▶  Zeichnen aktivieren")
            if self.canvas._overlay:
                self.canvas._overlay.close()
                self.canvas._overlay = None

    def _start_draw_overlay(self):
        """
        Neues DrawOverlay mit aktuellem Werkzeug, Farbe und Größe starten.
        Verbindet das drawing_done-Signal mit der Methode die den
        Strich permanent auf das PIL-Bild überträgt.
        """
        overlay = self.canvas.start_draw_overlay(
            self.draw_tool, self.draw_color, self.draw_size
        )
        overlay.drawing_done.connect(self._apply_draw_fn)
        # Wenn Overlay durch ESC geschlossen wird → Toggle zurücksetzen
        overlay.destroyed.connect(lambda: (
            self.btn_draw_start.blockSignals(True),
            self.btn_draw_start.setChecked(False),
            self.btn_draw_start.setText("▶  Zeichnen aktivieren"),
            self.btn_draw_start.blockSignals(False)
        ))

    def _apply_draw_fn(self, draw_fn):
        """
        Wendet eine Zeichenfunktion permanent auf das PIL-Bild an.
        Legt einen Undo-Schritt an. Startet danach das Overlay
        mit dem AKTUELL eingestellten Werkzeug neu (nicht dem alten).
        """
        if not self.current_pil:
            return
        self._push()
        result = draw_fn(self.current_pil.copy())
        self.current_pil  = result.convert("RGBA")
        self.original_pil = self.current_pil.copy()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))

        # Overlay mit dem AKTUELL gewählten Werkzeug neu starten
        if self.btn_draw_start.isChecked():
            if self.canvas._overlay:
                self.canvas._overlay.blockSignals(True)
                self.canvas._overlay.close()
                self.canvas._overlay = None
            self._start_draw_overlay()  # nutzt self.draw_tool (immer aktuell)

    # ══════════════════════════════════════════════
    #  TEXT-TO-DRAWING: Formen platzieren
    # ══════════════════════════════════════════════

    def start_shape_placer(self):
        """
        Startet das Shape-Placer-Overlay.
        Die gewählte Form wird als Vorschau unter dem Mauszeiger gezeigt.
        Ein Klick platziert sie permanent auf dem Bild.
        """
        if not self.current_pil:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000)
            return

        # Zeichenmodus deaktivieren falls aktiv
        if self.btn_draw_start.isChecked():
            self.btn_draw_start.setChecked(False)

        shape_key = self.shape_combo.currentText()
        size      = self.sl_shape_size.value()

        overlay = self.canvas.start_shape_placer(shape_key, size, self.draw_color)
        overlay.shape_placed.connect(self._on_shape_placed)
        overlay.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))

        self.status_bar.showMessage(
            f"✨  {shape_key}  —  Klick zum Platzieren  |  ESC = Abbruch")

    def _on_shape_placed(self, shape_key: str, ix: int, iy: int):
        """
        Callback wenn Nutzer auf das Bild geklickt hat.
        Zeichnet die Form permanent auf das PIL-Bild.
        """
        if not self.current_pil:
            return
        self._push()
        color_tuple = (self.draw_color.red(), self.draw_color.green(),
                       self.draw_color.blue(), 255)
        size      = self.sl_shape_size.value()
        lw        = max(2, size // 40)   # Strichbreite proportional zur Größe

        result = draw_shape_on_pil(
            self.current_pil.copy(),
            shape_key, ix, iy, size, color_tuple, lw
        )
        self.current_pil  = result.convert("RGBA")
        self.original_pil = self.current_pil.copy()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))
        self.canvas._overlay = None
        self._update_status()
        self.status_bar.showMessage(
            f"✅  {shape_key} platziert bei ({ix}, {iy}) px  |  Strg+Z zum Rückgängigmachen", 3000)

    # ══════════════════════════════════════════════
    #  ZOOM
    # ══════════════════════════════════════════════

    def zoom_in(self):
        self.canvas.set_zoom(self.canvas.get_zoom() + self.ZOOM_STEP)
        self._update_status()

    def zoom_out(self):
        self.canvas.set_zoom(self.canvas.get_zoom() - self.ZOOM_STEP)
        self._update_status()

    def zoom_reset(self):
        self.canvas.set_zoom(1.0); self._update_status()

    def zoom_fit(self):
        if not self.canvas._pix_orig: return
        aw = self.scroll.viewport().width()  - 20
        ah = self.scroll.viewport().height() - 20
        iw = self.canvas._pix_orig.width()
        ih = self.canvas._pix_orig.height()
        self.canvas.set_zoom(min(aw / iw, ah / ih))
        self._update_status()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_in() if event.angleDelta().y() > 0 else self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def event(self, event):
        if event.type() == event.Type.Gesture:
            pinch = event.gesture(Qt.GestureType.PinchGesture)
            if pinch:
                self.canvas.set_zoom(self.canvas.get_zoom() * pinch.scaleFactor())
                self._update_status()
            return True
        return super().event(event)

    # ══════════════════════════════════════════════
    #  KI-ANALYSE
    # ══════════════════════════════════════════════

    def run_ai_analysis(self):
        """Startet Moondream-Analyse im Hintergrund-Thread."""
        if not self.current_pil: return
        self.ai_text.setPlainText("🔄  Moondream analysiert das Bild…\n\nBitte warten.")
        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("⏳  Analysiere…")
        self.ai_worker = AIWorker(self.current_pil)
        self.ai_worker.result_ready.connect(self._on_ai_result)
        self.ai_worker.error_occurred.connect(self._on_ai_error)
        self.ai_worker.start()

    def _on_ai_result(self, desc: str):
        self.ai_text.setPlainText(f"🤖  KI-Beschreibung:\n\n{desc.strip()}")
        self._reset_ai_btn()

    def _on_ai_error(self, err: str):
        self.ai_text.setPlainText(f"⚠  Fehler:\n\n{err}")
        self._reset_ai_btn()

    def _reset_ai_btn(self):
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("🤖  Analysieren  (Strg+A)")

    # ══════════════════════════════════════════════
    #  STATUS
    # ══════════════════════════════════════════════

    def _update_status(self):
        if self.current_file and self.current_pil:
            pct  = int(self.canvas.get_zoom() * 100)
            name = self.current_file.split("/")[-1]
            self.status_bar.showMessage(
                f"📄  {name}   |   "
                f"{self.current_pil.width} × {self.current_pil.height} px   |   "
                f"Zoom: {pct}%   |   Undo: {len(self.history)} Schritte")


# ══════════════════════════════════════════════════════════════
#  EINSTIEGSPUNKT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(38,  38,  38))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,            QColor(25,  25,  25))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,          QColor(55,  55,  55))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(14,  99, 156))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(220, 220, 220))
    app.setPalette(p)

    win = ImageEditor()
    win.show()
    sys.exit(app.exec())

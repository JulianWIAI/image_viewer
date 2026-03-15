"""
==============================================================
 SBS Bildeditor v4 – Bachelor Professional
 Autor   : [Dein Name]
 Datum   : März 2026
 Schule  : SBS Herzogenaurach
 Prüfung : Bachelor Professional – Digitale Transformation
==============================================================

 PROJEKTÜBERSICHT:
 -----------------
 Professioneller Bildeditor im Stil von Adobe Photoshop,
 entwickelt mit PyQt6 (GUI-Framework) und Pillow (Bildverarbeitung).

 ARCHITEKTUR (Klassen-Übersicht):
 ----------------------------------
 ┌─────────────────────────────────────────────┐
 │  ImageEditor  (QMainWindow)                 │  ← Hauptfenster,
 │  • Menüleiste, Toolbar, Statusleiste        │    verwaltet alle
 │  • Dock-Panel mit allen Werkzeugen          │    Zustände
 │                                             │
 │  ┌──────────────────────────────────────┐   │
 │  │  ImageCanvas  (QLabel)               │   │  ← Zeigt das Bild,
 │  │  • Zoom-Verwaltung                   │   │    hostet Overlays
 │  │  • Hostet alle Overlay-Widgets       │   │
 │  │                                      │   │
 │  │  ┌─────────────┐ ┌───────────────┐   │   │
 │  │  │ CropOverlay │ │  DrawOverlay  │   │   │  ← Transparente
 │  │  │ (Rect/Lasso)│ │ (8 Werkzeuge) │   │   │    Widgets über
 │  │  └─────────────┘ └───────────────┘   │   │    dem Bild
 │  │  ┌──────────────────────────────┐    │   │
 │  │  │  ShapePlacerOverlay          │    │   │
 │  │  │  (Text-to-Drawing Vorschau)  │    │   │
 │  │  └──────────────────────────────┘    │   │
 │  └──────────────────────────────────────┘   │
 │                                             │
 │  AIWorker  (QThread)                        │  ← Läuft im
 │  • Moondream KI-Analyse im Hintergrund      │    Hintergrund-Thread
 └─────────────────────────────────────────────┘

 SCHLÜSSELKONZEPTE:
 -------------------
 1. Signal/Slot (PyQt6):
    Widgets kommunizieren über Signale. Z.B.:
    slider.valueChanged → _apply_adjustments()
    overlay.drawing_done → _apply_draw_fn()
    → Lose Kopplung, kein direkter Methodenaufruf nötig

 2. PIL / Pillow (Bildverarbeitung):
    Alle Filteroperationen laufen auf PIL-Images (nicht auf QPixmap).
    QPixmap ist nur für die Anzeige.
    PIL → QPixmap: pil_to_qpixmap()

 3. Overlay-Konzept:
    Transparente QWidgets liegen über dem Canvas.
    Sie fangen Mausereignisse ab ohne das Bild selbst zu verändern.
    Erst nach Abschluss (mouseRelease) wird das PIL-Bild aktualisiert.

 4. Undo-Stack:
    Vor jeder destruktiven Operation wird eine Kopie des PIL-Images
    in self.history gespeichert (max. 20 Schritte).

 5. Threading (QThread):
    KI-Analyse läuft in AIWorker (separater Thread).
    Verhindert dass die UI einfriert während Moondream arbeitet.
    Kommunikation zurück zur UI via result_ready Signal.

 ABHÄNGIGKEITEN:
 ----------------
   pip install Pillow PyQt6
   ollama pull moondream   ← für KI-Analyse (optional)
   ollama serve            ← muss laufen für KI
==============================================================
"""

import sys, base64, json, urllib.request, io, math
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QFileDialog, QScrollArea, QStatusBar, QSizePolicy, QToolBar,
    QFrame, QVBoxLayout, QHBoxLayout, QSlider, QGroupBox,
    QDockWidget, QMessageBox, QDialog, QDialogButtonBox,
    QSpinBox, QComboBox, QTextEdit, QSizePolicy, QLineEdit,
    QColorDialog, QInputDialog, QGridLayout, QRadioButton,
    QStackedWidget, QProgressDialog
)
from PyQt6.QtGui import (
    QPixmap, QIcon, QAction, QKeySequence, QFont, QColor,
    QPalette, QImage, QPainter, QPen, QBrush, QPolygon,
    QCursor, QPainterPath
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QRect, QPoint, QPointF

try:
    from PIL import Image as PILImage, ImageFilter, ImageEnhance, ImageOps, ImageDraw, ImageChops
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ══════════════════════════════════════════════════════════════
#  EBENEN-DATENKLASSE
# ══════════════════════════════════════════════════════════════

class Layer:
    """
    Repräsentiert eine einzelne Ebene im Ebenen-System.

    Felder:
      image   – PIL RGBA-Bild (der eigentliche Bildinhalt)
      name    – Anzeigename im Ebenen-Panel
      opacity – Deckkraft 0-100 %  (100 = vollständig sichtbar)
      visible – True = sichtbar, False = ausgeblendet
      x, y    – Offset (Position) auf der Leinwand in Pixeln

    Alle Ebenen zusammen ergeben durch Compositing das angezeigte Bild.
    """
    _counter = 0   # globaler Zähler für automatische Namen

    def __init__(self, image, name=None,
                 opacity: int = 100, visible: bool = True,
                 x: int = 0, y: int = 0):
        Layer._counter += 1
        self.image   = image.convert("RGBA") if image else None
        self.name    = name or f"Ebene {Layer._counter}"
        self.opacity = opacity
        self.visible = visible
        self.x       = x
        self.y       = y


# ══════════════════════════════════════════════════════════════
#  KONVERTIERUNGSFUNKTIONEN
# ══════════════════════════════════════════════════════════════

def pil_to_qpixmap(img: "PILImage.Image") -> QPixmap:
    """
    Konvertiert ein PIL-Image in ein QPixmap für die Qt-Anzeige.

    WARUM diese Funktion?
    PyQt6 kann PIL-Images nicht direkt anzeigen.
    PIL speichert Pixel als Python-Bytes, Qt braucht QImage/QPixmap.
    Weg: PIL → rohe RGBA-Bytes → QImage → QPixmap

    RGBA = 4 Kanäle: Rot, Grün, Blau, Alpha (Transparenz)
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qi = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qi)


# ══════════════════════════════════════════════════════════════
#  FORM-BIBLIOTHEK: Text-to-Drawing Feature
#
#  KONZEPT:
#  Statt eines KI-Modells (z.B. Stable Diffusion, zu langsam für Schule)
#  werden Formen als normalisierte Vektorkoordinaten gespeichert.
#
#  NORMALISIERUNG:
#  Alle Punkte liegen im Bereich 0.0–1.0 (unabhängig von Bildgröße).
#  Beim Zeichnen werden sie mit dem gewünschten size-Parameter skaliert.
#  Vorteil: Eine Form-Definition funktioniert für alle Größen (30px bis 400px).
#
#  VORTEIL gegenüber echtem Text-to-Image:
#  ✓ Kein Modell-Download (mehrere GB)
#  ✓ Sofortige Ausgabe (keine Wartezeit)
#  ✓ Vollständig offline
#  ✓ Deterministisch (immer gleiche Ausgabe)
#  ✓ Erklärbar und nachvollziehbar
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
    Hintergrund-Thread für die Moondream KI-Bildanalyse.

    WARUM ein separater Thread?
    KI-Analyse dauert 5–30 Sekunden. Würde sie im Haupt-Thread laufen,
    würde die gesamte UI einfrieren (kein Klicken, kein Scrollen möglich).
    → QThread verschiebt die Arbeit in einen Hintergrundprozess.
    → Kommunikation zurück zur UI: result_ready Signal (Thread-sicher).

    Ablauf:
      1. PIL-Bild auf 512×512 verkleinern (schnellere Übertragung)
      2. Als JPEG in Base64 kodieren
      3. HTTP-POST an Ollama API (localhost:11434)
      4. Antwort per Signal an ImageEditor senden

    Voraussetzung: ollama serve + ollama pull moondream
    """
    result_ready   = pyqtSignal(str)   # Sendet Beschreibungstext an UI
    error_occurred = pyqtSignal(str)   # Sendet Fehlermeldung an UI

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
                "prompt": "Please describe what you see in this image. Mention the main subject, colors, and background. Write 2-3 complete sentences.",
                "images": [b64],
                "stream": False,
                "options": {"num_predict": 200, "temperature": 0.1}
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
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    # ── Maus-Ereignisse ─────────────────────────

    def mousePressEvent(self, event):
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
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self._drag_orig  = (self._dx, self._dy)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            delta = event.pos() - self._drag_start
            self._dx = self._drag_orig[0] + delta.x()
            self._dy = self._drag_orig[1] + delta.y()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None

    def wheelEvent(self, event):
        steps  = event.angleDelta().y() / 120
        factor = 1.1 ** steps
        self._scale = max(0.05, min(self._scale * factor, 20.0))
        self._update_pixmap()
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            # Overlay-Verschiebung → Bildkoordinaten
            new_x = int(self._ox + self._dx / self._zoom)
            new_y = int(self._oy + self._dy / self._zoom)
            self.transform_done.emit(new_x, new_y, self._scale)
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
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
        for name, h in self._handles().items():
            if h.contains(pos): return name
        return None

    # ── Events ────────────────────────────────────────────────

    def mousePressEvent(self, event):
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
        self._mode = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirmed.emit(self._rect)
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
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
        return self._poly_path().contains(QPointF(pos.x(), pos.y()))

    def mousePressEvent(self, event):
        if self._inside(event.pos()):
            self._drag_start = event.pos()
            self._drag_orig  = QPoint(self._offset)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            d = event.pos() - self._drag_start
            self._offset = self._drag_orig + d
            self.update()
        else:
            cursor = Qt.CursorShape.SizeAllCursor if self._inside(event.pos()) \
                     else Qt.CursorShape.ArrowCursor
            self.setCursor(QCursor(cursor))

    def mouseReleaseEvent(self, _event):
        self._drag_start = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            ox, oy = self._offset.x(), self._offset.y()
            self.confirmed.emit([QPoint(p.x()+ox, p.y()+oy) for p in self._pts])
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def paintEvent(self, _event):
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
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit(); self.close()
        elif event.key() == Qt.Key.Key_Return:
            self.selection_ready.emit(self._mask); self.close()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        ix = max(0, min(int(event.pos().x() / self._zoom), self._pil.width  - 1))
        iy = max(0, min(int(event.pos().y() / self._zoom), self._pil.height - 1))
        add = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        new_mask = self._flood_fill(ix, iy)
        if add:
            # Additive Auswahl: Vereinigung beider Masken
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

class HistogramWidget(QWidget):
    """
    Zeigt die Rot/Grün/Blau-Verteilung des aktuellen Bildes als Balkendiagramm.

    FUNKTIONSWEISE:
    PIL's image.histogram() liefert 768 Werte: 256 je Kanal (R, G, B).
    Aus Performancegründen wird das Bild vorher auf max. 256×256 px skaliert.
    QPainter zeichnet für jeden der 256 Helligkeitswerte einen senkrechten Strich.

    Typisch für professionelle Editoren wie Lightroom oder Capture One.
    Erklärt beim Prüfungsgespräch: Überbelichtung (Häufung rechts),
    Unterbelichtung (Häufung links), Farbstiche (Kanal-Ungleichgewicht).
    """
    def __init__(self):
        super().__init__()
        self._data = None   # tuple (r_list, g_list, b_list, max_val)
        self.setFixedHeight(90)
        self.setMinimumWidth(200)
        self.setStyleSheet(
            "background:#141414; border:1px solid #2a2a2a; border-radius:4px;")
        self.setToolTip(
            "Histogramm: Rot / Grün / Blau Verteilung\n"
            "Links = dunkle Pixel, Rechts = helle Pixel")

    def update_histogram(self, pil_image):
        """Berechnet das Histogramm aus dem PIL-Bild und triggert Neuzeichnen."""
        if pil_image is None:
            self._data = None
            self.update()
            return
        try:
            thumb = pil_image.copy().convert("RGB")
            thumb.thumbnail((256, 256))
            hist = thumb.histogram()   # 768 Werte: R×256 + G×256 + B×256
            r = hist[0:256]
            g = hist[256:512]
            b = hist[512:768]
            max_val = max(max(r), max(g), max(b)) or 1
            self._data = (r, g, b, max_val)
        except Exception:
            self._data = None
        self.update()

    def paintEvent(self, event):
        """
        Zeichnet R/G/B als übereinanderliegende gefüllte Kurven (QPainterPath).

        REIHENFOLGE: Blau zuerst (hinten), dann Grün, dann Rot (vorne).
        Überlappende Bereiche zeigen die vorderste Farbe.
        Gefüllte Flächen (fillPath) sind zuverlässiger sichtbar als dünne Linien.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(14, 14, 14))

        if not self._data:
            painter.setPen(QPen(QColor(80, 80, 80)))
            painter.setFont(QFont("Arial", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Kein Bild geladen")
            painter.end()
            return

        r_data, g_data, b_data, max_val = self._data
        w, h = self.width(), self.height()
        usable_h = h - 6   # kleiner Puffer unten

        # Blau → Grün → Rot (Rot liegt oben = am deutlichsten sichtbar)
        for data, fill_color in [
            (b_data, QColor(50,  110, 240, 170)),
            (g_data, QColor(50,  200, 80,  170)),
            (r_data, QColor(230, 60,  60,  200)),
        ]:
            path = QPainterPath()
            path.moveTo(0, h)
            for i in range(256):
                x  = i / 255.0 * w
                bh = data[i] / max_val * usable_h
                path.lineTo(x, h - bh)
            path.lineTo(w, h)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(path, QBrush(fill_color))

        # Kanal-Labels oben links
        painter.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        for txt, col, xp in [("R", QColor(255, 100, 100), 4),
                              ("G", QColor(80,  220, 80),  14),
                              ("B", QColor(100, 150, 255), 24)]:
            painter.setPen(QPen(col))
            painter.drawText(xp, 12, txt)
        painter.end()


# ══════════════════════════════════════════════════════════════
#  EBENEN-PANEL: Liste aller Ebenen mit Steuerung
# ══════════════════════════════════════════════════════════════

class LayerPanel(QWidget):
    """
    Zeigt alle Ebenen des Editors als interaktive Liste.

    Reihenfolge: Oberste Ebene (zuletzt hinzugefügt) oben in der Liste,
    Hintergrund unten — wie in GIMP und Photoshop.

    Pro Ebene:
      👁 Sichtbarkeit  |  Thumbnail  |  Name  |  Deckkraft %  |  🗑 Löschen

    Buttons oben:
      + Neu  |  + Transparent (mit Größenwahl)  |  ⬇ Merge  |  Flatten
    """

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(4, 4, 4, 4)
        main.setSpacing(4)

        # ── Button-Leiste
        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)
        _bs = ("background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
               "border-radius:3px; padding:4px 6px; font-size:10px;")
        for label, slot in [
            ("+ Neu",          self._add_layer),
            ("+ Transparent",  self._add_transparent),
            ("⬇ Merge",        self._merge_down),
            ("Flatten",        self._flatten_all),
            ("🔀 Transform",   self._transform_active),
        ]:
            b = QPushButton(label)
            b.setStyleSheet(_bs)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        main.addLayout(btn_row)

        # ── Ebenen-Liste (scrollbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background:#111; border:none;")
        self._list_w = QWidget()
        self._list_w.setStyleSheet("background:#111;")
        self._list_layout = QVBoxLayout(self._list_w)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(3)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_w)
        main.addWidget(scroll, 1)

    # ── Öffentliche Methode: Liste neu aufbauen ──────────────

    def refresh(self):
        """Alle Zeilen entfernen und neu aus editor.layers aufbauen."""
        # Alle Widgets außer dem abschließenden Stretch löschen
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.deleteLater()

        editor = self.editor
        # Ebenen in umgekehrter Reihenfolge anzeigen (oben = zuletzt hinzugefügt)
        for i in reversed(range(len(editor.layers))):
            row = self._make_row(i, editor.layers[i], i == editor.active_layer_idx)
            self._list_layout.insertWidget(0, row)

    def _make_row(self, idx: int, layer, active: bool) -> QWidget:
        """Erzeugt eine Ebenen-Zeile."""
        row = QWidget()
        row.setFixedHeight(46)
        active_style = ("background:#1a3a5a; border:1px solid #4fc3f7; border-radius:3px;")
        idle_style   = ("background:#222; border:1px solid #333; border-radius:3px;")
        row.setStyleSheet(active_style if active else idle_style)

        hl = QHBoxLayout(row)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        # Sichtbarkeit
        vis = QPushButton("👁" if layer.visible else "🚫")
        vis.setFixedSize(26, 26)
        vis.setStyleSheet("background:#2a2a2a; border:1px solid #333; border-radius:3px;")
        vis.setToolTip("Ebene ein-/ausblenden")
        vis.clicked.connect(lambda _, i=idx: self._toggle_vis(i))
        hl.addWidget(vis)

        # Thumbnail
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(40, 32)
        thumb_lbl.setStyleSheet("background:#111; border:1px solid #2a2a2a;")
        if layer.image:
            t = layer.image.copy()
            t.thumbnail((40, 32))
            thumb_lbl.setPixmap(pil_to_qpixmap(t).scaled(
                40, 32,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        thumb_lbl.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        thumb_lbl.mousePressEvent = lambda ev, i=idx: self._select_and_transform(i)
        hl.addWidget(thumb_lbl)

        # Name
        name_lbl = QLabel(layer.name)
        name_lbl.setStyleSheet("color:#ddd; font-size:10px;")
        name_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        name_lbl.mousePressEvent = lambda ev, i=idx: self._select(i)
        hl.addWidget(name_lbl, 1)

        # Deckkraft
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setValue(layer.opacity)
        spin.setSuffix("%")
        spin.setFixedWidth(58)
        spin.setStyleSheet("background:#2d2d2d; color:#ddd; border:1px solid #333; padding:1px;")
        spin.setToolTip("Deckkraft (Opacity)")
        spin.valueChanged.connect(lambda v, i=idx: self._set_opacity(i, v))
        hl.addWidget(spin)

        # Löschen
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("background:#3a1a1a; border:1px solid #5a2a2a; border-radius:3px;")
        del_btn.setToolTip("Ebene löschen")
        del_btn.clicked.connect(lambda _, i=idx: self._delete(i))
        hl.addWidget(del_btn)

        # Rechtsklick-Kontextmenü auf der gesamten Zeile
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(
            lambda pos, i=idx: self._show_context_menu(i))

        return row

    # ── Kontextmenü (Rechtsklick auf Ebene) ──────────────────

    def _show_context_menu(self, idx: int):
        """Rechtsklick-Menü für eine Ebenen-Zeile."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#252525; color:#ddd; border:1px solid #444; }
            QMenu::item { padding:6px 20px; }
            QMenu::item:selected { background:#0e639c; }
        """)
        menu.addAction("🖼  Bild in diese Ebene laden…",
                       lambda: self._load_image_into(idx))
        menu.addAction("✏️  Umbenennen…",
                       lambda: self._rename(idx))
        menu.addSeparator()
        menu.addAction("⬆  Nach oben verschieben",
                       lambda: self._move_layer(idx, +1))
        menu.addAction("⬇  Nach unten verschieben",
                       lambda: self._move_layer(idx, -1))
        menu.addSeparator()
        menu.addAction("🗑  Ebene löschen",
                       lambda: self._delete(idx))
        menu.exec(self.cursor().pos())

    def _load_image_into(self, idx: int):
        """Bild in eine bestehende Ebene laden (ersetzt deren Inhalt)."""
        path, _ = QFileDialog.getOpenFileName(self, "Bild laden", "",
            "Bilder (*.png *.jpg *.jpeg *.bmp *.webp);;Alle (*)")
        if not path:
            return
        try:
            img = PILImage.open(path).convert("RGBA")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Fehler", str(e))
            return
        ed = self.editor
        ed._push()
        ed.layers[idx].image = img
        ed.original_pil      = img.copy()
        ed.active_layer_idx  = idx
        ed._update_display()
        self.refresh()

    def _rename(self, idx: int):
        """Ebene umbenennen."""
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Umbenennen", "Neuer Name:",
            text=self.editor.layers[idx].name)
        if ok and name.strip():
            self.editor.layers[idx].name = name.strip()
            self.refresh()

    def _move_layer(self, idx: int, direction: int):
        """Ebene in der Reihenfolge nach oben (+1) oder unten (-1) verschieben."""
        ed    = self.editor
        new_i = idx + direction
        if new_i < 0 or new_i >= len(ed.layers):
            return
        ed._push()
        ed.layers[idx], ed.layers[new_i] = ed.layers[new_i], ed.layers[idx]
        ed.active_layer_idx = new_i
        ed._update_display()
        self.refresh()

    # ── Aktionen ─────────────────────────────────────────────

    def _select_and_transform(self, idx: int):
        """Thumbnail-Klick: Ebene aktivieren UND sofort Transform-Overlay starten."""
        ed = self.editor
        ed.active_layer_idx = idx
        if ed.layers[idx].image:
            ed.original_pil = ed.layers[idx].image.copy()
        ed._reset_sliders()
        self.refresh()
        ed._start_layer_transform(idx)

    def _select(self, idx: int):
        ed = self.editor
        ed.active_layer_idx = idx
        if ed.layers[idx].image:
            ed.original_pil = ed.layers[idx].image.copy()
        ed._reset_sliders()
        self.refresh()

    def _toggle_vis(self, idx: int):
        self.editor.layers[idx].visible = not self.editor.layers[idx].visible
        self.editor._update_display()
        self.refresh()

    def _set_opacity(self, idx: int, value: int):
        self.editor.layers[idx].opacity = value
        self.editor._update_display()

    def _add_layer(self):
        """Neue Ebene hinzufügen — mit optionalem Bild-Ladefilm."""
        ed = self.editor
        if not ed.layers:
            return

        # Dialog: Leere Ebene oder Bild laden?
        dlg = QDialog(self)
        dlg.setWindowTitle("Neue Ebene")
        dlg.setStyleSheet("background:#1a1a1a; color:#ddd;")
        layout = QVBoxLayout(dlg)
        info = QLabel("Was soll die neue Ebene enthalten?")
        info.setStyleSheet("font-size:12px; padding:4px;")
        layout.addWidget(info)
        btn_empty = QPushButton("🗋  Leere transparente Ebene")
        btn_image = QPushButton("🖼  Bild laden…")
        for b in (btn_empty, btn_image):
            b.setStyleSheet("background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                            "border-radius:4px; padding:8px; font-size:12px;")
        btn_empty.clicked.connect(lambda: dlg.done(1))
        btn_image.clicked.connect(lambda: dlg.done(2))
        layout.addWidget(btn_empty)
        layout.addWidget(btn_image)
        choice = dlg.exec()

        base = ed.layers[0].image
        if choice == 1:
            new_img = PILImage.new("RGBA", (base.width, base.height), (0, 0, 0, 0))
        elif choice == 2:
            path, _ = QFileDialog.getOpenFileName(self, "Bild für neue Ebene", "",
                "Bilder (*.png *.jpg *.jpeg *.bmp *.webp);;Alle (*)")
            if not path:
                return
            try:
                new_img = PILImage.open(path).convert("RGBA")
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Fehler", str(e))
                return
        else:
            return

        ed._push()
        ed.layers.append(Layer(new_img))
        ed.active_layer_idx = len(ed.layers) - 1
        ed.original_pil = new_img.copy()
        ed._update_display()
        self.refresh()

    def _add_transparent(self):
        ed = self.editor
        dlg = QDialog(self)
        dlg.setWindowTitle("Transparente Ebene")
        dlg.setStyleSheet("background:#1a1a1a; color:#ddd;")
        form = QVBoxLayout(dlg)
        row_w = QHBoxLayout(); row_w.addWidget(QLabel("Breite px:"))
        sp_w = QSpinBox(); sp_w.setRange(1, 8000)
        sp_w.setValue(ed.layers[0].image.width if ed.layers else 800)
        row_w.addWidget(sp_w); form.addLayout(row_w)
        row_h = QHBoxLayout(); row_h.addWidget(QLabel("Höhe  px:"))
        sp_h = QSpinBox(); sp_h.setRange(1, 6000)
        sp_h.setValue(ed.layers[0].image.height if ed.layers else 600)
        row_h.addWidget(sp_h); form.addLayout(row_h)
        row_n = QHBoxLayout(); row_n.addWidget(QLabel("Name:"))
        le_n = QLineEdit("Transparente Ebene"); row_n.addWidget(le_n)
        form.addLayout(row_n)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_img = PILImage.new("RGBA", (sp_w.value(), sp_h.value()), (0, 0, 0, 0))
        ed._push()
        ed.layers.append(Layer(new_img, le_n.text() or "Transparente Ebene"))
        ed.active_layer_idx = len(ed.layers) - 1
        ed.original_pil = new_img.copy()
        ed._update_display()
        self.refresh()

    def _delete(self, idx: int):
        ed = self.editor
        if len(ed.layers) <= 1:
            QMessageBox.information(self, "Info",
                "Mindestens eine Ebene muss vorhanden sein.")
            return
        ed._push()
        ed.layers.pop(idx)
        ed.active_layer_idx = min(ed.active_layer_idx, len(ed.layers) - 1)
        ed.original_pil = ed.layers[ed.active_layer_idx].image.copy()
        ed._update_display()
        self.refresh()

    def _merge_down(self):
        """Aktive Ebene mit der direkt darunter liegenden zusammenführen."""
        ed  = self.editor
        idx = ed.active_layer_idx
        if idx == 0:
            QMessageBox.information(self, "Info",
                "Unterste Ebene — kein Merge Down möglich."); return
        ed._push()
        top    = ed.layers[idx]
        bottom = ed.layers[idx - 1]
        w = max(top.x + top.image.width,  bottom.x + bottom.image.width)
        h = max(top.y + top.image.height, bottom.y + bottom.image.height)
        merged = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        for lyr in (bottom, top):
            img = lyr.image.copy()
            if lyr.opacity < 100:
                r, g, b, a = img.split()
                a = a.point([int(v * lyr.opacity / 100) for v in range(256)])
                img = PILImage.merge("RGBA", (r, g, b, a))
            merged.paste(img, (lyr.x, lyr.y), img)
        bottom.image   = merged
        bottom.opacity = 100
        ed.layers.pop(idx)
        ed.active_layer_idx = idx - 1
        ed.original_pil = bottom.image.copy()
        ed._update_display()
        self.refresh()

    def _flatten_all(self):
        """Alle Ebenen zu einer einzigen zusammenführen."""
        ed = self.editor
        ed._push()
        flat = ed._composite_layers()
        Layer._counter = 0
        ed.layers = [Layer(flat, "Hintergrund")]
        ed.active_layer_idx = 0
        ed.original_pil = flat.copy()
        ed._update_display()
        self.refresh()

    def _transform_active(self):
        """Transform-Overlay für die aktive Ebene starten."""
        self.editor._start_layer_transform(self.editor.active_layer_idx)


# ══════════════════════════════════════════════════════════════
#  FILTER-VORSCHAU-DIALOG: 5×4 Thumbnail-Grid aller Filter
# ══════════════════════════════════════════════════════════════

class FilterPreviewDialog(QDialog):
    """
    Zeigt alle verfügbaren Filter als 100px-Thumbnails in einem 5×4-Grid.
    Klick auf ein Thumbnail → Dialog schließt, Filter wird vom Aufrufer angewendet.

    Die Thumbnails werden von ImageEditor._generate_filter_previews() vorberechnet
    und als dict {filtername: QPixmap} übergeben.
    """
    def __init__(self, parent, previews: dict):
        super().__init__(parent)
        self.setWindowTitle("🖼  Filter-Vorschau — alle Filter auf einen Blick")
        self.setModal(True)
        self.resize(730, 560)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self.chosen_filter = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("Klick auf ein Thumbnail zum sofortigen Anwenden  |  ESC = Abbrechen")
        title.setStyleSheet(
            "color:#4fc3f7; font-weight:bold; font-size:12px; padding:6px 0;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background:#1a1a1a; border:none;")

        grid_widget = QWidget()
        grid_widget.setStyleSheet("background:#1a1a1a;")
        grid = QGridLayout(grid_widget)
        grid.setSpacing(8)
        grid.setContentsMargins(8, 8, 8, 8)

        COLS = 5
        THUMB_W, THUMB_H = 118, 90
        CELL_W, CELL_H   = 128, 116

        for idx, (name, pix) in enumerate(previews.items()):
            row, col = divmod(idx, COLS)

            cell = QWidget()
            cell.setFixedSize(CELL_W, CELL_H)
            cell.setStyleSheet("""
                QWidget { background:#252525; border:1px solid #333; border-radius:5px; }
                QWidget:hover { border:1px solid #4fc3f7; background:#2d2d2d; }
            """)
            cell.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

            cl = QVBoxLayout(cell)
            cl.setContentsMargins(4, 4, 4, 4)
            cl.setSpacing(3)

            img_lbl = QLabel()
            img_lbl.setPixmap(pix.scaled(
                THUMB_W, THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # Kurzname: Emoji + Text ohne führende Leerzeichen
            short = name.strip().lstrip("─ ")
            if len(short) > 22:
                short = short[:22] + "…"
            name_lbl = QLabel(short)
            name_lbl.setStyleSheet("color:#aaa; font-size:9px;")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            cl.addWidget(img_lbl)
            cl.addWidget(name_lbl)

            filter_name = name
            cell.mousePressEvent = lambda a0, n=filter_name: self._select(n)

            grid.addWidget(cell, row, col)

        scroll.setWidget(grid_widget)
        layout.addWidget(scroll)

        btn_cancel = QPushButton("Abbrechen")
        btn_cancel.setStyleSheet(
            "background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
            "border-radius:4px; padding:7px 20px;")
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_cancel, alignment=Qt.AlignmentFlag.AlignRight)

    def _select(self, name: str):
        self.chosen_filter = name
        self.accept()


# ══════════════════════════════════════════════════════════════
#  COLLAGE-FILTER: Standalone PIL-Transforms für den Collage-Editor
#  Unabhängig von ImageEditor – direkt auf PIL-Images anwendbar.
# ══════════════════════════════════════════════════════════════

def _cf_sepia(img):
    g = ImageOps.grayscale(img.convert("RGB"))
    return PILImage.merge("RGB", (
        g.point([min(255, int(x * 1.1)) for x in range(256)]),
        g.point([min(255, int(x * 0.9)) for x in range(256)]),
        g.point([min(255, int(x * 0.7)) for x in range(256)]),
    )).convert("RGBA")

def _cf_cool(img):
    r, g, b, *a = img.convert("RGBA").split()
    alpha = a[0] if a else PILImage.new("L", img.size, 255)
    return PILImage.merge("RGBA", (
        r.point([max(0, x - 20) for x in range(256)]), g,
        b.point([min(255, x + 30) for x in range(256)]), alpha))

def _cf_warm(img):
    r, g, b, *a = img.convert("RGBA").split()
    alpha = a[0] if a else PILImage.new("L", img.size, 255)
    return PILImage.merge("RGBA", (
        r.point([min(255, x + 30) for x in range(256)]), g,
        b.point([max(0, x - 20) for x in range(256)]), alpha))

def _cf_psychedelic(img):
    r, g, b, *a = img.convert("RGBA").split()
    alpha = a[0] if a else PILImage.new("L", img.size, 255)
    result = PILImage.merge("RGBA", (g, b, r, alpha))
    return ImageEnhance.Color(result).enhance(3.0)

def _cf_kaleidoscope(img):
    img = img.convert("RGBA")
    w, h = img.size
    hf_w = w // 2
    left = img.crop((0, 0, hf_w, h))
    top  = PILImage.new("RGBA", (w, h))
    top.paste(left, (0, 0))
    top.paste(ImageOps.mirror(left), (hf_w, 0))
    hf_h     = h // 2
    top_half = top.crop((0, 0, w, hf_h))
    result   = PILImage.new("RGBA", (w, h))
    result.paste(top_half, (0, 0))
    result.paste(ImageOps.flip(top_half), (0, hf_h))
    return result

# Reihenfolge bestimmt die Einträge im Dropdown
COLLAGE_FILTER_FNS = {
    "⬛ Schwarz/Weiß":  lambda i: ImageOps.grayscale(i.convert("RGB")).convert("RGBA"),
    "🌅 Sepia":          _cf_sepia,
    "🔵 Kühler Ton":     _cf_cool,
    "🔴 Warmer Ton":     _cf_warm,
    "🎨 Invertieren":    lambda i: ImageOps.invert(i.convert("RGB")).convert("RGBA"),
    "💡 Schärfen":       lambda i: i.convert("RGBA").filter(ImageFilter.SHARPEN),
    "🌫 Weichzeichnen":  lambda i: i.convert("RGBA").filter(ImageFilter.GaussianBlur(2)),
    "✨ Emboss":         lambda i: i.filter(ImageFilter.EMBOSS).convert("RGBA"),
    "🔆 Auto-Kontrast":  lambda i: ImageOps.autocontrast(i.convert("RGB")).convert("RGBA"),
    "🌈 Psychedelic":    _cf_psychedelic,
    "🔮 Kaleidoskop":    _cf_kaleidoscope,
}


# ══════════════════════════════════════════════════════════════
#  GIF-EDITOR: Animierte GIFs aus Bildern erstellen
# ══════════════════════════════════════════════════════════════

class GifEditorDialog(QDialog):
    """
    Erstellt animierte GIFs mit vier Animations-Modi:

    a) 📼 VHS-Distortion-Loop  – flimmernder Bildschirm aus den 80ern
    b) ⭐ Sternschauer          – goldene Sterne fallen von einem Punkt
    c) 🎬 Pfad-Animation        – Ebene bewegt sich entlang eines Pfades
    d) 🌊 Parallax-GIF          – Ebenen schwingen mit Tiefen-Versatz (3D-Effekt)
    """

    _PW, _PH = 480, 320        # Vorschau-Größe in Pixeln
    _GOLD    = (255, 210, 0, 255)

    def __init__(self, parent, editor):
        super().__init__(parent)
        self.editor       = editor
        self.frames: list = []
        self._star_origin: tuple | None = None
        self._path_pts:    list         = []          # QPoint-Liste (Bildkoords)
        self._base_pil                  = None        # Composite-Bild
        self._sound_path:  str | None   = None        # Geladene Sound-Datei

        self.setWindowTitle("🎞  GIF-Editor")
        self.setModal(True)
        self.resize(980, 680)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self._build_ui()
        self._refresh_base()

    # ── UI-Aufbau ────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(10)

        # ── Linke Seite: Steuerung ────────────────────────────
        ctrl_w = QWidget(); ctrl_w.setFixedWidth(310)
        ctrl   = QVBoxLayout(ctrl_w); ctrl.setSpacing(8)

        # Modus-Auswahl
        mode_box = QGroupBox("Animation-Typ")
        mode_box.setStyleSheet(
            "QGroupBox { color:#4fc3f7; border:1px solid #333; border-radius:4px; "
            "margin-top:8px; padding:6px; } QGroupBox::title { left:8px; }")
        mb = QVBoxLayout(mode_box)
        rb_s = ("QRadioButton { color:#ddd; padding:4px; } "
                "QRadioButton::indicator { width:14px; height:14px; }")
        self._rb_vhs      = QRadioButton("a) 📼  VHS-Distortion-Loop");  self._rb_vhs.setStyleSheet(rb_s)
        self._rb_star     = QRadioButton("b) ⭐  Sternschauer");          self._rb_star.setStyleSheet(rb_s)
        self._rb_path     = QRadioButton("c) 🎬  Pfad-Animation");        self._rb_path.setStyleSheet(rb_s)
        self._rb_parallax = QRadioButton("d) 🌊  Parallax-GIF");          self._rb_parallax.setStyleSheet(rb_s)
        self._rb_vhs.setChecked(True)
        for rb in (self._rb_vhs, self._rb_star, self._rb_path, self._rb_parallax): mb.addWidget(rb)
        ctrl.addWidget(mode_box)

        # Parameter-Stack
        self._stack = QStackedWidget()
        self._stack.addWidget(self._panel_vhs())
        self._stack.addWidget(self._panel_star())
        self._stack.addWidget(self._panel_path())
        self._stack.addWidget(self._panel_parallax())
        self._rb_vhs.toggled.connect(     lambda c: self._stack.setCurrentIndex(0) if c else None)
        self._rb_star.toggled.connect(    lambda c: self._stack.setCurrentIndex(1) if c else None)
        self._rb_path.toggled.connect(    lambda c: self._stack.setCurrentIndex(2) if c else None)
        self._rb_parallax.toggled.connect(lambda c: self._stack.setCurrentIndex(3) if c else None)
        ctrl.addWidget(self._stack)

        # GIF-Einstellungen
        gif_box = QGroupBox("GIF-Einstellungen")
        gif_box.setStyleSheet(mode_box.styleSheet())
        gb = QVBoxLayout(gif_box)
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; padding:2px;"
        def spin_row(lbl, lo, hi, val):
            row = QHBoxLayout()
            row.addWidget(QLabel(lbl))
            sp = QSpinBox(); sp.setRange(lo, hi); sp.setValue(val); sp.setStyleSheet(sp_s)
            row.addWidget(sp); return row, sp
        r1, self._sp_frames = spin_row("Frames:", 4, 60, 12)
        r2, self._sp_delay  = spin_row("Verzögerung ms:", 50, 2000, 120)
        gb.addLayout(r1); gb.addLayout(r2)
        ctrl.addWidget(gif_box)

        # Aktions-Buttons
        _btn = ("QPushButton { background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                "border-radius:4px; padding:8px; font-size:12px; } "
                "QPushButton:hover { background:#3a3a3a; }")
        btn_gen = QPushButton("▶  GIF generieren")
        btn_gen.setStyleSheet(
            "QPushButton { background:#1a3a1a; color:#90ee90; border:1px solid #2a5a2a; "
            "border-radius:4px; padding:8px; font-size:12px; font-weight:bold; } "
            "QPushButton:hover { background:#2a4a2a; }")
        btn_gen.clicked.connect(self._generate)
        btn_exp = QPushButton("💾  Als GIF exportieren…"); btn_exp.setStyleSheet(_btn)
        btn_exp.clicked.connect(self._export)
        btn_vid = QPushButton("🎬  Als Video (MP4) exportieren…"); btn_vid.setStyleSheet(_btn)
        btn_vid.clicked.connect(self._export_video)
        ctrl.addWidget(btn_gen); ctrl.addWidget(btn_exp); ctrl.addWidget(btn_vid)

        # ── Sound ──────────────────────────────────────────────
        snd_box = QGroupBox("🎵  Sound (optional)")
        snd_box.setStyleSheet(
            "QGroupBox { color:#4fc3f7; border:1px solid #333; border-radius:4px; "
            "margin-top:8px; padding:6px; } QGroupBox::title { left:8px; }")
        sb = QVBoxLayout(snd_box)
        snd_info = QLabel("Sound wird beim Export auf GIF-Dauer\n"
                          "zugeschnitten und als .wav gespeichert.")
        snd_info.setStyleSheet("color:#888; font-size:10px;")
        snd_info.setWordWrap(True)
        sb.addWidget(snd_info)
        btn_snd = QPushButton("🎵  Sound laden…"); btn_snd.setStyleSheet(_btn)
        btn_snd.clicked.connect(self._load_sound)
        self._lbl_sound = QLabel("Kein Sound geladen")
        self._lbl_sound.setStyleSheet("color:#4fc3f7; font-size:10px;")
        self._lbl_sound.setWordWrap(True)
        sb.addWidget(btn_snd); sb.addWidget(self._lbl_sound)
        ctrl.addWidget(snd_box)
        ctrl.addStretch()

        # ── Rechte Seite: Vorschau ─────────────────────────────
        prev_w = QWidget()
        pv = QVBoxLayout(prev_w); pv.setSpacing(6)
        pv.addWidget(QLabel("Vorschau  (Klick = Startpunkt/Pfadpunkt):"))

        self._prev_lbl = QLabel()
        self._prev_lbl.setFixedSize(self._PW, self._PH)
        self._prev_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._prev_lbl.setStyleSheet("background:#0a0a0a; border:1px solid #333;")
        self._prev_lbl.mousePressEvent = lambda ev: self._on_preview_click(ev)
        pv.addWidget(self._prev_lbl)

        nav = QHBoxLayout()
        self._btn_pf = QPushButton("◀"); self._btn_nf = QPushButton("▶")
        self._lbl_fi = QLabel("—"); self._lbl_fi.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for b in (self._btn_pf, self._btn_nf):
            b.setFixedWidth(36)
            b.setStyleSheet("background:#2d2d2d; color:#ccc; border:1px solid #333; "
                            "border-radius:4px; padding:4px;")
        self._cur_fi = 0
        self._btn_pf.clicked.connect(lambda: self._show_frame(self._cur_fi - 1))
        self._btn_nf.clicked.connect(lambda: self._show_frame(self._cur_fi + 1))
        nav.addWidget(self._btn_pf); nav.addWidget(self._lbl_fi, 1); nav.addWidget(self._btn_nf)
        pv.addLayout(nav); pv.addStretch()

        root.addWidget(ctrl_w); root.addWidget(prev_w, 1)

    # ── Parameter-Panels ─────────────────────────────────────

    def _panel_vhs(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        info = QLabel("Bild wechselt zwischen Normal und Verzerrt.\n"
                      "Intensität steuert Stärke der Störungen.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#888; font-size:10px; background:#141414; "
                           "border-radius:3px; padding:6px;")
        l.addWidget(info)
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; padding:2px;"
        row = QHBoxLayout(); row.addWidget(QLabel("Intensität (1–10):"))
        self._sp_vhs_int = QSpinBox(); self._sp_vhs_int.setRange(1, 10)
        self._sp_vhs_int.setValue(5); self._sp_vhs_int.setStyleSheet(sp_s)
        row.addWidget(self._sp_vhs_int); l.addLayout(row); l.addStretch()
        return w

    def _panel_star(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        info = QLabel("Goldene Sterne fallen vom gewählten Punkt.\n"
                      "Klick auf Vorschau = Startpunkt festlegen.\n"
                      "Anzahl und Größe variieren zufällig.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#888; font-size:10px; background:#141414; "
                           "border-radius:3px; padding:6px;")
        l.addWidget(info)
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; padding:2px;"
        self._lbl_star_pt = QLabel("Startpunkt: (noch nicht gesetzt)")
        self._lbl_star_pt.setStyleSheet("color:#4fc3f7; font-size:10px;")
        l.addWidget(self._lbl_star_pt)

        def row2(a, lo, hi, va, b, lo2, hi2, vb):
            r = QHBoxLayout()
            r.addWidget(QLabel(a))
            sa = QSpinBox(); sa.setRange(lo, hi); sa.setValue(va); sa.setStyleSheet(sp_s)
            r.addWidget(sa)
            r.addWidget(QLabel(b))
            sb = QSpinBox(); sb.setRange(lo2, hi2); sb.setValue(vb); sb.setStyleSheet(sp_s)
            r.addWidget(sb)
            return r, sa, sb

        r1, self._sp_s_min, self._sp_s_max = row2("Sterne Min:", 2, 20, 9, "Max:", 2, 30, 13)
        r2, self._sp_sz_min, self._sp_sz_max = row2("Größe Min px:", 8, 60, 10, "Max:", 8, 100, 30)
        l.addLayout(r1); l.addLayout(r2); l.addStretch()
        return w

    def _panel_path(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        info = QLabel("Ebene bewegt sich entlang eines Pfades.\n"
                      "Klick auf Vorschau = Wegpunkte hinzufügen.\n"
                      "Objekt rotiert mit Kurven-Steigung.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#888; font-size:10px; background:#141414; "
                           "border-radius:3px; padding:6px;")
        l.addWidget(info)
        self._lbl_path = QLabel("Pfad: 0 Punkte")
        self._lbl_path.setStyleSheet("color:#4fc3f7; font-size:10px;")
        l.addWidget(self._lbl_path)
        btn_clr = QPushButton("✕ Pfad löschen")
        btn_clr.setStyleSheet("background:#3a1a1a; color:#f99; border:1px solid #5a2a2a; "
                              "border-radius:3px; padding:4px;")
        btn_clr.clicked.connect(self._clear_path)
        l.addWidget(btn_clr)
        l.addWidget(QLabel("Zu animierende Ebene:"))
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; "
        self._cb_layer = QComboBox(); self._cb_layer.setStyleSheet(sp_s + "border-radius:4px; padding:4px;")
        for i, lyr in enumerate(self.editor.layers):
            self._cb_layer.addItem(f"Ebene {i+1}: {lyr.name}", i)
        self._cb_layer.setCurrentIndex(min(1, len(self.editor.layers)-1))
        l.addWidget(self._cb_layer); l.addStretch()
        return w

    def _panel_parallax(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        info = QLabel("Jede Ebene schwingt mit unterschiedlicher\n"
                      "Amplitude – tiefere Ebenen bewegen sich\n"
                      "weniger (Tiefenparallax-Effekt).")
        info.setWordWrap(True)
        info.setStyleSheet("color:#888; font-size:10px; background:#141414; "
                           "border-radius:3px; padding:6px;")
        l.addWidget(info)
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; padding:2px;"
        def spin_row(lbl, lo, hi, val):
            row = QHBoxLayout()
            lw = QLabel(lbl); lw.setStyleSheet("color:#ddd; font-size:10px;")
            row.addWidget(lw)
            sp = QSpinBox(); sp.setRange(lo, hi); sp.setValue(val); sp.setStyleSheet(sp_s)
            row.addWidget(sp); return row, sp
        r1, self._sp_par_amp   = spin_row("Max. Amplitude (px):", 2, 80, 20)
        r2, self._sp_par_swing = spin_row("Schwingungen:", 1, 8, 2)
        l.addLayout(r1); l.addLayout(r2)
        horiz_lbl = QLabel("Richtung:")
        horiz_lbl.setStyleSheet("color:#ddd; font-size:10px;")
        l.addWidget(horiz_lbl)
        rb_s2 = "QRadioButton { color:#ddd; font-size:10px; } QRadioButton::indicator { width:12px; height:12px; }"
        self._rb_par_h = QRadioButton("Horizontal"); self._rb_par_h.setStyleSheet(rb_s2); self._rb_par_h.setChecked(True)
        self._rb_par_v = QRadioButton("Vertikal");   self._rb_par_v.setStyleSheet(rb_s2)
        l.addWidget(self._rb_par_h); l.addWidget(self._rb_par_v)
        l.addStretch()
        return w

    # ── Vorschau ─────────────────────────────────────────────

    def _refresh_base(self):
        self._base_pil = self.editor._composite_layers()
        self._draw_preview_overlay()

    def _draw_preview_overlay(self):
        """Basis + Markierungen (Startpunkt, Pfad) in Vorschau zeichnen."""
        if not self._base_pil: return
        base  = self._base_pil
        thumb = base.copy(); thumb.thumbnail((self._PW, self._PH))
        pix   = pil_to_qpixmap(thumb).scaled(
            self._PW, self._PH,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        # Offsets (KeepAspectRatio → Bild nicht zwingend ganz links/oben)
        ox = (self._PW - pix.width())  // 2
        oy = (self._PH - pix.height()) // 2
        self._thumb_ox, self._thumb_oy  = ox, oy
        self._thumb_w,  self._thumb_h   = thumb.width, thumb.height

        final = QPixmap(self._PW, self._PH)
        final.fill(QColor("#0a0a0a"))
        painter = QPainter(final)
        painter.drawPixmap(ox, oy, pix)

        def img2px(ix, iy):
            return (ox + int(ix * pix.width()  / base.width),
                    oy + int(iy * pix.height() / base.height))

        if self._star_origin:
            sx, sy = img2px(*self._star_origin)
            painter.setPen(QPen(QColor("#f0a030"), 2))
            painter.drawEllipse(QPoint(sx, sy), 8, 8)
            painter.drawLine(sx-12, sy, sx+12, sy)
            painter.drawLine(sx, sy-12, sx, sy+12)

        if self._path_pts:
            painter.setPen(QPen(QColor("#4fc3f7"), 2))
            pts_px = [img2px(p.x(), p.y()) for p in self._path_pts]
            for i, (px_x, px_y) in enumerate(pts_px):
                painter.drawEllipse(QPoint(px_x, px_y), 5, 5)
                if i > 0:
                    lx, ly = pts_px[i-1]
                    painter.drawLine(lx, ly, px_x, px_y)

        painter.end()
        self._prev_lbl.setPixmap(final)

    def _on_preview_click(self, event):
        """Klick auf Vorschau: Startpunkt (Stern) oder Pfadpunkt (Pfad)."""
        if not self._base_pil: return
        base  = self._base_pil
        thumb_copy = base.copy(); thumb_copy.thumbnail((self._PW, self._PH))
        tw, th = thumb_copy.width, thumb_copy.height
        ox = (self._PW - tw) // 2
        oy = (self._PH - th) // 2
        ix = int((event.pos().x() - ox) * base.width  / max(1, tw))
        iy = int((event.pos().y() - oy) * base.height / max(1, th))
        ix = max(0, min(base.width  - 1, ix))
        iy = max(0, min(base.height - 1, iy))

        if self._rb_star.isChecked():
            self._star_origin = (ix, iy)
            self._lbl_star_pt.setText(f"Startpunkt: ({ix}, {iy})")
        elif self._rb_path.isChecked():
            self._path_pts.append(QPoint(ix, iy))
            self._lbl_path.setText(f"Pfad: {len(self._path_pts)} Punkte")
        self._draw_preview_overlay()

    def _clear_path(self):
        self._path_pts.clear()
        self._lbl_path.setText("Pfad: 0 Punkte")
        self._draw_preview_overlay()

    # ── Frame-Vorschau ────────────────────────────────────────

    def _show_frame(self, idx: int):
        if not self.frames: return
        idx = idx % len(self.frames)
        self._cur_fi = idx
        f = self.frames[idx]
        thumb = f.copy(); thumb.thumbnail((self._PW, self._PH))
        pix = pil_to_qpixmap(thumb).scaled(
            self._PW, self._PH,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._prev_lbl.setPixmap(pix)
        self._lbl_fi.setText(f"Frame {idx+1} / {len(self.frames)}")

    # ── Generator ─────────────────────────────────────────────

    def _generate(self):
        if self._rb_vhs.isChecked():
            self.frames = self._gen_vhs()
        elif self._rb_star.isChecked():
            self.frames = self._gen_stars()
        elif self._rb_path.isChecked():
            self.frames = self._gen_path()
        else:
            self.frames = self._gen_parallax()
        if self.frames:
            self._show_frame(0)

    # ── a) VHS-Distortion-Loop ────────────────────────────────

    def _gen_vhs(self) -> list:
        import random
        base   = self.editor._composite_layers().convert("RGB")
        n      = self._sp_frames.value()
        intens = self._sp_vhs_int.value()
        frames = []
        for i in range(n):
            frames.append(base.copy() if i % 2 == 0
                          else self._vhs_frame(base, random.Random(i * 7 + 1), intens))
        return frames

    @staticmethod
    def _vhs_frame(img, rng, intensity: int):
        img  = img.copy().convert("RGB")
        w, h = img.size
        pixels = list(img.getdata())
        f = 1.0 - intensity * 0.025
        for y in range(0, h, 2):
            for x in range(w):
                r, g, b = pixels[y * w + x]
                pixels[y * w + x] = (max(0, int(r*f)), max(0, int(g*f)), max(0, int(b*f)))
        img.putdata(pixels)
        n_s = rng.randint(4 + intensity, 7 + intensity * 2)
        for _ in range(n_s):
            sy = rng.randint(0, h - 1); sh = rng.randint(2, 3 + intensity * 2)
            shift = rng.randint(-intensity * 5, intensity * 5)
            brt   = rng.uniform(max(0.3, 1.0 - intensity * 0.07),
                                min(2.0, 1.0 + intensity * 0.12))
            band  = img.crop((0, sy, w, min(h, sy + sh)))
            band  = ImageEnhance.Brightness(band).enhance(brt)
            if shift:
                nb = PILImage.new("RGB", (w, band.height))
                s  = abs(shift)
                if shift > 0:
                    nb.paste(band.crop((0,       0, w-s, band.height)), (s,   0))
                    nb.paste(band.crop((w-s,     0, w,   band.height)), (0,   0))
                else:
                    nb.paste(band.crop((s,       0, w,   band.height)), (0,   0))
                    nb.paste(band.crop((0,       0, s,   band.height)), (w-s, 0))
                band = nb
            img.paste(band, (0, sy))
        r_c, g_c, b_c = img.split()
        sp = max(1, intensity * w // 200)
        r2 = PILImage.new("L", (w, h), 0); b2 = PILImage.new("L", (w, h), 0)
        r2.paste(r_c.crop((sp, 0, w, h)), (0,  0))
        b2.paste(b_c.crop((0,  0, w - sp, h)), (sp, 0))
        return PILImage.merge("RGB", (r2, g_c, b2))

    # ── b) Sternschauer ───────────────────────────────────────

    def _gen_stars(self) -> list:
        import random, math
        # ── Validation ────────────────────────────────────────
        s_min = self._sp_s_min.value(); s_max = self._sp_s_max.value()
        z_min = self._sp_sz_min.value(); z_max = self._sp_sz_max.value()
        if s_min > s_max:
            QMessageBox.warning(self, "Ungültige Einstellung",
                f"Minimale Sterne ({s_min}) dürfen nicht größer als "
                f"maximale Sterne ({s_max}) sein.\n\n"
                "Bitte den Wert korrigieren und erneut generieren.")
            return []
        if z_min > z_max:
            QMessageBox.warning(self, "Ungültige Einstellung",
                f"Minimale Größe ({z_min} px) darf nicht größer als "
                f"maximale Größe ({z_max} px) sein.\n\n"
                "Bitte den Wert korrigieren und erneut generieren.")
            return []

        base = self.editor._composite_layers().convert("RGBA")
        w, h = base.size
        n    = self._sp_frames.value()
        sx, sy = self._star_origin if self._star_origin else (w // 2, 0)
        rng    = random.Random()
        n_st   = rng.randint(s_min, s_max)
        sizes  = [rng.randint(z_min, z_max) for _ in range(n_st)]
        # Basis-Offset pro Stern (Startposition links/rechts vom Ursprung)
        base_offsets = [rng.randint(-w // 5, w // 5) for _ in range(n_st)]
        # Amplitude und Frequenz der horizontalen Schwingung pro Stern
        amplitudes   = [rng.randint(w // 20, w // 8) for _ in range(n_st)]
        freqs        = [rng.uniform(0.4, 1.0)         for _ in range(n_st)]
        delays       = [rng.randint(0, max(1, n // 3)) for _ in range(n_st)]
        step         = max(1, (h - sy) / max(1, n - 1))
        frames       = []; done = set()

        for fi in range(n):
            canvas = base.copy()
            draw   = ImageDraw.Draw(canvas, "RGBA")
            for si in range(n_st):
                prog = fi - delays[si]
                if prog < 0: continue
                cy_  = int(sy + prog * step)
                # Horizontale Schwingung: Sinus × Amplitude
                cx_  = sx + base_offsets[si] + int(amplitudes[si] * math.sin(prog * freqs[si]))
                sz   = sizes[si]
                if cy_ + sz >= h:
                    cy_ = h - sz; done.add(si)
                GifEditorDialog._star5(draw, cx_, cy_, sz)
            # Akkumulierte Sterne am Boden (keine Schwingung mehr)
            for si in done:
                cx_final = sx + base_offsets[si]
                GifEditorDialog._star5(draw, cx_final, h - sizes[si], sizes[si])
            frames.append(canvas.convert("RGB"))
        return frames

    @staticmethod
    def _star5(draw, cx, cy, r_outer):
        import math
        r_inner = r_outer * 0.42
        pts = []
        for i in range(10):
            r = r_outer if i % 2 == 0 else r_inner
            a = math.radians(i * 36 - 90)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        draw.polygon(pts, fill=(255, 210, 0, 255), outline=(220, 160, 0, 255))

    # ── c) Pfad-Animation ─────────────────────────────────────

    def _gen_path(self) -> list:
        import math
        if len(self._path_pts) < 2:
            QMessageBox.warning(self, "Pfad",
                "Bitte mindestens 2 Wegpunkte auf der Vorschau setzen.")
            return []
        layer_idx = self._cb_layer.currentData()
        if layer_idx is None or layer_idx >= len(self.editor.layers):
            return []
        n     = self._sp_frames.value()
        lyrs  = self.editor.layers
        obj_l = lyrs[layer_idx]
        if not obj_l.image: return []
        obj   = obj_l.image.convert("RGBA")

        # Hintergrund: alle anderen sichtbaren Ebenen
        bg = PILImage.new("RGBA", lyrs[0].image.size if lyrs[0].image else (800, 600),
                          (40, 40, 40, 255))
        for i, lyr in enumerate(lyrs):
            if i == layer_idx or not lyr.visible or not lyr.image: continue
            img = lyr.image.copy()
            if lyr.opacity < 100:
                r, g, b, a = img.split()
                a = a.point([int(v * lyr.opacity / 100) for v in range(256)])
                img = PILImage.merge("RGBA", (r, g, b, a))
            bg.paste(img, (lyr.x, lyr.y), img)

        # Catmull-Rom Pfad interpolieren
        path = _catmull_rom_pts(self._path_pts,
                                n_per_seg=max(1, n // max(1, len(self._path_pts) - 1)))
        total = len(path)
        idxs  = [int(i * (total - 1) / max(1, n - 1)) for i in range(n)]
        frames = []

        for _, pi in enumerate(idxs):
            frame = bg.copy()
            pos   = path[pi]
            ix    = pos.x() - obj.width  // 2
            iy    = pos.y() - obj.height // 2
            # Tangenten-Winkel → Rotation
            angle = 0.0
            if pi + 1 < total:
                nxt = path[pi + 1]
                dx  = nxt.x() - pos.x(); dy = nxt.y() - pos.y()
                if dx or dy:
                    angle = math.degrees(math.atan2(dy, dx))
            rot = obj.rotate(-angle, expand=True,
                             resample=PILImage.Resampling.BICUBIC)
            ix -= (rot.width  - obj.width)  // 2
            iy -= (rot.height - obj.height) // 2
            frame.paste(rot, (ix, iy), rot)
            frames.append(frame.convert("RGB"))
        return frames

    # ── d) Parallax-GIF ───────────────────────────────────────

    def _gen_parallax(self) -> list:
        """
        Parallax-GIF: Jede Ebene schwingt horizontal (oder vertikal) mit
        einer Amplitude proportional zu ihrer Tiefe (Index in der Ebenen-Liste).
        Hintere Ebenen (Index 0) bewegen sich kaum, vordere viel.

        ALGORITHMUS:
        1. n Frames, jeder Frame = vollständig neu kompositiert
        2. Für Ebene i bei n Ebenen:
           depth_factor = i / (n - 1)   → 0.0 (hinten) … 1.0 (vorne)
           offset = amplitude * depth_factor * sin(angle)
        3. Alle Ebenen werden mit diesem versetzten x (oder y) gerendert.
        """
        import math
        lyrs  = self.editor.layers
        n_lyrs = len(lyrs)
        if n_lyrs < 2:
            QMessageBox.warning(self, "Parallax",
                "Für den Parallax-Effekt werden mindestens 2 Ebenen benötigt.")
            return []

        n          = self._sp_frames.value()
        amplitude  = self._sp_par_amp.value()
        swings     = self._sp_par_swing.value()
        horizontal = self._rb_par_h.isChecked()

        # Bestimme Leinwandgröße
        base_w = max((l.x + l.image.width  for l in lyrs if l.image), default=800)
        base_h = max((l.y + l.image.height for l in lyrs if l.image), default=600)

        frames = []
        for fi in range(n):
            angle  = (fi / max(1, n - 1)) * swings * 2 * math.pi
            canvas = PILImage.new("RGBA", (base_w, base_h), (30, 30, 30, 255))

            for i, lyr in enumerate(lyrs):
                if not lyr.visible or not lyr.image:
                    continue
                depth_factor = i / max(1, n_lyrs - 1)
                offset = int(amplitude * depth_factor * math.sin(angle))

                img = lyr.image.convert("RGBA")
                if lyr.opacity < 100:
                    r, g, b, a = img.split()
                    a = a.point([int(v * lyr.opacity / 100) for v in range(256)])
                    img = PILImage.merge("RGBA", (r, g, b, a))

                px = lyr.x + (offset if horizontal else 0)
                py = lyr.y + (offset if not horizontal else 0)
                canvas.paste(img, (px, py), img)

            frames.append(canvas.convert("RGB"))
        return frames

    # ── Export ────────────────────────────────────────────────

    # ── Sound laden ───────────────────────────────────────────

    def _load_sound(self):
        """Sounddatei laden (WAV, MP3, OGG, FLAC)."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Sound wählen", "",
            "Audio (*.wav *.mp3 *.ogg *.flac *.aac *.m4a);;Alle (*)")
        if not path:
            return
        self._sound_path = path
        name = path.replace("\\", "/").split("/")[-1]
        self._lbl_sound.setText(f"✅  {name}")

    # ── Export ────────────────────────────────────────────────

    def _export(self):
        if not self.frames:
            QMessageBox.warning(self, "Export",
                "Bitte zuerst 'GIF generieren' klicken."); return
        path, _ = QFileDialog.getSaveFileName(
            self, "GIF speichern", "", "GIF (*.gif);;Alle (*)")
        if not path: return
        try:
            delay  = self._sp_delay.value()
            first  = self.frames[0].convert("RGBA").convert(
                "P", palette=PILImage.Palette.ADAPTIVE, dither=PILImage.Dither.NONE)
            rest   = [f.convert("RGBA").convert(
                "P", palette=PILImage.Palette.ADAPTIVE, dither=PILImage.Dither.NONE)
                      for f in self.frames[1:]]
            first.save(path, save_all=True, append_images=rest,
                       loop=0, duration=delay, optimize=False)
            # ── Sound-Export ──────────────────────────────────
            sound_msg = ""
            if self._sound_path:
                sound_out = self._export_sound(path, len(self.frames) * delay)
                if sound_out:
                    sound_msg = f"\n\n🎵  Sound gespeichert:\n{sound_out}"
            QMessageBox.information(self, "Exportiert",
                f"GIF gespeichert:\n{path}{sound_msg}")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))

    def _export_video(self):
        """
        Exportiert Frames + Sound als MP4-Video.

        Verwendet imageio-ffmpeg (bereits als moviepy-Abhängigkeit installiert).
        Wenn eine Sounddatei geladen ist, wird ffmpeg genutzt um Video + Audio
        zusammenzufügen — kein moviepy-API-Aufruf nötig.
        """
        if not self.frames:
            QMessageBox.warning(self, "Export",
                "Bitte zuerst 'GIF generieren' klicken."); return
        path, _ = QFileDialog.getSaveFileName(
            self, "Video speichern", "", "MP4 (*.mp4);;Alle (*)")
        if not path: return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"

        import os, subprocess

        delay = self._sp_delay.value()
        fps   = int(round(1000.0 / max(1, delay)))

        # libx264 erfordert gerade Breite & Höhe
        sample_w, sample_h = self.frames[0].size
        even_w = sample_w - (sample_w % 2)
        even_h = sample_h - (sample_h % 2)
        def _even(f):
            img = f.convert("RGB")
            return img.crop((0, 0, even_w, even_h)) if img.size != (even_w, even_h) else img

        try:
            import imageio_ffmpeg
        except ImportError:
            QMessageBox.critical(self, "Fehlende Abhängigkeit",
                "imageio-ffmpeg fehlt.\n\n  python -m pip install imageio imageio-ffmpeg")
            return

        ffmpeg_exe  = imageio_ffmpeg.get_ffmpeg_exe()
        # Wenn Sound vorhanden: erst stummes Video, dann muxen
        vid_tmp     = path + "._tmp.mp4" if self._sound_path else path

        try:
            writer = imageio_ffmpeg.write_frames(
                vid_tmp,
                size=(even_w, even_h),
                fps=fps,
                codec="libx264",
                output_params=["-crf", "18", "-pix_fmt", "yuv420p"],
            )
            writer.send(None)           # Generator starten
            for f in self.frames:
                writer.send(_even(f).tobytes())
            writer.close()
        except Exception as e:
            QMessageBox.critical(self, "Video-Fehler", str(e)); return

        # ── Audio muxen via ffmpeg-Subprocess ────────────────
        if self._sound_path:
            vid_dur_s = len(self.frames) * delay / 1000.0
            try:
                cmd = [
                    ffmpeg_exe, "-y",
                    "-i", vid_tmp,
                    "-stream_loop", "-1",   # Audio endlos loopen
                    "-i", self._sound_path,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-t", str(vid_dur_s),   # auf Video-Dauer begrenzen
                    path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr[-600:])
            except Exception as e:
                # Audio-Mux fehlgeschlagen → stummes Video behalten
                try: os.replace(vid_tmp, path)
                except: pass
                QMessageBox.warning(self, "Audio-Warnung",
                    f"Audio konnte nicht eingebunden werden:\n{e}\n\n"
                    f"Video ohne Ton gespeichert:\n{path}")
                return
            finally:
                try: os.remove(vid_tmp)
                except: pass

        QMessageBox.information(self, "Exportiert", f"Video gespeichert:\n{path}")

    def _export_sound(self, gif_path: str, gif_ms: int) -> str | None:
        """
        Schneidet die geladene Sounddatei auf die GIF-Dauer zu.
        GIF und Sound sind danach gleich lang → loopen synchron.

        Methode 1 (bevorzugt): pydub — unterstützt MP3/OGG/WAV/etc.
        Methode 2 (Fallback):  eingebautes wave-Modul — nur WAV.
        Methode 3 (Fallback):  Datei kopieren + Info anzeigen.
        """
        import os, shutil
        if not self._sound_path:
            return None
        ext      = os.path.splitext(self._sound_path)[1].lower()
        out_path = gif_path.replace(".gif", f"_sound{ext}")

        # ── Methode 1: pydub ──────────────────────────────────
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(self._sound_path)
            # Auf GIF-Dauer loopen falls zu kurz, dann auf gif_ms kürzen
            while len(audio) < gif_ms:
                audio = audio + audio
            audio = audio[:gif_ms]
            fmt = ext.lstrip(".") or "wav"
            if fmt == "m4a": fmt = "mp4"
            audio.export(out_path, format=fmt)
            return out_path
        except ImportError:
            pass
        except Exception as e:
            QMessageBox.warning(self, "Sound-Fehler",
                f"pydub-Fehler: {e}\nVersuche WAV-Fallback…")

        # ── Methode 2: wave (nur .wav) ────────────────────────
        if ext == ".wav":
            try:
                import wave
                with wave.open(self._sound_path, "rb") as wf:
                    params     = wf.getparams()
                    framerate  = wf.getframerate()
                    n_channels = wf.getnchannels()
                    sampwidth  = wf.getsampwidth()
                    data       = wf.readframes(wf.getnframes())
                target_frames = int(framerate * gif_ms / 1000)
                bytes_per_frame = n_channels * sampwidth
                target_bytes    = target_frames * bytes_per_frame
                # Loopen bis Ziel-Länge erreicht
                looped = data
                while len(looped) < target_bytes:
                    looped += data
                looped = looped[:target_bytes]
                with wave.open(out_path, "wb") as wo:
                    wo.setparams(params)
                    wo.writeframes(looped)
                return out_path
            except Exception as e:
                QMessageBox.warning(self, "Sound-Fehler", f"WAV-Fehler: {e}")

        # ── Methode 3: Datei kopieren + Hinweis ───────────────
        shutil.copy(self._sound_path, out_path)
        QMessageBox.information(self, "Sound-Hinweis",
            f"Sound wurde als '{out_path}' kopiert.\n\n"
            f"Für automatisches Trimmen:\n  pip install pydub\n\n"
            f"GIF-Dauer: {gif_ms/1000:.1f} s")
        return out_path


# ══════════════════════════════════════════════════════════════
#  3D-MODELL-VIEWER (BETA): 2D-Bild → interaktives 3D-Mesh
# ══════════════════════════════════════════════════════════════
#
#  Funktionsweise:
#  1. KI-Tiefenschätzung  (Depth-Anything-V2 via transformers, ~100 MB)
#     Fallback: MiDaS (torch + timm) → Luminanz-Approximation
#  2. Tiefenkarte → 3D-Mesh (jeder Pixel = Vertex, Z = Tiefenwert)
#  3. Interaktiver Viewer (PyOpenGL + QOpenGLWidget)
#     Steuerung: Maus ziehen = Rotation, Mausrad = Zoom
#
#  pip install transformers torch PyOpenGL PyOpenGL_accelerate
# ══════════════════════════════════════════════════════════════



class DepthWorker(QThread):
    """
    Schätzt eine Tiefenkarte im Hintergrund-Thread.

    Probiert nacheinander:
    1. transformers + Depth-Anything-V2-Small (~100 MB Download, beste Qualität)
    2. torch + MiDaS_small (falls timm installiert)
    3. Luminanz-basierte Approximation (immer verfügbar, niedrige Qualität)
    """
    depth_ready = pyqtSignal(object)   # numpy.ndarray float32 0–1
    progress    = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, pil_image):
        super().__init__()
        self.pil_image = pil_image

    def run(self):
        try:
            import numpy as np
            raw = self._estimate()
            lo, hi = raw.min(), raw.max()
            depth = ((raw - lo) / (hi - lo + 1e-8)).astype(np.float32)
            self.depth_ready.emit(depth)
        except Exception as e:
            self.error.emit(str(e))

    # Subprocess-Skript: läuft in sauberem Python ohne Qt-DLLs
    _DEPTH_SCRIPT = (
        "import sys, numpy as np\n"
        "from PIL import Image\n"
        "img = Image.open(sys.argv[1]).convert('RGB')\n"
        "try:\n"
        "    from transformers import pipeline\n"
        "    import torch\n"
        "    dev = 0 if torch.cuda.is_available() else -1\n"
        "    pipe = pipeline('depth-estimation',\n"
        "        model='depth-anything/Depth-Anything-V2-Small-hf', device=dev)\n"
        "    try:\n"
        "        r = pipe(img)\n"
        "    except Exception:\n"
        "        pipe = pipeline('depth-estimation',\n"
        "            model='depth-anything/Depth-Anything-V2-Small-hf', device=-1)\n"
        "        r = pipe(img)\n"
        "    np.save(sys.argv[2], np.array(r['depth'], dtype='float32'))\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(str(e), file=sys.stderr); sys.exit(1)\n"
    )

    def _estimate(self):
        import sys, os, subprocess, tempfile
        import numpy as np

        # ── Methode 1: Depth-Anything-V2 im Subprocess (kein Qt-DLL-Konflikt) ──
        img_tmp = out_tmp = None
        try:
            self.progress.emit("⏳  Lade Depth-Anything-V2 (Subprocess-Modus) …")
            fd, img_tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            fd, out_tmp = tempfile.mkstemp(suffix=".npy")
            os.close(fd)

            self.pil_image.save(img_tmp)
            self.progress.emit("🔍  Berechne KI-Tiefenkarte (Depth-Anything-V2) …")
            res = subprocess.run(
                [sys.executable, "-c", self._DEPTH_SCRIPT, img_tmp, out_tmp],
                capture_output=True, text=True, timeout=300,
            )
            if res.returncode == 0:
                depth = np.load(out_tmp)
                self.progress.emit("✅  Depth-Anything-V2 fertig.")
                return depth
            self.progress.emit(f"⚠  Depth-Anything-V2 fehlgeschlagen: {res.stderr[-120:]}")
        except Exception as _e1:
            self.progress.emit(f"⚠  Subprocess fehlgeschlagen: {_e1!s:.120}")
        finally:
            for p in (img_tmp, out_tmp):
                if p:
                    try: os.unlink(p)
                    except OSError: pass

        # ── Methode 2: Kanten-/Schärfe-Approximation (kein KI) ────────────────
        self.progress.emit("⚠  Kein KI-Modell verfügbar — verwende Luminanz-Approximation …")
        from PIL import ImageFilter as _IF
        edges = np.array(
            self.pil_image.convert("L").filter(_IF.FIND_EDGES), dtype=np.float32
        )
        smooth_pil = PILImage.fromarray(edges.astype(np.uint8)).filter(_IF.GaussianBlur(8))
        self.progress.emit("✅  Luminanz-Approximation fertig (geringe Qualität).")
        return np.array(smooth_pil, dtype=np.float32)


class NovelViewWorker(QThread):
    """
    Generiert KI-basierte Rückansicht via zero123plus-v1.1 (sudo-ai).

    Benötigt:
      pip install diffusers transformers accelerate
      pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

    Gibt ein PIL-Image zurück (Rückseite ≈ azimuth 150°–210°, gemittelt).
    """
    views_ready = pyqtSignal(object)   # PIL Image — generierte Rückseite
    progress    = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, pil_image):
        super().__init__()
        self.pil_image = pil_image

    # Subprocess-Skript: läuft in sauberem Python ohne Qt-DLLs
    _Z123_SCRIPT = (
        "import sys, os, functools, numpy as np\n"
        "from PIL import Image\n"
        "img_path, out_path = sys.argv[1], sys.argv[2]\n"
        "img = Image.open(img_path).convert('RGB')\n"
        "if max(img.size) > 512:\n"
        "    img = img.copy(); img.thumbnail((512,512), Image.Resampling.LANCZOS)\n"
        "import torch\n"
        "_orig = torch.load\n"
        "torch.load = functools.partial(_orig, weights_only=False)\n"
        "try:\n"
        "    from diffusers import DiffusionPipeline\n"
        "    pipe = DiffusionPipeline.from_pretrained(\n"
        "        'sudo-ai/zero123plus-v1.1',\n"
        "        custom_pipeline='sudo-ai/zero123plus-pipeline',\n"
        "        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,\n"
        "        trust_remote_code=True,\n"
        "    )\n"
        "    pipe = pipe.to('cuda' if torch.cuda.is_available() else 'cpu')\n"
        "    grid = pipe(img, num_inference_steps=75).images[0]\n"
        "    tw, th = grid.width//2, grid.height//3\n"
        "    v150 = np.array(grid.crop((0,th,tw,th*2)), np.float32)\n"
        "    v210 = np.array(grid.crop((tw,th,tw*2,th*2)), np.float32)\n"
        "    back = Image.fromarray(((v150+v210)/2).clip(0,255).astype(np.uint8))\n"
        "    orig = Image.open(img_path)\n"
        "    back.resize(orig.size, Image.Resampling.LANCZOS).save(out_path)\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(str(e), file=sys.stderr); sys.exit(1)\n"
        "finally:\n"
        "    torch.load = _orig\n"
    )

    def run(self):
        try:
            back = self._zero123plus()
            self.views_ready.emit(back)
        except Exception as e:
            self.error.emit(str(e))

    # ── zero123plus-v1.1 im Subprocess ───────────────────────
    def _zero123plus(self):
        import sys, os, subprocess, tempfile

        img_tmp = out_tmp = None
        try:
            self.progress.emit("⏳  Lade zero123plus-v1.1 (Subprocess-Modus) …")
            fd, img_tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            fd, out_tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)

            self.pil_image.save(img_tmp)
            self.progress.emit("🔄  Generiere 6 Ansichten (zero123plus) — bitte warten …")
            res = subprocess.run(
                [sys.executable, "-c", self._Z123_SCRIPT, img_tmp, out_tmp],
                capture_output=True, text=True, timeout=600,
            )
            if res.returncode != 0:
                raise RuntimeError(res.stderr[-500:] or "Subprocess fehlgeschlagen (kein stderr)")

            back_pil = PILImage.open(out_tmp).convert("RGB")
            self.progress.emit("✅  KI-Rückseite (zero123plus) fertig.")
            return back_pil
        finally:
            for p in (img_tmp, out_tmp):
                if p:
                    try: os.unlink(p)
                    except OSError: pass

    # ── (veraltet — Zero-1-to-3 XL entfernt aus diffusers 0.28+) ─
    def _zero1to3(self):
        import torch
        from diffusers import Zero1to3StableDiffusionPipeline

        self.progress.emit("⏳  Lade Zero-1-to-3 XL (~5 GB) …")
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        pipe = Zero1to3StableDiffusionPipeline.from_pretrained(
            "cvlab-columbia/zero123-xl",
            torch_dtype=dtype,
        )
        if torch.cuda.is_available():
            pipe = pipe.to("cuda")

        # Zero-1-to-3 erwartet 256×256
        img256 = self.pil_image.convert("RGB").resize((256, 256),
                                                       PILImage.Resampling.LANCZOS)

        self.progress.emit("🔄  Generiere Rückseite azimuth=180° (Zero-1-to-3 XL) …")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        result = pipe(
            img256,
            guidance_scale=3.0,
            num_inference_steps=76,
            elevation_cond=torch.Tensor([0.0]).to(device),
            azimuth_cond=torch.Tensor([180.0]).to(device),
            distance_cond=torch.Tensor([1.0]).to(device),
        ).images[0]

        # Auf Originalgröße hochskalieren
        result = result.resize(self.pil_image.size, PILImage.Resampling.LANCZOS)
        self.progress.emit("✅  KI-Rückseite (Zero-1-to-3 XL) fertig.")
        return result


class ThreeDViewerWidget(QWidget):
    """
    3D-Viewer auf Basis von Matplotlib (kein PyOpenGL / QOpenGLWidget nötig).

    Matplotlib rendert das Tiefenkarten-Mesh als texturierten 3D-Plot.
    Steuerung direkt durch Matplotlib:
    • Linke Maustaste ziehen  → Rotation
    • Rechte Maustaste ziehen → Zoom
    Tiefenskala nachträglich änderbar via update_depth_scale().
    """

    def __init__(self, pil_image, depth_map, depth_scale=0.3, invert=False,
                 show_back=True, parent=None):
        super().__init__(parent)
        self.pil_image   = pil_image
        self.depth_map   = depth_map
        self.depth_scale = depth_scale
        self.invert      = invert
        self.show_back   = show_back
        self._canvas       = None
        self._fig          = None
        self._back_bg_mask = None   # set by set_ai_back(); None = use flipped front mask
        self.setMinimumSize(600, 420)
        self._init_plot()

    def _init_plot(self):
        import numpy as np

        # matplotlib-Canvas einbinden (backend_qtagg seit matplotlib 3.6)
        FigureCanvasQTAgg = None
        for _backend in ("matplotlib.backends.backend_qtagg",
                         "matplotlib.backends.backend_qt5agg"):
            try:
                import importlib
                _mod = importlib.import_module(_backend)
                FigureCanvasQTAgg = _mod.FigureCanvasQTAgg
                break
            except Exception:
                pass
        if FigureCanvasQTAgg is None:
            lbl = QLabel("matplotlib fehlt.\n  pip install matplotlib")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay = QVBoxLayout(self); lay.addWidget(lbl)
            return
        from matplotlib.figure import Figure

        self._fig    = Figure(facecolor="#0a0a0a")
        self._canvas = FigureCanvasQTAgg(self._fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas)

        # Daten vorbereiten
        iw, ih = self.pil_image.size
        gw = min(150, iw)
        gh = min(150, ih)
        img_s   = self.pil_image.resize((gw, gh), PILImage.Resampling.LANCZOS).convert("RGB")
        dep_s   = PILImage.fromarray((self.depth_map * 255).astype(np.uint8)).resize(
                      (gw, gh), PILImage.Resampling.LANCZOS)
        self._img_np   = np.array(img_s,  dtype=np.float32) / 255.0
        self._depth_np = np.array(dep_s,  dtype=np.float32) / 255.0
        aspect = iw / max(1, ih)
        self._XX, self._YY = np.meshgrid(
            np.linspace(-0.5 * aspect, 0.5 * aspect, gw),
            np.linspace(0.5, -0.5, gh),   # Phase 1: Y top→bottom in image = +Y→−Y in 3D
        )

        # ── Hintergrund-Maske (True = Hintergrundpixel → transparent) ──
        # Computed first so _make_back_texture can use char_mask.
        self._bg_mask = ThreeDViewerWidget._detect_bg(self._img_np)

        # ── Generierte Rückseite — silhouette-ring inpainting ─────────
        # Instead of a simple flip (which bleeds belly/light colors onto
        # the back), we propagate the colors from the inner silhouette
        # ring outward.  Each back pixel inherits the nearest edge color
        # of the FRONT (orange body → orange back, black hair → black
        # back), giving a coherent result for any character pose.
        self._back_np = ThreeDViewerWidget._make_back_texture(
            self._img_np, ~self._bg_mask)

        self._draw()

    @staticmethod
    def _detect_bg(img_np, threshold=0.13):
        """
        Erkennt Hintergrundpixel: Flood-Fill-Approximation über Eck-Farbe
        + direkte Erkennung nahezu-weißer Pixel.

        After the raw threshold pass a morphological 'fill-holes' step
        removes interior false-positives (slight transparency / JPEG
        artefacts inside the character body) that would otherwise punch
        NaN holes through the mesh.
        """
        import numpy as np
        # ── Raw threshold pass ────────────────────────────────
        corners = np.array([img_np[0, 0], img_np[0, -1],
                             img_np[-1, 0], img_np[-1, -1]])
        bg = corners.mean(axis=0)                          # (3,)
        diff = np.abs(img_np - bg).mean(axis=2)            # (gh, gw)
        near_bg    = diff < threshold
        near_white = img_np.min(axis=2) > 0.82
        raw_mask   = near_bg | near_white                  # True = background

        # ── Fill interior holes ───────────────────────────────
        # A background-classified pixel that is completely enclosed by
        # character pixels is a false positive (body artefact).  We keep
        # only background pixels that are reachable from the image border
        # through other background pixels — those are truly exterior.
        try:
            from scipy.ndimage import binary_fill_holes
            # ~raw_mask = character (True); fill enclosed False regions
            import numpy as _np
            filled = _np.asarray(binary_fill_holes(~raw_mask), dtype=bool)
            return ~filled
        except ImportError:
            # Pure-numpy fallback: iterative dilation from the border,
            # constrained to raw_mask.  Converges in ≤ h+w steps.
            flood = np.zeros_like(raw_mask)
            flood[0,  :] = raw_mask[0,  :]
            flood[-1, :] = raw_mask[-1, :]
            flood[:,  0] = raw_mask[:,  0]
            flood[:, -1] = raw_mask[:, -1]
            for _ in range(raw_mask.shape[0] + raw_mask.shape[1]):
                pad   = np.pad(flood, 1, constant_values=False)
                grown = (pad[:-2, 1:-1] | pad[2:,  1:-1] |
                         pad[1:-1, :-2] | pad[1:-1, 2:]) & raw_mask
                if np.array_equal(grown, flood):
                    break
                flood = grown
            return flood

    # ── Texture helpers ───────────────────────────────────────
    @staticmethod
    def _dilate_texture(img_np, char_mask, iterations=3):
        """
        Edge-padding: expands character colors outward into the background
        by `iterations` pixels (vectorised numpy, no Python pixel loop).

        Each pass fills unfilled fringe pixels with the weighted average of
        their already-filled 4-connected neighbors.  The atlas character
        area is untouched; only the boundary fringe around the silhouette
        is written, eliminating the white anti-aliasing halo that would
        otherwise bleed onto seam UVs.
        """
        import numpy as np
        result = img_np.copy()
        filled = char_mask.copy()
        for _ in range(iterations):
            pad_r = np.pad(result, ((1, 1), (1, 1), (0, 0)), constant_values=0.0)
            pad_f = np.pad(filled.astype(np.float32), 1,      constant_values=0.0)
            nbr_c = (pad_r[:-2, 1:-1] + pad_r[2:,  1:-1] +
                     pad_r[1:-1, :-2] + pad_r[1:-1, 2:])
            nbr_n = (pad_f[:-2, 1:-1] + pad_f[2:,  1:-1] +
                     pad_f[1:-1, :-2] + pad_f[1:-1, 2:])
            to_fill = ~filled & (nbr_n > 0)
            if not to_fill.any():
                break
            avg    = nbr_c / np.maximum(nbr_n[:, :, None], 1.0)
            result = np.where(to_fill[:, :, None], avg, result)
            filled = filled | to_fill
        return result

    @staticmethod
    def _compute_inset_map(char_mask, inset_px=3):
        """
        Returns (row_map, col_map) — integer arrays of shape (gh, gw).

        For each pixel (i, j):
          • If it is already >= inset_px pixels inside the silhouette,
            row_map[i,j] = i  and  col_map[i,j] = j  (maps to itself).
          • Otherwise it maps to the nearest pixel that IS >= inset_px
            pixels deep — i.e., the nearest solidly-interior pixel.

        Used for UV insetting (seam faces sample solid interior colors)
        and for back-texture generation (avoids anti-aliased edge halo).
        """
        import numpy as np
        try:
            from scipy.ndimage import distance_transform_edt
            dist  = distance_transform_edt(char_mask)   # 0 outside, depth inside
            solid = dist >= inset_px
            if not solid.any():                         # very thin object
                solid = dist >= max(1.0, float(dist.max()))
            _, nn = distance_transform_edt(~solid, return_indices=True)
            return nn[0].astype(np.int32), nn[1].astype(np.int32)
        except ImportError:
            # ── scipy-free fallback: erode inset_px times, then dilate ──
            from PIL import Image as _PIL, ImageFilter as _IFP
            pil = _PIL.fromarray((char_mask * 255).astype(np.uint8))
            for _ in range(inset_px):
                pil = pil.filter(_IFP.MinFilter(3))
            solid = np.array(pil, dtype=bool)
            if not solid.any():
                solid = char_mask
            h, w  = char_mask.shape
            ri    = np.tile(np.arange(h)[:, None], (1, w)).astype(np.int32)
            ci    = np.tile(np.arange(w)[None, :], (h, 1)).astype(np.int32)
            r_map = np.where(solid, ri, -1).astype(np.int32)
            c_map = np.where(solid, ci, -1).astype(np.int32)
            filled = solid.copy()
            for _ in range(h + w):
                to_fill = char_mask & ~filled
                if not to_fill.any():
                    break
                pr = np.pad(r_map, 1, constant_values=-1)
                pc = np.pad(c_map, 1, constant_values=-1)
                pf = np.pad(filled.astype(np.int32), 1, constant_values=0)
                # Accumulate neighbor coordinates (only from filled pixels)
                sr = (pr[:-2,1:-1]*(pf[:-2,1:-1]) + pr[2:,1:-1]*(pf[2:,1:-1]) +
                      pr[1:-1,:-2]*(pf[1:-1,:-2]) + pr[1:-1,2:]*(pf[1:-1,2:]))
                sc = (pc[:-2,1:-1]*(pf[:-2,1:-1]) + pc[2:,1:-1]*(pf[2:,1:-1]) +
                      pc[1:-1,:-2]*(pf[1:-1,:-2]) + pc[1:-1,2:]*(pf[1:-1,2:]))
                cnt = (pf[:-2,1:-1] + pf[2:,1:-1] +
                       pf[1:-1,:-2] + pf[1:-1,2:])
                valid = to_fill & (cnt > 0)
                r_map  = np.where(valid, (sr / np.maximum(cnt, 1)).astype(np.int32), r_map)
                c_map  = np.where(valid, (sc / np.maximum(cnt, 1)).astype(np.int32), c_map)
                filled = filled | valid
            # Any still-unmapped pixel (isolated dot) → self
            r_map = np.where(r_map < 0, ri, r_map)
            c_map = np.where(c_map < 0, ci, c_map)
            return r_map, c_map

    @staticmethod
    def _make_back_texture(img_np, char_mask):
        """
        Builds a coherent backside texture using the same 3-pixel inset
        ring used for UV insetting.

        Every back pixel inherits the color of the nearest solidly-interior
        front pixel (>= 3px from the silhouette boundary).  This eliminates
        anti-aliased edge colors and ensures the back matches the seam UVs:
        solid orange body → orange back, solid black hair → black back.
        """
        import numpy as np
        from PIL import Image as _PIL, ImageFilter as _IFB

        char = char_mask

        # ── 1. Map every pixel to its nearest solid-interior pixel ────
        row_ins, col_ins = ThreeDViewerWidget._compute_inset_map(char, inset_px=3)

        # ── 2. Sample solid-interior color for every character pixel ──
        back_fill = img_np[row_ins, col_ins] * char[:, :, None]

        # ── 3. Slight blur + desaturation to read as "the back" ───────
        pil  = _PIL.fromarray((back_fill * 255).astype(np.uint8))
        pil  = pil.filter(_IFB.GaussianBlur(radius=1.5))
        back = np.array(pil, dtype=np.float32) / 255.0
        gray = back.mean(axis=2, keepdims=True)
        back = (back * 0.85 + gray * 0.15).clip(0.0, 1.0)

        return np.where(char[:, :, None], back, 0.0)

    def _draw(self):
        if self._fig is None or self._canvas is None:
            return
        import numpy as np
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        # ── Kantenglättungs-Radius (Pixel) ────────────────────
        # Erhöhen für weichere/rundere Kanten, 0 zum Deaktivieren.
        _EDGE_FEATHER_PX = 3

        # Alten Rotations-Handler trennen (beide Event-IDs)
        for _attr in ('_rot_cid', '_rot_cid2'):
            cid = getattr(self, _attr, None)
            if cid is not None:
                try: self._canvas.mpl_disconnect(cid)
                except Exception: pass
            setattr(self, _attr, None)

        self._fig.clear()
        ax = self._fig.add_subplot(111, projection="3d")
        ax.set_facecolor("#0a0a0a")
        ax.patch.set_facecolor("#0a0a0a")
        ax.axis("off")

        depth_raw = (1.0 - self._depth_np) if self.invert else self._depth_np
        mask      = getattr(self, '_bg_mask', None)

        # Tiefenkarte weichzeichnen → glatteres Mesh
        try:
            from PIL import ImageFilter as _IFD
            _dp = PILImage.fromarray((depth_raw * 255).astype(np.uint8))
            depth = np.array(_dp.filter(_IFD.GaussianBlur(radius=1.5)),
                             dtype=np.float32) / 255.0
        except Exception:
            depth = depth_raw

        # Normalize to [0, 1] — guards against AI estimators that output
        # arbitrary float scales which would blow up Z unconditionally.
        _d_min, _d_max = float(depth.min()), float(depth.max())
        if _d_max - _d_min > 1e-6:
            depth = (depth - _d_min) / (_d_max - _d_min)

        # sqrt-Kompression × 0.5 → Vorder-/Rückfläche näher beieinander
        ZZ = np.sqrt(depth) * self.depth_scale * 0.5

        # ── Fix 1: Silhouetten-Kantenramp ─────────────────────
        # Outermost _EDGE_FEATHER_PX layers of character pixels have their Z
        # linearly ramped from 0 → full depth.  This rounds the model edge so
        # it looks like a toy rather than a sharp-edged card.
        # Tweak _EDGE_FEATHER_PX above to change the rounding amount.
        if mask is not None and _EDGE_FEATHER_PX > 0:
            char = (~mask).astype(np.float32)
            dist = np.zeros_like(char)        # 0 = unvisited / background
            cur  = char.copy()
            for step in range(1, _EDGE_FEATHER_PX + 1):
                pad     = np.pad(cur, 1, constant_values=0.0)
                eroded  = (pad[:-2,1:-1] * pad[2:,1:-1]
                           * pad[1:-1,:-2] * pad[1:-1,2:] * cur)
                ring    = cur - eroded          # pixels removed this step
                dist    = np.where(ring > 0, float(step), dist)
                cur     = eroded
            # Interior pixels (never in a ring) get maximum distance
            dist = np.where((cur > 0) & (dist == 0), float(_EDGE_FEATHER_PX), dist)
            feather = np.clip(dist / _EDGE_FEATHER_PX, 0.0, 1.0)
            ZZ = ZZ * feather

        # ── Fix 2: NaN für Hintergrund → keine Wasserfall-Faces ─
        # np.nan causes plot_surface to skip any quad touching that vertex.
        # Previously Z=0 created a visible slanted face at the silhouette.
        if mask is not None:
            ZZ = np.where(mask, np.nan, ZZ)
        ZZ_back = -ZZ   # NaN propagates: background stays absent on back too

        # ── Phase 3: Unified Poly3DCollection (single mesh, no set_visible) ──
        # Builds the same watertight mesh as export_3d so the viewer always
        # matches the exported file.  All faces are always present — no
        # rotation-triggered set_visible swap needed.

        back_img = (self._back_np
                    if (self.show_back and hasattr(self, '_back_np'))
                    else self._img_np)

        gh, gw = ZZ.shape
        fv     = np.isfinite(ZZ)          # (gh, gw) — True = character pixel
        XX_r   = self._XX.ravel()
        YY_r   = self._YY.ravel()
        ZZ_r   = ZZ.ravel()
        ZZB_r  = ZZ_back.ravel()
        img_r  = self._img_np.reshape(-1, 3)
        back_r = back_img.reshape(-1, 3)

        # Quad validity: all 4 corners of a 1×1 grid cell must be character
        qv = (fv[:-1, :-1] & fv[1:, :-1] &
              fv[:-1,  1:] & fv[1:,  1:])          # (gh-1, gw-1)
        ii_q, jj_q = np.where(qv)
        k0 = ii_q * gw + jj_q
        k1 = (ii_q + 1) * gw + jj_q
        k2 = ii_q * gw + (jj_q + 1)
        k3 = (ii_q + 1) * gw + (jj_q + 1)

        def _v(zr, kk):
            """Stack (x, y, z) columns for index array kk."""
            return np.stack([XX_r[kk], YY_r[kk], zr[kk]], axis=1)   # (N, 3)

        # ── Front triangles (CCW, outward +Z) ─────────────────────────────
        # Tri1: k0→k2→k3  │  Tri2: k0→k3→k1
        ft1 = np.stack([_v(ZZ_r, k0), _v(ZZ_r, k2), _v(ZZ_r, k3)], axis=1)
        ft2 = np.stack([_v(ZZ_r, k0), _v(ZZ_r, k3), _v(ZZ_r, k1)], axis=1)
        fc1 = (img_r[k0] + img_r[k2] + img_r[k3]) / 3.0
        fc2 = (img_r[k0] + img_r[k3] + img_r[k1]) / 3.0

        # ── Back triangles (reversed winding, outward −Z) ─────────────────
        # Tri3: k0→k3→k2  │  Tri4: k0→k1→k3
        bt1 = np.stack([_v(ZZB_r, k0), _v(ZZB_r, k3), _v(ZZB_r, k2)], axis=1)
        bt2 = np.stack([_v(ZZB_r, k0), _v(ZZB_r, k1), _v(ZZB_r, k3)], axis=1)
        bc1 = (back_r[k0] + back_r[k3] + back_r[k2]) / 3.0
        bc2 = (back_r[k0] + back_r[k1] + back_r[k3]) / 3.0

        all_tris   = np.concatenate([ft1, ft2, bt1, bt2], axis=0)   # (4N, 3, 3)
        all_colors = np.concatenate([fc1, fc2, bc1, bc2], axis=0)   # (4N, 3)

        # ── Seam triangles (boundary-edge stitching, vectorised) ───────────
        if mask is not None:
            # Pad quad-validity arrays so index arithmetic is uniform
            q_pad_r = np.zeros((gh, gw - 1), dtype=bool)   # right-flank quads
            q_pad_r[:-1, :] = qv                            # qv[i,j] = quad below row i
            q_pad_l = np.zeros((gh, gw - 1), dtype=bool)
            q_pad_l[1:, :] = qv                             # qv[i-1,j] = quad above row i

            q_pad_d = np.zeros((gh - 1, gw), dtype=bool)   # down-flank quads
            q_pad_d[:, :-1] = qv                            # qv[i,j] = quad right of col j
            q_pad_u = np.zeros((gh - 1, gw), dtype=bool)
            q_pad_u[:, 1:] = qv                             # qv[i,j-1] = quad left of col j

            # ── Horizontal boundary edges  (i, j)–(i, j+1) ────────────────
            ep_h = fv[:, :-1] & fv[:, 1:]                  # (gh, gw-1)
            bnd_h = ep_h & (q_pad_l ^ q_pad_r)
            hi, hj = np.where(bnd_h)
            if hi.size:
                hk0 = hi * gw + hj
                hk1 = hi * gw + hj + 1
                hfi  = _v(ZZ_r,  hk0);  hbi  = _v(ZZB_r, hk0)
                hfi1 = _v(ZZ_r,  hk1);  hbi1 = _v(ZZB_r, hk1)
                hcol = (img_r[hk0] * 0.6 + back_r[hk0] * 0.4 +
                        img_r[hk1] * 0.6 + back_r[hk1] * 0.4) / 2.0

                above = q_pad_l[hi, hj]                     # True → bottom edge
                # above: (fi, bi1, fi1) + (fi, bi, bi1)  + back-face duplicates
                a = np.where(above)[0]
                ha_t1 = np.stack([hfi[a], hbi1[a], hfi1[a]], axis=1)
                ha_t2 = np.stack([hfi[a], hbi[a],  hbi1[a]], axis=1)
                ha_t1r = np.stack([hfi1[a], hbi1[a], hfi[a]], axis=1)   # reversed
                ha_t2r = np.stack([hbi1[a], hbi[a],  hfi[a]], axis=1)   # reversed
                ha_c  = np.concatenate([hcol[a]] * 4, axis=0)
                # below: (fi, fi1, bi1) + (fi, bi1, bi)  + back-face duplicates
                b = np.where(~above)[0]
                hb_t1 = np.stack([hfi[b], hfi1[b], hbi1[b]], axis=1)
                hb_t2 = np.stack([hfi[b], hbi1[b], hbi[b]],  axis=1)
                hb_t1r = np.stack([hbi1[b], hfi1[b], hfi[b]], axis=1)   # reversed
                hb_t2r = np.stack([hbi[b],  hbi1[b], hfi[b]], axis=1)   # reversed
                hb_c  = np.concatenate([hcol[b]] * 4, axis=0)

                seam_t = np.concatenate([ha_t1, ha_t2, ha_t1r, ha_t2r,
                                         hb_t1, hb_t2, hb_t1r, hb_t2r], axis=0)
                seam_c = np.concatenate([ha_c, hb_c], axis=0)
                all_tris   = np.concatenate([all_tris, seam_t],   axis=0)
                all_colors = np.concatenate([all_colors, seam_c], axis=0)

            # ── Vertical boundary edges  (i, j)–(i+1, j) ──────────────────
            ep_v = fv[:-1, :] & fv[1:, :]                  # (gh-1, gw)
            bnd_v = ep_v & (q_pad_u ^ q_pad_d)
            vi, vj = np.where(bnd_v)
            if vi.size:
                vk0 = vi * gw + vj
                vk1 = (vi + 1) * gw + vj
                vfi  = _v(ZZ_r,  vk0);  vbi  = _v(ZZB_r, vk0)
                vfi1 = _v(ZZ_r,  vk1);  vbi1 = _v(ZZB_r, vk1)
                vcol = (img_r[vk0] * 0.6 + back_r[vk0] * 0.4 +
                        img_r[vk1] * 0.6 + back_r[vk1] * 0.4) / 2.0

                onright = q_pad_d[vi, vj]                   # True → left boundary
                # right: (fi, bi1, fi1) + (fi, bi, bi1)  + back-face duplicates
                r = np.where(onright)[0]
                vr_t1 = np.stack([vfi[r], vbi1[r], vfi1[r]], axis=1)
                vr_t2 = np.stack([vfi[r], vbi[r],  vbi1[r]], axis=1)
                vr_t1r = np.stack([vfi1[r], vbi1[r], vfi[r]], axis=1)   # reversed
                vr_t2r = np.stack([vbi1[r], vbi[r],  vfi[r]], axis=1)   # reversed
                vr_c  = np.concatenate([vcol[r]] * 4, axis=0)
                # left: (fi, fi1, bi1) + (fi, bi1, bi)  + back-face duplicates
                l = np.where(~onright)[0]
                vl_t1 = np.stack([vfi[l], vfi1[l], vbi1[l]], axis=1)
                vl_t2 = np.stack([vfi[l], vbi1[l], vbi[l]],  axis=1)
                vl_t1r = np.stack([vbi1[l], vfi1[l], vfi[l]], axis=1)   # reversed
                vl_t2r = np.stack([vbi[l],  vbi1[l], vfi[l]], axis=1)   # reversed
                vl_c  = np.concatenate([vcol[l]] * 4, axis=0)

                seam_t = np.concatenate([vr_t1, vr_t2, vr_t1r, vr_t2r,
                                         vl_t1, vl_t2, vl_t1r, vl_t2r], axis=0)
                seam_c = np.concatenate([vr_c, vl_c], axis=0)
                all_tris   = np.concatenate([all_tris, seam_t],   axis=0)
                all_colors = np.concatenate([all_colors, seam_c], axis=0)

        # ── Render unified mesh ────────────────────────────────────────────
        rgba = np.concatenate(
            [np.clip(all_colors, 0.0, 1.0),
             np.ones((len(all_colors), 1), dtype=np.float32)], axis=1)
        coll = Poly3DCollection(all_tris, linewidths=0)
        coll.set_facecolor(rgba)
        ax.add_collection3d(coll)

        # Auto-scale axes to the mesh extent
        pts = all_tris.reshape(-1, 3)
        for setter, col in zip(
                [ax.set_xlim3d, ax.set_ylim3d, ax.set_zlim3d],
                [pts[:, 0],     pts[:, 1],     pts[:, 2]]):
            mn, mx = float(col.min()), float(col.max())
            mid = (mn + mx) / 2
            half = max((mx - mn) / 2, 1e-3)
            setter(mid - half, mid + half)

        _init_az = -60.0
        ax.view_init(elev=20, azim=_init_az)
        self._canvas.draw()

    # ── Shared ZZ helper ──────────────────────────────────────
    def _compute_ZZ(self):
        """
        Re-computes the same ZZ (and ZZ_back) arrays used in _draw(), so
        export_3d() always produces geometry that matches the on-screen view.

        Returns (ZZ, ZZ_back, mask) where background pixels are np.nan.
        """
        import numpy as np

        # Feathering radius — must match _draw()
        _EDGE_FEATHER_PX = 3

        depth_raw = (1.0 - self._depth_np) if self.invert else self._depth_np
        mask      = getattr(self, '_bg_mask', None)

        try:
            from PIL import ImageFilter as _IFD
            _dp = PILImage.fromarray((depth_raw * 255).astype(np.uint8))
            depth = np.array(_dp.filter(_IFD.GaussianBlur(radius=1.5)),
                             dtype=np.float32) / 255.0
        except Exception:
            depth = depth_raw

        # Normalize to [0, 1] — must match _draw()
        _d_min, _d_max = float(depth.min()), float(depth.max())
        if _d_max - _d_min > 1e-6:
            depth = (depth - _d_min) / (_d_max - _d_min)

        ZZ = np.sqrt(depth) * self.depth_scale * 0.5

        if mask is not None and _EDGE_FEATHER_PX > 0:
            char = (~mask).astype(np.float32)
            dist = np.zeros_like(char)
            cur  = char.copy()
            for step in range(1, _EDGE_FEATHER_PX + 1):
                pad    = np.pad(cur, 1, constant_values=0.0)
                eroded = (pad[:-2,1:-1] * pad[2:,1:-1]
                          * pad[1:-1,:-2] * pad[1:-1,2:] * cur)
                ring   = cur - eroded
                dist   = np.where(ring > 0, float(step), dist)
                cur    = eroded
            dist = np.where((cur > 0) & (dist == 0), float(_EDGE_FEATHER_PX), dist)
            ZZ = ZZ * np.clip(dist / _EDGE_FEATHER_PX, 0.0, 1.0)

        if mask is not None:
            ZZ = np.where(mask, np.nan, ZZ)

        return ZZ, -ZZ, mask

    # ── 3D Export ─────────────────────────────────────────────
    def export_3d(self, path: str):
        """
        Exports the current 3D model to *path*.

        Supported formats (chosen by file extension):
          .glb  — Binary glTF  (preferred; requires trimesh)
          .obj  — Wavefront OBJ + .mtl + texture PNG (pure-Python fallback)

        The exported mesh is a closed, textured object with:
          • Front surface  — mapped to the left  half of the texture atlas
          • Back  surface  — mapped to the right half of the texture atlas
          • Side walls     — connecting quads along the silhouette
        """
        import numpy as np
        import os

        ZZ, ZZ_back, mask = self._compute_ZZ()
        gh, gw = ZZ.shape

        img_np  = self._img_np                         # (gh, gw, 3) float32
        back_np = self._back_np if hasattr(self, '_back_np') else img_np[:, ::-1]

        # ── UV inset map ───────────────────────────────────────
        # For each grid position (i,j) this maps to the nearest pixel that
        # is >= 3 px deep inside the character silhouette.  Boundary pixels
        # (anti-aliased fringe) are redirected to their nearest solid-
        # interior neighbour; interior pixels map to themselves (no change).
        # Using these redirected coordinates in the UV formula forces seam
        # and boundary vertices to sample solid, non-halo colors.
        _INSET_PX = 3
        if mask is not None:
            row_ins, col_ins = ThreeDViewerWidget._compute_inset_map(
                ~mask, inset_px=_INSET_PX)
        else:
            row_ins = np.tile(np.arange(gh)[:, None], (1, gw)).astype(np.int32)
            col_ins = np.tile(np.arange(gw)[None, :], (gh, 1)).astype(np.int32)

        # ── 1. Build vertex + UV lists ─────────────────────────
        # Index grids: -1 where vertex is absent (NaN / background).
        front_idx = np.full((gh, gw), -1, dtype=np.int32)
        back_idx  = np.full((gh, gw), -1, dtype=np.int32)

        verts, uvs = [], []
        _gw1 = max(gw - 1, 1)
        _gh1 = max(gh - 1, 1)

        # Front vertices
        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ[i, j]):
                    front_idx[i, j] = len(verts)
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ[i, j])))
                    # Atlas left-half UV [u=0..0.5], inset to avoid halo
                    ci, ri = int(col_ins[i, j]), int(row_ins[i, j])
                    uvs.append((ci / _gw1 * 0.5,
                                1.0 - ri / _gh1))

        n_front = len(verts)

        # Back vertices (same XY, flipped Z)
        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ_back[i, j]):
                    back_idx[i, j] = len(verts) - n_front  # offset stored
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ_back[i, j])))
                    # Atlas right-half UV [u=0.5..1.0], inset to avoid halo
                    ci, ri = int(col_ins[i, j]), int(row_ins[i, j])
                    uvs.append((0.5 + ci / _gw1 * 0.5,
                                1.0 - ri / _gh1))

        # ── 2. Build face lists ────────────────────────────────
        faces = []

        # Front faces — CCW winding (outward normal +Z)
        for i in range(gh - 1):
            for j in range(gw - 1):
                i0 = front_idx[i,   j  ]
                i1 = front_idx[i+1, j  ]
                i2 = front_idx[i,   j+1]
                i3 = front_idx[i+1, j+1]
                if i0 >= 0 and i1 >= 0 and i2 >= 0 and i3 >= 0:
                    faces.append((i0, i2, i3))
                    faces.append((i0, i3, i1))

        # Back faces — reversed winding (outward normal −Z)
        for i in range(gh - 1):
            for j in range(gw - 1):
                b0 = back_idx[i,   j  ]
                b1 = back_idx[i+1, j  ]
                b2 = back_idx[i,   j+1]
                b3 = back_idx[i+1, j+1]
                if b0 >= 0 and b1 >= 0 and b2 >= 0 and b3 >= 0:
                    # Apply n_front offset to convert to global index
                    g0, g1, g2, g3 = (b0+n_front, b1+n_front,
                                      b2+n_front, b3+n_front)
                    faces.append((g0, g3, g2))   # reversed
                    faces.append((g0, g1, g3))

        # ── Phase 2: Boundary-edge stitching (manifold seam) ──────────────
        # seam_fwd — correctly-wound seam faces (used for both GLB and OBJ)
        # seam_dup — reversed duplicates for OBJ (GLB uses doubleSided=True)
        seam_fwd, seam_dup = [], []
        if mask is not None:
            def _fq(ii, jj):
                """True when front quad (ii,jj)→(ii+1,jj+1) has all 4 valid corners."""
                return (0 <= ii < gh - 1 and 0 <= jj < gw - 1 and
                        front_idx[ii,   jj  ] >= 0 and
                        front_idx[ii+1, jj  ] >= 0 and
                        front_idx[ii,   jj+1] >= 0 and
                        front_idx[ii+1, jj+1] >= 0)

            # ── Horizontal boundary edges  (i,j)–(i,j+1) ──────────────────
            for i in range(gh):
                for j in range(gw - 1):
                    if front_idx[i, j] < 0 or front_idx[i, j+1] < 0:
                        continue
                    above = _fq(i - 1, j)
                    below = _fq(i,     j)
                    if above == below:
                        continue
                    fi  = front_idx[i, j]
                    fi1 = front_idx[i, j+1]
                    bi  = back_idx[i, j]   + n_front
                    bi1 = back_idx[i, j+1] + n_front
                    if above:
                        seam_fwd += [(fi, bi1, fi1), (fi, bi, bi1)]
                        seam_dup += [(fi1, bi1, fi), (bi1, bi, fi)]
                    else:
                        seam_fwd += [(fi, fi1, bi1), (fi, bi1, bi)]
                        seam_dup += [(bi1, fi1, fi), (bi, bi1, fi)]

            # ── Vertical boundary edges  (i,j)–(i+1,j) ────────────────────
            for i in range(gh - 1):
                for j in range(gw):
                    if front_idx[i, j] < 0 or front_idx[i+1, j] < 0:
                        continue
                    left  = _fq(i, j - 1)
                    right = _fq(i, j    )
                    if left == right:
                        continue
                    fi  = front_idx[i,   j]
                    fi1 = front_idx[i+1, j]
                    bi  = back_idx[i,   j] + n_front
                    bi1 = back_idx[i+1, j] + n_front
                    if right:
                        seam_fwd += [(fi, bi1, fi1), (fi, bi, bi1)]
                        seam_dup += [(fi1, bi1, fi), (bi1, bi, fi)]
                    else:
                        seam_fwd += [(fi, fi1, bi1), (fi, bi1, bi)]
                        seam_dup += [(bi1, fi1, fi), (bi, bi1, fi)]

        verts_np      = np.array(verts,                          dtype=np.float32)
        uvs_np        = np.array(uvs,                            dtype=np.float32)
        # GLB: forward seam only — doubleSided material removes culling;
        #      clean winding lets trimesh compute smooth vertex normals.
        # OBJ: include reversed duplicates (no doubleSided flag in .mtl).
        faces_glb_np  = np.array(faces + seam_fwd,              dtype=np.int32)
        faces_obj_np  = np.array(faces + seam_fwd + seam_dup,   dtype=np.int32)

        # ── 3. Texture atlas (with edge dilation) ──────────────────────
        # Expand character colors 3 pixels outward into the background so
        # seam-boundary UVs sample solid color instead of the white halo
        # left by anti-aliasing against the background.
        if mask is not None:
            img_dil  = ThreeDViewerWidget._dilate_texture(img_np,  ~mask, iterations=3)
            back_dil = ThreeDViewerWidget._dilate_texture(back_np, ~mask, iterations=3)
        else:
            img_dil, back_dil = img_np, back_np

        atlas_np  = np.concatenate([img_dil, back_dil], axis=1)  # (gh, 2*gw, 3)
        atlas_pil = PILImage.fromarray((atlas_np * 255).astype(np.uint8))

        # ── 4. Export ──────────────────────────────────────────
        ext = os.path.splitext(path)[1].lower()

        # ── GLB via trimesh ────────────────────────────────────
        if ext == '.glb':
            try:
                import trimesh
                import trimesh.visual
                import trimesh.visual.material as _tvm

                # Always export as RGB — an RGBA texture causes some viewers to
                # default to ALPHA_BLEND mode, breaking depth-buffer ordering.
                atlas_rgb = atlas_pil.convert('RGB')

                # PBR material: OPAQUE alpha so the depth buffer is never
                # bypassed, and doubleSided so seam faces are visible from
                # both the inside and outside without back-face culling.
                mat = _tvm.PBRMaterial(
                    baseColorTexture = atlas_rgb,
                    alphaMode        = 'OPAQUE',
                    doubleSided      = True,
                )

                mesh = trimesh.Trimesh(
                    vertices = verts_np,
                    faces    = faces_glb_np,
                    process  = False,
                )
                mesh.visual = trimesh.visual.TextureVisuals(
                    uv       = uvs_np,
                    material = mat,
                )
                mesh.export(path)
                return

            except ImportError:
                # trimesh not installed → fall through to OBJ
                path = os.path.splitext(path)[0] + '.obj'
                ext  = '.obj'

        # ── OBJ + MTL (pure-Python fallback) ──────────────────
        if ext in ('.obj', ''):
            base   = os.path.splitext(path)[0]
            mtl_p  = base + '.mtl'
            tex_p  = base + '_texture.png'

            # Save texture as RGB — no alpha channel in the OBJ texture either
            atlas_pil.convert('RGB').save(tex_p)

            with open(mtl_p, 'w') as f:
                f.write(f"newmtl mat0\n"
                        f"Ka 1 1 1\nKd 1 1 1\nKs 0 0 0\n"
                        f"d 1.0\n"          # fully opaque — no transparency
                        f"illum 2\n"        # full lighting model
                        f"map_Kd {os.path.basename(tex_p)}\n")

            with open(path if path.endswith('.obj') else path+'.obj', 'w') as f:
                f.write(f"mtllib {os.path.basename(mtl_p)}\n")
                f.write("usemtl mat0\n")
                for (x, y, z) in verts_np:
                    f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
                for (u, v) in uvs_np:
                    f.write(f"vt {u:.6f} {v:.6f}\n")
                for (a, b, c) in faces_obj_np:
                    # OBJ is 1-indexed; include UV index = vertex index
                    f.write(f"f {a+1}/{a+1} {b+1}/{b+1} {c+1}/{c+1}\n")

    # ── STL Export (3D-print) ─────────────────────────────────
    def export_stl(self, path: str, target_mm: float = 100.0):
        """
        Exports the current mesh as a binary STL file scaled so that the
        longest bounding-box dimension equals *target_mm* millimetres.

        STL carries no color or UV data; only vertices and face normals
        are written.  The face list uses forward-wound seam faces only
        (no backface duplicates) so the mesh stays manifold/watertight —
        a hard requirement for FDM slicers such as Cura and PrusaSlicer.

        Scale: 100 mm default → desktop-toy / enamel-pin size (~10 cm).
        Slicers interpret STL units as mm, so 100 units ≡ 100 mm.
        """
        import numpy as np
        import os

        ZZ, ZZ_back, mask = self._compute_ZZ()
        gh, gw = ZZ.shape

        # ── Build vertices (no UVs needed for STL) ────────────
        front_idx = np.full((gh, gw), -1, dtype=np.int32)
        back_idx  = np.full((gh, gw), -1, dtype=np.int32)
        verts     = []

        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ[i, j]):
                    front_idx[i, j] = len(verts)
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ[i, j])))
        n_front = len(verts)

        for i in range(gh):
            for j in range(gw):
                if np.isfinite(ZZ_back[i, j]):
                    back_idx[i, j] = len(verts) - n_front
                    verts.append((float(self._XX[i, j]),
                                  float(self._YY[i, j]),
                                  float(ZZ_back[i, j])))

        # ── Build faces (manifold — forward seam only) ─────────
        faces = []

        for i in range(gh - 1):              # front
            for j in range(gw - 1):
                i0, i1 = front_idx[i, j], front_idx[i+1, j]
                i2, i3 = front_idx[i, j+1], front_idx[i+1, j+1]
                if i0 >= 0 and i1 >= 0 and i2 >= 0 and i3 >= 0:
                    faces += [(i0, i2, i3), (i0, i3, i1)]

        for i in range(gh - 1):              # back (reversed winding)
            for j in range(gw - 1):
                b0 = back_idx[i,   j  ]
                b1 = back_idx[i+1, j  ]
                b2 = back_idx[i,   j+1]
                b3 = back_idx[i+1, j+1]
                if b0 >= 0 and b1 >= 0 and b2 >= 0 and b3 >= 0:
                    g0, g1 = b0+n_front, b1+n_front
                    g2, g3 = b2+n_front, b3+n_front
                    faces += [(g0, g3, g2), (g0, g1, g3)]

        if mask is not None:                 # seam (forward only)
            def _fq(ii, jj):
                return (0 <= ii < gh-1 and 0 <= jj < gw-1 and
                        front_idx[ii, jj] >= 0 and front_idx[ii+1, jj] >= 0 and
                        front_idx[ii, jj+1] >= 0 and front_idx[ii+1, jj+1] >= 0)
            for i in range(gh):
                for j in range(gw - 1):
                    if front_idx[i, j] < 0 or front_idx[i, j+1] < 0:
                        continue
                    above, below = _fq(i-1, j), _fq(i, j)
                    if above == below:
                        continue
                    fi, fi1 = front_idx[i, j], front_idx[i, j+1]
                    bi  = back_idx[i, j]   + n_front
                    bi1 = back_idx[i, j+1] + n_front
                    if above:
                        faces += [(fi, bi1, fi1), (fi, bi, bi1)]
                    else:
                        faces += [(fi, fi1, bi1), (fi, bi1, bi)]
            for i in range(gh - 1):
                for j in range(gw):
                    if front_idx[i, j] < 0 or front_idx[i+1, j] < 0:
                        continue
                    left, right = _fq(i, j-1), _fq(i, j)
                    if left == right:
                        continue
                    fi, fi1 = front_idx[i, j], front_idx[i+1, j]
                    bi  = back_idx[i,   j] + n_front
                    bi1 = back_idx[i+1, j] + n_front
                    if right:
                        faces += [(fi, bi1, fi1), (fi, bi, bi1)]
                    else:
                        faces += [(fi, fi1, bi1), (fi, bi1, bi)]

        verts_np = np.array(verts, dtype=np.float32)
        faces_np = np.array(faces, dtype=np.int32)

        # ── Scale to target_mm on the longest dimension ────────
        lo, hi   = verts_np.min(axis=0), verts_np.max(axis=0)
        max_dim  = float((hi - lo).max())
        scale    = target_mm / max(max_dim, 1e-9)
        verts_mm = verts_np * scale           # units are now millimetres

        # ── Write binary STL ───────────────────────────────────
        try:
            import trimesh as _tm
            mesh = _tm.Trimesh(vertices=verts_mm, faces=faces_np, process=False)
            mesh.export(path)
        except ImportError:
            # stdlib-only fallback: write binary STL with struct
            import struct
            tv = verts_mm[faces_np]           # (N, 3, 3)  triangle vertices
            e1 = tv[:, 1] - tv[:, 0]
            e2 = tv[:, 2] - tv[:, 0]
            normals = np.cross(e1, e2)
            nlen = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = normals / np.where(nlen > 0, nlen, 1.0)
            with open(path, 'wb') as f:
                f.write(b'\x00' * 80)         # 80-byte header
                f.write(struct.pack('<I', len(faces_np)))
                for k in range(len(faces_np)):
                    nx, ny, nz = normals[k]
                    f.write(struct.pack('<3f', nx, ny, nz))
                    for v in tv[k]:
                        f.write(struct.pack('<3f', *v))
                    f.write(struct.pack('<H', 0))

    # ─────────────────────────────────────────────────────────

    def update_depth_scale(self, scale: float, invert: bool = False,
                           show_back: bool = True):
        self.depth_scale = scale
        self.invert      = invert
        self.show_back   = show_back
        self._draw()

    def set_ai_back(self, pil_img):
        """Ersetzt die generierte Rückseite durch ein KI-synthetisiertes Bild."""
        import numpy as np
        gw = self._img_np.shape[1]
        gh = self._img_np.shape[0]
        back_resized = pil_img.convert("RGB").resize((gw, gh), PILImage.Resampling.LANCZOS)
        self._back_np      = np.array(back_resized, dtype=np.float32) / 255.0
        # Fix 5: derive the transparency mask from the AI image itself so that
        # the back surface is transparent in the right places (not based on a
        # flipped copy of the front mask, which may not match the AI output).
        self._back_bg_mask = ThreeDViewerWidget._detect_bg(self._back_np)
        self._draw()


class ThreeDModelDialog(QDialog):
    """
    2D → 3D Modell-Viewer (Beta)

    Öffnet das aktuelle Bild, schätzt eine Tiefenkarte mit KI (Depth-Anything-V2
    oder MiDaS) und zeigt das Ergebnis als interaktives 3D-Mesh.
    """

    def __init__(self, parent, editor):
        super().__init__(parent)
        self.editor   = editor
        self._worker:       "DepthWorker | None"     = None
        self._novel_worker: "NovelViewWorker | None" = None
        self._viewer:       "ThreeDViewerWidget | None" = None
        self._depth_map = None
        self._img       = None

        self.setWindowTitle("🧊  2D → 3D Modell (Beta)")
        self.setModal(True)
        self.resize(1000, 700)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(10)

        # ── Linke Seite: Steuerung ────────────────────────────
        ctrl_w = QWidget(); ctrl_w.setFixedWidth(270)
        ctrl   = QVBoxLayout(ctrl_w); ctrl.setSpacing(8)

        _btn = ("QPushButton { background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                "border-radius:4px; padding:8px; font-size:12px; } "
                "QPushButton:hover { background:#3a3a3a; }")

        info = QLabel(
            "Konvertiert das aktuelle Bild in ein\n"
            "interaktives 3D-Modell.\n\n"
            "🟢 Beste Qualität (empfohlen):\n"
            "  pip install transformers torch\n"
            "  → Depth-Anything-V2 (~100 MB)\n\n"
            "🟡 Fallback:\n"
            "  pip install torch timm\n"
            "  → MiDaS\n\n"
            "🔴 Immer verfügbar:\n"
            "  Luminanz-Approximation\n"
            "  (geringe Qualität)\n\n"
            "3D-Viewer:\n"
            "  pip install PyOpenGL\n"
            "  pip install PyOpenGL_accelerate"
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "color:#888; font-size:10px; background:#141414; "
            "border-radius:3px; padding:6px;"
        )
        ctrl.addWidget(info)

        # Tiefen-Effekt Stärke
        sp_s = "background:#2d2d2d; color:#ddd; border:1px solid #333; padding:2px;"
        ctrl.addWidget(QLabel("Tiefen-Effekt Stärke:"))
        self._depth_slider = QSlider(Qt.Orientation.Horizontal)
        self._depth_slider.setRange(5, 30)
        self._depth_slider.setValue(20)
        self._depth_slider.setStyleSheet(
            "QSlider::groove:horizontal { background:#3a3a3a; height:4px; border-radius:2px; }"
            "QSlider::handle:horizontal { background:#4fc3f7; width:14px; height:14px; "
            "margin:-5px 0; border-radius:7px; }"
        )
        self._depth_slider.valueChanged.connect(self._on_depth_slider)
        ctrl.addWidget(self._depth_slider)

        # Tiefe invertieren (für helle Hintergründe / Produktfotos)
        from PyQt6.QtWidgets import QCheckBox
        self._chk_invert = QCheckBox("🔄  Tiefe invertieren")
        self._chk_invert.setStyleSheet("color:#ddd; font-size:11px;")
        self._chk_invert.setToolTip(
            "Aktivieren bei weißem/hellem Hintergrund:\n"
            "Depth-Anything behandelt helle Flächen als 'nah'.\n"
            "Invertieren schiebt den Hintergrund zurück.")
        self._chk_invert.stateChanged.connect(self._on_invert_changed)
        ctrl.addWidget(self._chk_invert)

        self._chk_back = QCheckBox("🔲  Generierte Rückseite")
        self._chk_back.setStyleSheet("color:#ddd; font-size:11px;")
        self._chk_back.setChecked(True)
        self._chk_back.setToolTip(
            "Fügt eine generierte Rückseite hinzu:\n"
            "• Horizontal gespiegelt\n"
            "• Weichgezeichnet (fehlende Details)\n"
            "• Desaturiert + abgedunkelt\n\n"
            "Keine KI — plausible Annäherung.")
        self._chk_back.stateChanged.connect(self._on_invert_changed)
        ctrl.addWidget(self._chk_back)

        # Mesh-Auflösung
        ctrl.addWidget(QLabel("Mesh-Auflösung (Grid-Größe):"))
        self._sp_resolution = QSpinBox()
        self._sp_resolution.setRange(50, 300)
        self._sp_resolution.setValue(200)
        self._sp_resolution.setSuffix(" px")
        self._sp_resolution.setStyleSheet(sp_s)
        ctrl.addWidget(self._sp_resolution)

        # Status
        self._lbl_status = QLabel("Bereit.")
        self._lbl_status.setStyleSheet("color:#4fc3f7; font-size:10px;")
        self._lbl_status.setWordWrap(True)
        ctrl.addWidget(self._lbl_status)

        # Starten
        btn_gen = QPushButton("🧊  3D-Modell erstellen")
        btn_gen.setStyleSheet(
            "QPushButton { background:#1a3a1a; color:#90ee90; border:1px solid #2a5a2a; "
            "border-radius:4px; padding:10px; font-size:13px; font-weight:bold; } "
            "QPushButton:hover { background:#2a4a2a; }"
        )
        btn_gen.clicked.connect(self._start)
        ctrl.addWidget(btn_gen)

        # Tiefenkarte anzeigen
        self._btn_show_depth = QPushButton("🗺  Tiefenkarte anzeigen")
        self._btn_show_depth.setStyleSheet(_btn)
        self._btn_show_depth.clicked.connect(self._show_depth_map)
        self._btn_show_depth.setEnabled(False)
        ctrl.addWidget(self._btn_show_depth)

        # KI-Rückseite generieren
        self._btn_ai_back = QPushButton("🤖  KI-Rückseite generieren")
        self._btn_ai_back.setStyleSheet(
            "QPushButton { background:#1a2a3a; color:#7ec8e3; border:1px solid #2a4a5a; "
            "border-radius:4px; padding:8px; font-size:12px; } "
            "QPushButton:hover { background:#2a3a4a; } "
            "QPushButton:disabled { background:#1a1a1a; color:#444; border-color:#2a2a2a; }"
        )
        self._btn_ai_back.setToolTip(
            "Generiert eine echte KI-Rückseite mit Novel View Synthesis.\n\n"
            "Benötigt (einmalig):\n"
            "  pip install diffusers transformers accelerate\n"
            "  pip install torch torchvision --index-url\n"
            "    https://download.pytorch.org/whl/cu128\n\n"
            "Modelle (automatischer Download):\n"
            "  • zero123plus-v1.1   (~1.8 GB, primär)\n"
            "  • Zero-1-to-3 XL     (~5 GB, Fallback)\n\n"
            "NVIDIA-GPU mit ≥4 GB VRAM empfohlen."
        )
        self._btn_ai_back.clicked.connect(self._start_novel_view)
        self._btn_ai_back.setEnabled(False)
        ctrl.addWidget(self._btn_ai_back)

        # Als 3D exportieren
        self._btn_export = QPushButton("💾  Als 3D exportieren …")
        self._btn_export.setStyleSheet(
            "QPushButton { background:#2a1a3a; color:#c39bd3; border:1px solid #4a2a5a; "
            "border-radius:4px; padding:8px; font-size:12px; } "
            "QPushButton:hover { background:#3a2a4a; } "
            "QPushButton:disabled { background:#1a1a1a; color:#444; border-color:#2a2a2a; }"
        )
        self._btn_export.setToolTip(
            "Exportiert das 3D-Modell als:\n"
            "  • .glb  (Binary glTF — bevorzugt, braucht trimesh)\n"
            "  • .obj  (Wavefront OBJ + MTL + Textur-PNG)\n\n"
            "pip install trimesh  →  für GLB-Export"
        )
        self._btn_export.clicked.connect(self._export_3d)
        self._btn_export.setEnabled(False)
        ctrl.addWidget(self._btn_export)

        # 3D-Druck STL-Export
        self._btn_stl = QPushButton("🖨  Export for 3D Printing (.stl)")
        self._btn_stl.setStyleSheet(
            "QPushButton { background:#2a1a0a; color:#f0a830; border:1px solid #5a3a0a; "
            "border-radius:4px; padding:8px; font-size:12px; } "
            "QPushButton:hover { background:#3a2a0a; } "
            "QPushButton:disabled { background:#1a1a1a; color:#444; border-color:#2a2a2a; }"
        )
        self._btn_stl.setToolTip(
            "Exportiert das Mesh als binäres STL für 3D-Drucker-Slicer\n"
            "(Cura, PrusaSlicer, Bambu Studio, …).\n\n"
            "• Keine Farben / UVs — reine Geometrie\n"
            "• Skaliert auf 100 mm längste Seite\n"
            "• Watertight / manifold — druckfertig\n\n"
            "Benötigt trimesh (empfohlen) oder nutzt stdlib-Fallback."
        )
        self._btn_stl.clicked.connect(self._export_stl)
        self._btn_stl.setEnabled(False)
        ctrl.addWidget(self._btn_stl)

        nav = QLabel("Steuerung:\n• Maus ziehen = Rotation\n• Mausrad = Zoom")
        nav.setStyleSheet("color:#555; font-size:9px;")
        ctrl.addWidget(nav)
        ctrl.addStretch()
        root.addWidget(ctrl_w)

        # ── Rechte Seite: persistenter Container ─────────────
        # Der Container bleibt immer im root-Layout.
        # Nur sein Inhalt (Placeholder ↔ Viewer) wird getauscht.
        self._right_panel = QWidget()
        self._right_panel.setMinimumSize(640, 480)
        self._right_lay = QVBoxLayout(self._right_panel)
        self._right_lay.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel(
            "3D-Modell erscheint hier nach der Berechnung.\n\n"
            "Klicke auf '🧊 3D-Modell erstellen'."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "background:#0a0a0a; border:1px solid #2a2a2a; color:#444; font-size:14px;"
        )
        self._right_lay.addWidget(self._placeholder)
        root.addWidget(self._right_panel, 1)

    # ── Tiefenschätzung starten ──────────────────────────────

    @staticmethod
    def _preload_torch():
        """
        Lädt torch im Haupt-Thread vor, bevor ein QThread startet.
        Auf Windows schlägt die DLL-Initialisierung fehl, wenn torch
        erstmals in einem Qt-C++-Thread (nicht im Python-Haupt-Thread)
        importiert wird.  Durch den Vorab-Import ist c10.dll danach
        bereits im Prozess und der Thread-Import funktioniert.
        """
        try:
            import torch          # noqa: F401
            import transformers   # noqa: F401
        except Exception:
            pass

    def _start(self):
        if not self.editor.layers:
            return

        self._preload_torch()
        img = self.editor._composite_layers().convert("RGB")
        # Auf max 768px begrenzen für Performance
        if max(img.size) > 768:
            img = img.copy()
            img.thumbnail((768, 768), PILImage.Resampling.LANCZOS)

        self._img = img
        self._lbl_status.setText("⏳ Starte Tiefenschätzung …")

        if self._worker and self._worker.isRunning():
            self._worker.terminate()

        self._worker = DepthWorker(img)
        self._worker.progress.connect(self._lbl_status.setText)
        self._worker.depth_ready.connect(self._on_depth_ready)
        self._worker.error.connect(lambda e: (
            self._lbl_status.setText(f"❌ {e}"),
            QMessageBox.critical(self, "Fehler", e),
        ))
        self._worker.start()

    def _on_depth_ready(self, depth_map):
        self._depth_map = depth_map
        self._btn_show_depth.setEnabled(True)
        self._btn_ai_back.setEnabled(True)
        self._lbl_status.setText("✅ Tiefenkarte bereit — lade 3D-Viewer …")
        self._show_viewer()

    def _show_viewer(self):
        scale = self._depth_slider.value() / 100.0

        # Alten Viewer aus Container entfernen
        if self._viewer:
            self._right_lay.removeWidget(self._viewer)
            self._viewer.hide()
            self._viewer.deleteLater()
            self._viewer = None

        # Platzhalter verstecken
        self._placeholder.hide()
        self._right_lay.removeWidget(self._placeholder)

        # Neuen Viewer in Container einsetzen — parent=_right_panel hält ihn am Leben
        self._viewer = ThreeDViewerWidget(
            self._img, self._depth_map,
            depth_scale=scale,
            invert=self._chk_invert.isChecked(),
            show_back=self._chk_back.isChecked(),
            parent=self._right_panel
        )
        self._right_lay.addWidget(self._viewer)
        self._btn_export.setEnabled(True)
        self._btn_stl.setEnabled(True)
        self._lbl_status.setText(
            "✅ Modell bereit.\n"
            "Maus ziehen = Rotation | Mausrad = Zoom"
        )

    def _on_depth_slider(self, val: int):
        if self._viewer:
            self._viewer.update_depth_scale(val / 100.0,
                                            self._chk_invert.isChecked(),
                                            self._chk_back.isChecked())

    def _on_invert_changed(self):
        if self._viewer:
            self._viewer.update_depth_scale(self._depth_slider.value() / 100.0,
                                            self._chk_invert.isChecked(),
                                            self._chk_back.isChecked())

    def _start_novel_view(self):
        """Startet den NovelViewWorker für KI-generierte Rückseite."""
        if self._img is None:
            return

        self._preload_torch()

        if self._novel_worker and self._novel_worker.isRunning():
            self._novel_worker.terminate()

        self._btn_ai_back.setEnabled(False)
        self._lbl_status.setText("⏳ Starte KI-Rückseiten-Generierung …")

        self._novel_worker = NovelViewWorker(self._img)
        self._novel_worker.progress.connect(self._lbl_status.setText)
        self._novel_worker.views_ready.connect(self._on_novel_view_ready)
        self._novel_worker.error.connect(self._on_novel_view_error)
        self._novel_worker.start()

    def _on_novel_view_ready(self, back_pil):
        """Wendet die KI-generierte Rückseite auf den Viewer an."""
        self._btn_ai_back.setEnabled(True)
        if self._viewer:
            self._viewer.set_ai_back(back_pil)
            self._lbl_status.setText(
                "✅ KI-Rückseite angewendet!\n"
                "Modell auf ~180° drehen um sie zu sehen."
            )
        else:
            self._lbl_status.setText("✅ KI-Rückseite bereit (3D-Modell noch nicht erstellt).")

    def _on_novel_view_error(self, msg: str):
        self._btn_ai_back.setEnabled(True)
        self._lbl_status.setText(f"❌ KI-Rückseite fehlgeschlagen.")
        QMessageBox.critical(
            self, "KI-Rückseite fehlgeschlagen",
            f"Novel View Synthesis fehlgeschlagen:\n\n{msg}\n\n"
            "Bitte installieren:\n"
            "  pip install diffusers transformers accelerate\n"
            "  pip install torch torchvision "
            "--index-url https://download.pytorch.org/whl/cu128"
        )

    def _export_3d(self):
        if self._viewer is None:
            return
        try:
            import trimesh as _tm  # noqa: F401
            tri_ok = True
        except ImportError:
            tri_ok = False

        filters = []
        if tri_ok:
            filters.append("Binary glTF (*.glb)")
        filters.append("Wavefront OBJ (*.obj)")

        path, _ = QFileDialog.getSaveFileName(
            self, "3D-Modell exportieren", "", ";;".join(filters)
        )
        if not path:
            return

        self._lbl_status.setText("⏳ Exportiere 3D-Modell …")
        QApplication.processEvents()
        try:
            self._viewer.export_3d(path)
            self._lbl_status.setText(f"✅ Exportiert:\n{path}")
        except Exception as e:
            self._lbl_status.setText(f"❌ Export fehlgeschlagen:\n{e}")
            QMessageBox.critical(self, "Export-Fehler", str(e))

    def _export_stl(self):
        if self._viewer is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "3D-Druck STL exportieren", "", "STL-Datei (*.stl)"
        )
        if not path:
            return
        if not path.lower().endswith('.stl'):
            path += '.stl'
        self._lbl_status.setText("⏳ Exportiere STL …")
        QApplication.processEvents()
        try:
            self._viewer.export_stl(path)
            self._lbl_status.setText(f"✅ STL exportiert (100 mm):\n{path}")
        except Exception as e:
            self._lbl_status.setText(f"❌ STL-Export fehlgeschlagen:\n{e}")
            QMessageBox.critical(self, "STL-Export-Fehler", str(e))

    def _show_depth_map(self):
        if self._depth_map is None:
            return
        import numpy as np
        dm_uint8 = (self._depth_map * 255).astype(np.uint8)
        depth_pil = PILImage.fromarray(dm_uint8).convert("RGB")
        dlg = QDialog(self)
        dlg.setWindowTitle("Tiefenkarte (hell = nah, dunkel = fern)")
        dlg.setStyleSheet("background:#1a1a1a; color:#ddd;")
        lay = QVBoxLayout(dlg)
        lbl = QLabel()
        pix = pil_to_qpixmap(depth_pil)
        lbl.setPixmap(pix.scaled(500, 400, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(lbl)
        dlg.exec()


# ══════════════════════════════════════════════════════════════
#  COLLAGE-EDITOR: Mehrere Bilder zu einer Collage zusammenfügen
# ══════════════════════════════════════════════════════════════

class CollageDialog(QDialog):
    """
    Ermöglicht das Erstellen einer Bild-Collage aus mehreren eigenen Fotos.

    Funktionsweise:
    • Raster-Größe wählen (2×2 bis 5×4)
    • Jede Zelle per Klick mit einem eigenen Bild füllen
    • Optional: Alle Zellen auf einmal mit mehreren Dateien befüllen
    • 'Collage erstellen' → PIL setzt alle Zellen zusammen → zurück zum Editor

    Das Ergebnis ersetzt das aktuelle Bild im Editor.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("🖼  Collage-Editor")
        self.setModal(True)
        self.resize(920, 660)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")

        self.result_image = None
        self._grid_rows   = 3
        self._grid_cols   = 3
        self._cell_images = {}   # (row, col) → PIL Image (immer das Original)
        self._cell_labels = {}   # (row, col) → QLabel
        self._cell_combos = {}   # (row, col) → QComboBox (filter per cell)
        self._swap_source = None # (row, col) oder None — Zelle ausgewählt zum Tauschen

        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(8)

        # ── Obere Leiste: Raster + Zellgröße
        top = QHBoxLayout()
        top.addWidget(QLabel("Raster:"))

        self.combo_grid = QComboBox()
        self.combo_grid.setStyleSheet(
            "background:#2d2d2d; color:#ddd; padding:5px 10px; min-width:80px;")
        for lbl in ["2×2", "2×3", "3×2", "3×3", "4×4", "2×4", "4×2", "5×4"]:
            self.combo_grid.addItem(lbl)
        self.combo_grid.setCurrentText("3×3")
        self.combo_grid.currentTextChanged.connect(self._on_grid_change)
        top.addWidget(self.combo_grid)

        top.addSpacing(16)
        top.addWidget(QLabel("Zellgröße (px):"))
        self.spin_size = QSpinBox()
        self.spin_size.setRange(80, 500)
        self.spin_size.setValue(200)
        self.spin_size.setSingleStep(20)
        self.spin_size.setStyleSheet(
            "background:#2d2d2d; color:#ddd; padding:4px; min-width:70px;")
        top.addWidget(self.spin_size)

        self.btn_equal_size = QPushButton("📐  Gleiche Größe")
        self.btn_equal_size.setCheckable(True)
        self.btn_equal_size.setToolTip(
            "Bilder auf exakt gleiche Zellgröße zuschneiden (Crop-Fill).\n"
            "Kein Letterboxing — jede Zelle wird vollständig ausgefüllt.")
        self.btn_equal_size.setStyleSheet("""
            QPushButton          { background:#2d2d2d; color:#888; border:1px solid #3a3a3a;
                                   border-radius:4px; padding:5px 10px; font-size:11px; }
            QPushButton:checked  { background:#1a3a20; color:#90ee90; border-color:#2d6a2d; }
            QPushButton:hover    { background:#3a3a3a; color:#ccc; }
        """)
        top.addWidget(self.btn_equal_size)
        top.addStretch()

        btn_fill = QPushButton("📂  Alle Zellen füllen…")
        btn_fill.setStyleSheet(
            "background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
            "border-radius:4px; padding:6px 12px;")
        btn_fill.clicked.connect(self._fill_all)
        top.addWidget(btn_fill)

        main.addLayout(top)

        # ── Gitter-Bereich (scrollbar)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet("background:#111; border:1px solid #2a2a2a;")
        self._rebuild_grid()
        main.addWidget(self._scroll_area, 1)

        # ── Untere Leiste: Buttons
        btns = QHBoxLayout()
        btns.addStretch()

        btn_cancel = QPushButton("Abbrechen")
        btn_cancel.setStyleSheet(
            "background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
            "border-radius:4px; padding:8px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        self.btn_create = QPushButton("▶  Collage erstellen  (0/9 Zellen)")
        self.btn_create.setEnabled(False)
        self.btn_create.setStyleSheet("""
            QPushButton          { background:#0e639c; color:white; border:none;
                                   border-radius:4px; padding:8px 20px; font-weight:bold; }
            QPushButton:disabled { background:#2a2a2a; color:#555; }
            QPushButton:hover    { background:#1177bb; }
        """)
        self.btn_create.clicked.connect(self._create_collage)
        btns.addWidget(self.btn_create)

        main.addLayout(btns)

    def _on_grid_change(self, text: str):
        parts = text.split("×")
        self._grid_rows = int(parts[0])
        self._grid_cols = int(parts[1])
        self._cell_images.clear()
        self._cell_labels.clear()
        self._cell_combos.clear()
        self._swap_source = None
        self._rebuild_grid()
        self._update_btn()

    def _rebuild_grid(self):
        _COMBO_STYLE = """
            QComboBox { background:#252525; color:#ccc; border:1px solid #383838;
                        border-radius:3px; padding:2px 6px; font-size:10px; }
            QComboBox::drop-down { border:none; width:16px; }
            QComboBox QAbstractItemView { background:#252525; color:#ccc; font-size:10px;
                selection-background-color:#0e639c; }
        """
        container = QWidget()
        container.setStyleSheet("background:#111;")
        grid = QGridLayout(container)
        grid.setSpacing(6)
        grid.setContentsMargins(8, 8, 8, 8)

        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                # ── Zell-Container (Bild + Filter-Dropdown)
                cell = QWidget()
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px dashed #333; border-radius:4px;")
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(3, 3, 3, 3)
                cell_layout.setSpacing(3)

                # Bild-Label
                lbl = QLabel("📂\nKlick zum\nLaden")
                lbl.setFixedSize(162, 126)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("color:#555; font-size:11px; border:none;")
                lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                lbl.mousePressEvent = lambda ev, row=r, col=c: self._on_cell_click(row, col)
                cell_layout.addWidget(lbl)

                # Filter-Dropdown (Live-Vorschau bei Änderung)
                combo = QComboBox()
                combo.setStyleSheet(_COMBO_STYLE)
                combo.addItem("— Kein Filter —")
                for fname in COLLAGE_FILTER_FNS:
                    combo.addItem(fname)
                combo.currentIndexChanged.connect(
                    lambda idx, row=r, col=c: self._on_filter_change(row, col))
                cell_layout.addWidget(combo)

                grid.addWidget(cell, r, c)
                self._cell_labels[(r, c)] = lbl
                self._cell_combos[(r, c)] = combo

        self._scroll_area.setWidget(container)

    def _load_cell(self, row: int, col: int):
        path, _ = QFileDialog.getOpenFileName(
            self, "Bild wählen", "",
            "Bilder (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;Alle (*)")
        if not path:
            return
        try:
            img = PILImage.open(path).convert("RGBA")
            self._cell_images[(row, col)] = img
            self._refresh_thumb(row, col)
            self._update_btn()
        except Exception as e:
            QMessageBox.warning(self, "Fehler", str(e))

    def _refresh_thumb(self, row: int, col: int):
        """
        Zeigt das Thumbnail der Zelle — mit dem aktuell gewählten Filter als Live-Vorschau.
        Liest immer das Original aus _cell_images, wendet den Combo-Filter on-the-fly an.
        _cell_images wird dabei NICHT verändert (Original bleibt erhalten).
        """
        img = self._cell_images.get((row, col))
        if img is None:
            return

        combo    = self._cell_combos.get((row, col))
        disp_img = img
        if combo and combo.currentIndex() > 0:
            filter_fn = COLLAGE_FILTER_FNS.get(combo.currentText())
            if filter_fn:
                try:
                    disp_img = filter_fn(img.copy()).convert("RGBA")
                except Exception:
                    pass  # Fehler → Original anzeigen

        thumb = disp_img.copy()
        thumb.thumbnail((158, 122))
        pix = pil_to_qpixmap(thumb)
        lbl = self._cell_labels[(row, col)]
        lbl.setPixmap(pix.scaled(
            158, 122,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        lbl.setStyleSheet("border:none;")

        cell = lbl.parent()
        if cell:
            if self._swap_source == (row, col):
                # Orange = ausgewählt als Tausch-Quelle
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px solid #f0a030; border-radius:4px;")
            else:
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px solid #4fc3f7; border-radius:4px;")

    def _fill_all(self):
        """Mehrere Bilder auf einmal laden und der Reihe nach in die Zellen einfügen."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Bilder wählen", "",
            "Bilder (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;Alle (*)")
        if not paths:
            return
        cells = [(r, c)
                 for r in range(self._grid_rows)
                 for c in range(self._grid_cols)]
        for i, (r, c) in enumerate(cells):
            if i >= len(paths):
                break
            try:
                img = PILImage.open(paths[i]).convert("RGBA")
                self._cell_images[(r, c)] = img
                self._refresh_thumb(r, c)
            except Exception:
                pass
        self._update_btn()

    # ── Live-Filter-Vorschau ────────────────────────────────────
    def _on_filter_change(self, row: int, col: int):
        """Wird aufgerufen wenn der Filter-Dropdown einer Zelle geändert wird."""
        self._refresh_thumb(row, col)

    # ── Zell-Klick: Laden oder Tauschen ─────────────────────────
    def _on_cell_click(self, row: int, col: int):
        """
        Klick auf eine Zelle:
        • Leere Zelle  → Bild laden (Swap-Modus wird ggf. abgebrochen)
        • Gefüllte Zelle, kein Swap aktiv → als Tausch-Quelle markieren (orange)
        • Gefüllte Zelle, gleiche Zelle   → Auswahl aufheben
        • Gefüllte Zelle, andere Quelle   → Tausch durchführen
        """
        if (row, col) in self._cell_images:
            if self._swap_source is None:
                # Zelle als Tausch-Quelle auswählen
                self._swap_source = (row, col)
                self._refresh_thumb(row, col)   # → orange Rahmen
                self.btn_create.setText(
                    "🔄  Tausch-Modus: Ziel-Zelle anklicken  |  ESC = Abbrechen")
            elif self._swap_source == (row, col):
                # Gleiche Zelle → Auswahl aufheben
                self._swap_source = None
                self._refresh_thumb(row, col)   # → blau zurück
                self._update_btn()
            else:
                # Andere Zelle → Tausch ausführen
                # _swap_source VOR dem Tausch zurücksetzen,
                # damit _refresh_thumb() beide Zellen blau zeichnet.
                src_saved = self._swap_source
                self._swap_source = None
                self._swap_cells(src_saved, (row, col))
                self._update_btn()
        else:
            # Leere Zelle: evtl. laufenden Swap abbrechen, dann laden
            if self._swap_source is not None:
                prev = self._swap_source
                self._swap_source = None
                self._refresh_thumb(*prev)
                self._update_btn()
            self._load_cell(row, col)

    def _swap_cells(self, src: tuple, dst: tuple):
        """
        Tauscht Bild und Filter-Auswahl zweier Zellen.
        Das Original-Bild bleibt in _cell_images, nur der Eintrag wechselt.
        """
        img_s = self._cell_images.get(src)
        img_d = self._cell_images.get(dst)

        # Bilder in _cell_images tauschen
        if img_d is not None:
            self._cell_images[src] = img_d
        elif src in self._cell_images:
            del self._cell_images[src]
        if img_s is not None:
            self._cell_images[dst] = img_s
        elif dst in self._cell_images:
            del self._cell_images[dst]

        # Filter-Dropdown-Auswahl tauschen
        cb_s = self._cell_combos.get(src)
        cb_d = self._cell_combos.get(dst)
        if cb_s and cb_d:
            idx_s, idx_d = cb_s.currentIndex(), cb_d.currentIndex()
            cb_s.blockSignals(True);  cb_d.blockSignals(True)
            cb_s.setCurrentIndex(idx_d);  cb_d.setCurrentIndex(idx_s)
            cb_s.blockSignals(False); cb_d.blockSignals(False)

        # Beide Thumbnails aktualisieren
        for key in (src, dst):
            if key in self._cell_images:
                self._refresh_thumb(*key)
            else:
                self._clear_cell(*key)

    def _clear_cell(self, row: int, col: int):
        """Setzt eine Zelle auf den leeren Ausgangszustand zurück."""
        self._cell_images.pop((row, col), None)
        lbl = self._cell_labels.get((row, col))
        if lbl:
            lbl.clear()
            lbl.setText("📂\nKlick zum\nLaden")
            lbl.setStyleSheet("color:#555; font-size:11px; border:none;")
            cell = lbl.parent()
            if cell:
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px dashed #333; border-radius:4px;")

    def keyPressEvent(self, event):
        """ESC bricht den Tausch-Modus ab (ohne den Dialog zu schließen)."""
        if event.key() == Qt.Key.Key_Escape and self._swap_source is not None:
            prev = self._swap_source
            self._swap_source = None
            self._refresh_thumb(*prev)
            self._update_btn()
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_btn(self):
        filled = len(self._cell_images)
        total  = self._grid_rows * self._grid_cols
        self.btn_create.setEnabled(filled > 0)
        self.btn_create.setText(
            f"▶  Collage erstellen  ({filled}/{total} Zellen)")

    def _create_collage(self):
        """Alle Zellen auf PIL-Canvas zusammensetzen."""
        cell_w = self.spin_size.value()
        cell_h = self.spin_size.value()
        gap    = 4
        total_w = self._grid_cols * cell_w + (self._grid_cols - 1) * gap
        total_h = self._grid_rows * cell_h + (self._grid_rows - 1) * gap

        result = PILImage.new("RGBA", (total_w, total_h), (30, 30, 30, 255))

        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                x = c * (cell_w + gap)
                y = r * (cell_h + gap)
                img = self._cell_images.get((r, c))
                if img is None:
                    continue

                # Filter auf Zelle anwenden falls ausgewählt
                combo = self._cell_combos.get((r, c))
                if combo and combo.currentIndex() > 0:
                    filter_fn = COLLAGE_FILTER_FNS.get(combo.currentText())
                    if filter_fn:
                        try:
                            img = filter_fn(img.copy()).convert("RGBA")
                        except Exception:
                            pass  # Original behalten bei Fehler

                cell_img = img.copy().convert("RGBA")
                if self.btn_equal_size.isChecked():
                    # Crop-Fill: Bild exakt auf Zellgröße zuschneiden (kein Letterboxing)
                    cell_img = ImageOps.fit(
                        cell_img, (cell_w, cell_h), PILImage.Resampling.LANCZOS)
                    result.paste(cell_img, (x, y), cell_img)
                else:
                    # Proportional skalieren + zentrieren
                    cell_img.thumbnail((cell_w, cell_h), PILImage.Resampling.LANCZOS)
                    ox = (cell_w - cell_img.width)  // 2
                    oy = (cell_h - cell_img.height) // 2
                    result.paste(cell_img, (x + ox, y + oy), cell_img)

        self.result_image = result
        self.accept()


# ══════════════════════════════════════════════════════════════
#  BILDFLÄCHE mit Zoom und Overlay-Unterstützung
# ══════════════════════════════════════════════════════════════

class ImageCanvas(QLabel):
    """
    Bildfläche — zentrales Anzeige-Widget des Editors.

    WARUM QLabel statt QWidget?
    QLabel hat eine eingebaute setPixmap()-Methode, die ein QPixmap
    effizient darstellt. Zoom wird durch Skalieren des QPixmap erreicht.

    ZOOM-IMPLEMENTIERUNG:
    Zoom-Faktor (self._zoom) skaliert das angezeigte QPixmap.
    Das PIL-Image bleibt immer in Originalgröße erhalten.
    Beispiel: zoom=2.0 → Bild doppelt so groß angezeigt,
              PIL-Image selbst unverändert.

    OVERLAY-HOST:
    Canvas ist Eltern-Widget aller Overlays (CropOverlay, DrawOverlay,
    ShapePlacerOverlay). Overlays liegen flächendeckend darüber.
    Immer nur ein Overlay gleichzeitig aktiv (self._overlay).
    """

    def __init__(self):
        super().__init__()
        self._pix_orig   = None    # QPixmap des aktuellen Bildes
        self._zoom       = 1.0
        self._overlay: "QWidget | None" = None    # Aktives Overlay (Crop/Draw/Shape/MagicWand)

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
                           size: int, texture=None) -> DrawOverlay:
        """
        Neues Zeichen-Overlay starten.
        Gibt das Overlay zurück damit der Editor Signale verbinden kann.
        """
        if self._overlay:
            self._overlay.close()
        self._overlay = DrawOverlay(self, tool, color, size, self._zoom, texture=texture)
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
    Hauptfenster des SBS Bildeditors.

    VERANTWORTLICHKEITEN:
    • Verwaltet den Anwendungszustand (aktuelles Bild, Undo-Stack, Werkzeuge)
    • Erstellt Menüleiste, Toolbar, Canvas, Dock-Panel, Statusleiste
    • Verbindet alle Signale mit ihren Slots (Signal/Slot-Prinzip)
    • Delegiert Bildoperationen an PIL (Pillow-Bibliothek)
    • Delegiert KI-Analyse an AIWorker (separater Thread)

    ZUSTANDSVARIABLEN:
      self.original_pil  – Unverändertes Original (für Reset & Slider-Basis)
      self.current_pil   – Aktuell bearbeitetes Bild (wird gespeichert)
      self.history       – Undo-Stack, max. 20 PIL-Image-Kopien
      self.draw_tool     – Aktives Zeichen-Werkzeug ('pen', 'brush', ...)
      self.draw_color    – Aktuelle Zeichenfarbe (QColor)
      self.draw_size     – Pinselgröße in Pixeln

    UNDO-MECHANISMUS:
    Vor jeder destruktiven Operation: self._push() → kopiert current_pil
    in history. self.undo() → stellt letzte Kopie wieder her.
    """
    ZOOM_STEP = 0.15   # Zoom-Schrittgröße pro Klick (15%)

    def __init__(self):
        super().__init__()
        self.current_file       = None
        self.original_pil       = None   # Original der aktiven Ebene (für Slider-Reset)
        self.current_pil        = None   # Composite aller Ebenen (für Anzeige/Speichern)
        self.history            = []     # Undo-Stack (max. 20, speichert Layer-Zustand)
        self.ai_worker          = None

        # Ebenen-System
        self.layers             = []     # Liste von Layer-Objekten
        self.active_layer_idx   = 0      # Index der aktiven Ebene

        # Auswahl (Zauberstab)
        self.selection_mask     = None   # PIL "L"-Bild oder None
        self.wand_tolerance     = 30     # Toleranz 0–100

        # Textur-Pinsel
        self.draw_texture       = None   # PIL RGBA Textur oder None

        # Zeichen-Zustand
        self.draw_tool    = "pen"
        self.draw_color   = QColor(255, 0, 0)
        self.draw_size    = 4

        self._setup_window()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_central()
        self._setup_panel()
        self._setup_layers_dock()
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
        im.addSeparator()
        a(im, "🔮 Kaleidoskop",       "",             self.apply_kaleidoscope)
        a(im, "🖼  Filter-Vorschau…", "Ctrl+Shift+V", self.open_filter_preview)
        a(im, "🖼  Collage-Editor…",  "Ctrl+Shift+C", self.open_collage_dialog)
        a(im, "🎞  GIF-Editor…",      "Ctrl+Shift+G", self.open_gif_editor)
        a(im, "🧊  3D-Modell (Beta)…","Ctrl+Shift+3", self.open_3d_dialog)

        dm = mb.addMenu("Zeichnen")
        a(dm, "✏️ Stift",             "Ctrl+Shift+P", lambda: self.set_draw_tool("pen"))
        a(dm, "🖌 Pinsel",            "Ctrl+Shift+B", lambda: self.set_draw_tool("brush"))
        a(dm, "⬜ Radierer",          "Ctrl+Shift+E", lambda: self.set_draw_tool("eraser"))
        a(dm, "╱ Linie",             "",             lambda: self.set_draw_tool("line"))
        a(dm, "▭ Rechteck",          "",             lambda: self.set_draw_tool("rect"))
        a(dm, "◯ Ellipse",           "",             lambda: self.set_draw_tool("ellipse"))
        a(dm, "T Text einfügen",     "",             lambda: self.set_draw_tool("text"))
        a(dm, "💧 Weichzeich.-Pinsel","",            lambda: self.set_draw_tool("blur"))
        a(dm, "〜 Kurve zeichnen",  "",             lambda: self.set_draw_tool("curve"))
        a(dm, "🖼 Textur-Pinsel",   "",             lambda: self.set_draw_tool("texture_brush"))
        dm.addSeparator()
        a(dm, "🎨 Farbe wählen…",    "",             self.pick_color)
        a(dm, "🖼 Textur laden…",    "",             self.pick_texture)
        dm.addSeparator()
        a(dm, "🪄 Zauberstab",       "Ctrl+Shift+W", self.start_magic_wand)
        a(dm, "✕ Auswahl aufheben", "",             self.clear_selection)
        a(dm, "✂ Auswahl → Ebene",  "",             self.cut_selection_to_layer)

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
        add("💧 Unschärfe", "Weichzeich.-Pinsel", "",             lambda: self.set_draw_tool("blur"))
        add("〜 Kurve",    "Kurve zeichnen",     "",             lambda: self.set_draw_tool("curve"))
        add("🎨 Farbe",     "Farbe wählen",       "",             self.pick_color)
        add("🖼 Textur",    "Textur laden",       "",             self.pick_texture)
        add("🪄 Wand",      "Zauberstab",         "Ctrl+Shift+W", self.start_magic_wand)
        tb.addSeparator()
        add("🤖 KI",        "KI-Analyse",         "Ctrl+A",       self.run_ai_analysis)
        tb.addSeparator()
        add("✨ Form",       "Form platzieren",    "Ctrl+Shift+F", self.start_shape_placer)
        tb.addSeparator()
        add("🔮 Kaleidoskop","Kaleidoskop-Filter", "",             self.apply_kaleidoscope)
        add("🖼 Vorschau",  "Filter-Vorschau",    "Ctrl+Shift+V", self.open_filter_preview)
        add("🖼 Collage",   "Collage-Editor",     "Ctrl+Shift+C", self.open_collage_dialog)
        add("🎞 GIF",       "GIF-Editor",         "Ctrl+Shift+G", self.open_gif_editor)
        add("🧊 3D",        "3D-Modell (Beta)",   "Ctrl+Shift+3", self.open_3d_dialog)

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
            ("✏️ Stift",    "pen"),          ("🖌 Pinsel",       "brush"),
            ("💧 Unschärfe","blur"),         ("⬜ Radierer",     "eraser"),
            ("╱ Linie",    "line"),         ("▭ Rechteck",     "rect"),
            ("◯ Ellipse",  "ellipse"),      ("T Text",         "text"),
            ("〜 Kurve",   "curve"),        ("🖼 Textur-Pinsel","texture_brush"),
            ("🎨 Farbe…",  "__color__"),    ("",               "__noop__"),
        ]
        # Stil für aktiven/inaktiven Button
        self._draw_btns = {}
        grid_rows = [draw_tools[i:i+2] for i in range(0, len(draw_tools), 2)]
        for row_items in grid_rows:
            row = QHBoxLayout()
            for label, key in row_items:
                if key == "__noop__":
                    row.addStretch()
                    continue
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

        # Textur laden Button
        _btn_style2 = ("background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                       "border-radius:4px; padding:6px; font-size:11px;")
        btn_tex = QPushButton("🖼  Textur laden…")
        btn_tex.setStyleSheet(_btn_style2)
        btn_tex.setToolTip("Bild als Textur-Stempel laden (aktiviert Textur-Pinsel-Werkzeug)")
        btn_tex.clicked.connect(self.pick_texture)
        dl.addWidget(btn_tex)

        layout.addWidget(grp_draw)

        # ── Zauberstab-Werkzeug
        grp_wand = self._grp("🪄  ZAUBERSTAB")
        wl = QVBoxLayout(grp_wand); wl.setSpacing(5)

        wand_info = QLabel("Klick = Bereich auswählen\nShift+Klick = hinzufügen\nEnter = bestätigen")
        wand_info.setStyleSheet("color:#888; font-size:10px; background:#141414; "
                                "border-radius:3px; padding:5px;")
        wl.addWidget(wand_info)

        # Toleranz-Slider
        self.sl_wand_tol = LabeledSlider("Toleranz", 0, 100, self.wand_tolerance)
        def _on_wand_tol(v):
            self.wand_tolerance = v
        self.sl_wand_tol.value_changed.connect(_on_wand_tol)
        wl.addWidget(self.sl_wand_tol)

        btn_wand = QPushButton("🪄  Zauberstab aktivieren")
        btn_wand.setStyleSheet("""
            QPushButton { background:#2a1a3a; color:#c9b8f7; border:1px solid #4a2a6a;
                border-radius:4px; padding:7px; font-size:12px; }
            QPushButton:hover { background:#3a2a5a; border-color:#9b7fd4; }
        """)
        btn_wand.clicked.connect(self.start_magic_wand)
        wl.addWidget(btn_wand)

        # Auswahl-Aktionen
        sel_row = QHBoxLayout()
        btn_clr_sel = QPushButton("✕ Aufheben")
        btn_clr_sel.setStyleSheet(_btn_style2)
        btn_clr_sel.setToolTip("Aktive Auswahl aufheben")
        btn_clr_sel.clicked.connect(self.clear_selection)
        btn_cut_sel = QPushButton("✂ → Ebene")
        btn_cut_sel.setStyleSheet(_btn_style2)
        btn_cut_sel.setToolTip("Ausgewählten Bereich auf neue Ebene ausschneiden")
        btn_cut_sel.clicked.connect(self.cut_selection_to_layer)
        sel_row.addWidget(btn_clr_sel)
        sel_row.addWidget(btn_cut_sel)
        wl.addLayout(sel_row)

        layout.addWidget(grp_wand)

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
            ("🔮  Kaleidoskop",              self.apply_kaleidoscope),
            ("📼  VHS-Flicker (80er)",       self.apply_vhs_flicker),
            ("── 3D-Effekte ──────────────", None),
            ("🔴🔵  Anaglyphen-3D (Rot/Cyan)", self.apply_anaglyph_3d),
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

        btn_preview = QPushButton("🖼  Filter-Vorschau-Grid…  (Strg+Shift+V)")
        btn_preview.setStyleSheet("""
            QPushButton { background:#1a2a3a; color:#7ec8f7; border:1px solid #2a4a6a;
                border-radius:4px; padding:7px; font-size:11px; }
            QPushButton:hover { background:#1e3a50; border-color:#4fc3f7; color:#fff; }
        """)
        btn_preview.clicked.connect(self.open_filter_preview)
        fl.addWidget(btn_preview)
        layout.addWidget(grp_f)

        # ── Histogramm
        grp_hist = self._grp("📊  HISTOGRAMM  (R / G / B)")
        hl = QVBoxLayout(grp_hist); hl.setContentsMargins(6, 4, 6, 6)
        self.histogram_widget = HistogramWidget()
        hl.addWidget(self.histogram_widget)
        hint_hist = QLabel("Live-Anzeige der Farbverteilung.\n"
                           "Links = dunkel  |  Rechts = hell")
        hint_hist.setStyleSheet("color:#555; font-size:9px;")
        hl.addWidget(hint_hist)
        layout.addWidget(grp_hist)

        # ── Collage-Editor
        grp_col = self._grp("🖼  COLLAGE-EDITOR")
        col_l = QVBoxLayout(grp_col); col_l.setSpacing(5)
        info_col = QLabel("Mehrere Bilder zu einer\nCollage zusammenfügen.")
        info_col.setStyleSheet("color:#777; font-size:10px;")
        col_l.addWidget(info_col)
        btn_collage = QPushButton("🖼  Collage erstellen…  (Strg+Shift+C)")
        btn_collage.setStyleSheet("""
            QPushButton { background:#1a3050; color:#7ec8f7; border:1px solid #2a5080;
                border-radius:4px; padding:8px; font-size:11px; font-weight:bold; }
            QPushButton:hover { background:#1e4070; border-color:#4fc3f7; color:#fff; }
        """)
        btn_collage.clicked.connect(self.open_collage_dialog)
        col_l.addWidget(btn_collage)
        layout.addWidget(grp_col)

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
        grp_ai = self._grp("🤖  KI-ANALYSE  (Moondream – Beta)")
        al = QVBoxLayout(grp_ai); al.setSpacing(6)

        # Großes Textfeld für die Antwort
        self.ai_text = QTextEdit()
        self.ai_text.setReadOnly(True)
        self.ai_text.setMinimumHeight(150)
        self.ai_text.setMaximumHeight(260)
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
            "SBS Bildeditor  –  Öffne ein Bild  |  ✂ Crop  |  🎨 Filter  |  📐 Ebenen  |  🤖 KI")

    # ── Ebenen-Dock (links) ──────────────────────
    def _setup_layers_dock(self):
        """
        Zweites Dock-Widget auf der linken Seite: zeigt das Ebenen-Panel.
        Trennung vom rechten Einstellungs-Panel hält die UI übersichtlich.
        """
        dock = QDockWidget("📐  Ebenen", self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setStyleSheet("""
            QDockWidget { color:#ddd; }
            QDockWidget::title { background:#1a1a1a; padding:8px 12px;
                font-weight:bold; font-size:12px; letter-spacing:1px; }
        """)
        self.layer_panel = LayerPanel(self)
        dock.setWidget(self.layer_panel)
        dock.setMinimumWidth(280)
        dock.setMaximumWidth(320)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    # ── Composite & Display ──────────────────────
    def _composite_layers(self) -> "PILImage.Image":
        """
        Fasst alle sichtbaren Ebenen zu einem einzigen RGBA-Bild zusammen.

        Reihenfolge: layers[0] = Hintergrund (zuerst gemalt),
                     layers[-1] = oberste Ebene (zuletzt gemalt).
        Opacity wird auf den Alpha-Kanal jeder Ebene angewendet
        bevor sie auf das Ergebnis-Canvas gepastet wird.
        """
        if not self.layers:
            return PILImage.new("RGBA", (800, 600), (40, 40, 40, 255))
        visible = [l for l in self.layers if l.visible and l.image]
        if not visible:
            return PILImage.new("RGBA", (800, 600), (40, 40, 40, 255))
        w = max(l.x + l.image.width  for l in visible if l.image)
        h = max(l.y + l.image.height for l in visible if l.image)
        result = PILImage.new("RGBA", (w, h), (40, 40, 40, 255))
        for layer in self.layers:
            if not layer.visible or not layer.image:
                continue
            img = layer.image.copy()
            if layer.opacity < 100:
                r, g, b, a = img.split()
                a = a.point([int(v * layer.opacity / 100) for v in range(256)])
                img = PILImage.merge("RGBA", (r, g, b, a))
            result.paste(img, (layer.x, layer.y), img)
        return result

    def _update_display(self):
        """
        Recompiliert das Composite aller Ebenen und aktualisiert:
          • Canvas-Anzeige
          • Histogramm
          • Ebenen-Panel Thumbnails
        Wird nach jeder Ebenen-Änderung aufgerufen.
        """
        self.current_pil = self._composite_layers()
        self.canvas.update_image(pil_to_qpixmap(self.current_pil))
        if hasattr(self, "histogram_widget"):
            self.histogram_widget.update_histogram(self.current_pil)
        if hasattr(self, "layer_panel"):
            self.layer_panel.refresh()

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
            Layer._counter = 0
            self.layers           = [Layer(pil.copy(), "Hintergrund")]
            self.active_layer_idx = 0
            self.original_pil     = pil.copy()
            self.current_file     = path
            self.history.clear()
            self.selection_mask   = None
            self._update_display()
            if self.layers[0].image:
                self.canvas.set_image(pil_to_qpixmap(self.layers[0].image))
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
        """
        Aktuellen Ebenen-Zustand in den Undo-Stack schieben.

        UNDO-MECHANISMUS:
        Jede destruktive Operation ruft zuerst _push() auf.
        Gespeichert wird ein Snapshot aller Layer (Bildkopie + Metadaten).
        Speicherverbrauch: ~4 Bytes × Breite × Höhe × Anzahl Ebenen pro Schritt.
        Maximum 20 Schritte.
        """
        if not self.layers:
            return
        snapshot = [
            {"image":   l.image.copy() if l.image else None,
             "name":    l.name,
             "opacity": l.opacity,
             "visible": l.visible,
             "x":       l.x,
             "y":       l.y}
            for l in self.layers
        ]
        self.history.append({
            "layers":       snapshot,
            "active":       self.active_layer_idx,
            "original_pil": self.original_pil.copy() if self.original_pil else None,
        })
        if len(self.history) > 20:
            self.history.pop(0)

    def undo(self):
        if not self.history:
            self.status_bar.showMessage("Nichts zum Rückgängigmachen.", 2000); return
        state = self.history.pop()
        self.layers = []
        for s in state["layers"]:
            l = Layer.__new__(Layer)
            l.image   = s["image"]
            l.name    = s["name"]
            l.opacity = s["opacity"]
            l.visible = s["visible"]
            l.x       = s["x"]
            l.y       = s["y"]
            self.layers.append(l)
        self.active_layer_idx = state["active"]
        self.original_pil     = state["original_pil"]
        self._reset_sliders()
        self._update_display()
        self._update_status()

    def reset_to_original(self):
        """Aktive Ebene auf ihren letzten bestätigten Zustand (vor Schieberegler-Änderungen) zurücksetzen."""
        if not self.original_pil or not self.layers:
            return
        self._push()
        self.layers[self.active_layer_idx].image = self.original_pil.copy()
        self._reset_sliders()
        self._update_display()
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
        overlay.rect_selected.connect(self._on_rect_selected)
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
        overlay.lasso_selected.connect(self._on_lasso_selected)
        overlay.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))

    def _on_rect_selected(self, rect: QRect):
        """Nach dem Zeichnen des Rechtecks: MovableRectOverlay für Anpassung zeigen."""
        mov = MovableRectOverlay(self.canvas, rect)
        mov.confirmed.connect(self._do_rect_crop)
        mov.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))
        mov.setGeometry(self.canvas.rect())
        self.canvas._overlay = mov
        self.status_bar.showMessage(
            "Auswahl anpassen: Ziehen=Verschieben  |  Ecken=Skalieren  |  Enter=Zuschneiden  |  ESC=Abbruch")

    def _on_lasso_selected(self, points: list):
        """Nach dem Zeichnen des Lassos: MovableLassoOverlay für Anpassung zeigen."""
        mov = MovableLassoOverlay(self.canvas, points)
        mov.confirmed.connect(self._do_lasso_crop)
        mov.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))
        mov.setGeometry(self.canvas.rect())
        self.canvas._overlay = mov
        self.status_bar.showMessage(
            "Auswahl verschieben: Ziehen=Verschieben  |  Enter=Zuschneiden  |  ESC=Abbruch")

    def _do_rect_crop(self, rect: QRect):
        """
        Rechteck-Koordinaten vom Overlay in Bildkoordinaten umrechnen
        und PIL-Image zuschneiden.

        KOORDINATEN-UMRECHNUNG (Zoom-Korrektur):
        Das Overlay arbeitet in Bildschirmpixeln (beeinflusst durch Zoom).
        PIL.Image.crop() braucht echte Bildpixel.
        Formel: Bildpixel = Bildschirmpixel / zoom_faktor
        Beispiel: zoom=2.0, Klick bei x=400 → Bildpixel x=200
        """
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return

        # Zoom-Faktor berücksichtigen (Overlay-Koordinaten → Bildkoordinaten)
        z = self.canvas.get_zoom()
        x1 = int(rect.left()   / z)
        y1 = int(rect.top()    / z)
        x2 = int(rect.right()  / z)
        y2 = int(rect.bottom() / z)

        # Auf Bildgrenzen begrenzen
        w, h = layer.image.width, layer.image.height
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 > x1 and y2 > y1:
            self._push()
            rgba = layer.image.convert("RGBA")
            # ── Ausschnitt auf neue Ebene ──────────────────────
            cut_img = rgba.crop((x1, y1, x2, y2))
            new_layer   = Layer(cut_img, "Ausschnitt")
            new_layer.x = layer.x + x1
            new_layer.y = layer.y + y1
            # ── Loch ins Original stanzen ──────────────────────
            orig_mod = rgba.copy()
            hole = PILImage.new("RGBA", (x2 - x1, y2 - y1), (0, 0, 0, 0))
            orig_mod.paste(hole, (x1, y1))
            layer.image = orig_mod
            # ── Layer-Stack aktualisieren ──────────────────────
            self.layers.append(new_layer)
            self.active_layer_idx = len(self.layers) - 1
            self.original_pil     = cut_img.copy()
            self._update_display()
            self._update_status()
            self.status_bar.showMessage(
                f"✂ Ausschnitt ({x2-x1}×{y2-y1} px) auf neue Ebene  |  "
                "Original hat transparentes Loch", 4000)

    def _do_lasso_crop(self, points: list):
        """
        Lasso-Zuschnitt: Freihand-Polygon als Maske anwenden.

        MASKEN-KONZEPT (wichtig für die Prüfung!):
        1. Schwarze Maske (L-Mode) in Originalgröße erstellen
        2. Polygon mit Weiß füllen → weiß = sichtbar, schwarz = transparent
        3. PIL.Image.paste() mit Maske: kopiert nur die weißen Bereiche
        4. crop() auf Bounding Box des Polygons → fertiges Ergebnis

        Ergebnis ist ein RGBA-Bild mit transparentem Hintergrund.
        Kann als PNG mit Transparenz gespeichert werden.
        """
        if not self.layers or len(points) < 3: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return

        z = self.canvas.get_zoom()
        w, h = layer.image.width, layer.image.height

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
        rgba = layer.image.copy().convert("RGBA")
        result = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        result.paste(rgba, mask=mask)

        # ── Ausschnitt (Polygon) auf neue Ebene ───────────────
        cut_img = result.crop((x1, y1, x2, y2))
        new_layer   = Layer(cut_img, "Lasso-Ausschnitt")
        new_layer.x = layer.x + x1
        new_layer.y = layer.y + y1
        # ── Loch ins Original stanzen (inverse Maske) ─────────
        inv_mask  = mask.point([255 - v for v in range(256)])
        orig_mod  = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        orig_mod.paste(rgba, mask=inv_mask)
        layer.image = orig_mod
        # ── Layer-Stack aktualisieren ──────────────────────────
        self.layers.append(new_layer)
        self.active_layer_idx = len(self.layers) - 1
        self.original_pil     = cut_img.copy()
        self._update_display()
        self._update_status()
        self.status_bar.showMessage(
            f"🔮 Lasso-Ausschnitt ({x2-x1}×{y2-y1} px) auf neue Ebene  |  "
            "Original hat transparentes Loch", 4000)

    # ══════════════════════════════════════════════
    #  GRUNDKORREKTUREN (Schieberegler)
    # ══════════════════════════════════════════════

    def _apply_adjustments(self):
        """
        Wendet Helligkeit/Kontrast/Sättigung/Schärfe live auf das Bild an.

        WICHTIG – immer vom Original starten:
        Würde man von current_pil starten, würden sich Werte aufaddieren.
        Beispiel: Slider 2× auf 150 gesetzt → Helligkeit wäre 150% × 150% = 225%.
        Lösung: Immer self.original_pil als Basis nehmen, dann alle
        Schieberegler in einem Durchlauf anwenden.

        ImageEnhance-Werte: 1.0 = keine Änderung, <1 = weniger, >1 = mehr.
        Slider-Wert 100 → 100/100 = 1.0 (neutraler Wert)
        """
        if not self.original_pil or not self.layers: return
        img = self.original_pil.copy().convert("RGB")
        img = ImageEnhance.Brightness(img).enhance(self.sl_brightness.value() / 100)
        img = ImageEnhance.Contrast(img).enhance(self.sl_contrast.value()    / 100)
        img = ImageEnhance.Color(img).enhance(self.sl_saturation.value()  / 100)
        img = ImageEnhance.Sharpness(img).enhance(self.sl_sharpness.value()  / 100)
        self.layers[self.active_layer_idx].image = img.convert("RGBA")
        self._update_display()

    # ══════════════════════════════════════════════
    #  TRANSFORMATIONEN
    # ══════════════════════════════════════════════

    def rotate_cw(self):   self._transform(lambda i: i.rotate(-90, expand=True))
    def rotate_ccw(self):  self._transform(lambda i: i.rotate( 90, expand=True))
    def flip_horizontal(self): self._transform(lambda i: ImageOps.mirror(i))
    def flip_vertical(self):   self._transform(lambda i: ImageOps.flip(i))

    def _transform(self, fn):
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return
        self._push()
        layer.image       = fn(layer.image.convert("RGBA")).convert("RGBA")
        self.original_pil = layer.image.copy()
        self._reset_sliders()
        self._update_display()
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
        """Hilfsmethode: Filter auf aktive Ebene anwenden mit Undo-Schritt."""
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return
        self._push()
        layer.image       = fn(layer.image).convert("RGBA")
        self.original_pil = layer.image.copy()
        self._reset_sliders()
        self._update_display()
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

    def apply_kaleidoscope(self):
        """
        Kaleidoskop-Filter: Bild in 4 gespiegelte Segmente teilen.

        ALGORITHMUS:
        1. Linke Bildhälfte nehmen
        2. Horizontal spiegeln → rechte Hälfte (Symmetrie links↔rechts)
        3. Obere Hälfte des Ergebnisses nehmen
        4. Vertikal spiegeln → untere Hälfte (Symmetrie oben↔unten)
        Ergebnis: 4-fach symmetrisches Kaleidoskop-Muster.
        """
        def kaleidoscope(img):
            img = img.convert("RGBA")
            w, h = img.size
            half_w = w // 2
            # Schritt 1+2: Linke Hälfte + gespiegelte rechte Hälfte
            left   = img.crop((0, 0, half_w, h))
            right  = ImageOps.mirror(left)
            top    = PILImage.new("RGBA", (w, h))
            top.paste(left,  (0,      0))
            top.paste(right, (half_w, 0))
            # Schritt 3+4: Obere Hälfte + gespiegelte untere Hälfte
            half_h   = h // 2
            top_half = top.crop((0, 0, w, half_h))
            bottom   = ImageOps.flip(top_half)
            result   = PILImage.new("RGBA", (w, h))
            result.paste(top_half, (0, 0))
            result.paste(bottom,   (0, half_h))
            return result
        self._filt(kaleidoscope)

    def apply_vhs_flicker(self):
        """
        VHS-Flicker-Filter: Simuliert einen flimmernden Bildschirm aus den 80ern.

        ALGORITHMUS:
        1. Horizontale Scan-Linien: jede 2. Zeile leicht abdunkeln (CRT-Effekt)
        2. Zufällige Störstreifen: schmale horizontale Bänder werden
           horizontal verschoben (Tracking-Fehler) und aufgehellt/abgedunkelt
        3. Leichtes Chroma-Shift: Rot-Kanal minimal nach links, Blau nach rechts
        4. Leichtes Rauschen über das gesamte Bild
        Ergebnis: Nur ein Teil der Streifen ist sichtbar — nicht das ganze Bild.
        """
        import random
        def vhs(img):
            img  = img.convert("RGB")
            w, h = img.size
            pixels = list(img.getdata())

            rng = random.Random(42)   # Reproduzierbares Ergebnis für Vorschau

            # ── 1. Scan-Linien: jede 2. Zeile 15% abdunkeln ──────────
            for y in range(0, h, 2):
                for x in range(w):
                    idx = y * w + x
                    r, g, b = pixels[idx]
                    pixels[idx] = (int(r * 0.85), int(g * 0.85), int(b * 0.85))

            img.putdata(pixels)

            # ── 2. Störstreifen (Tracking-Fehler) ────────────────────
            # Ca. 8–14 zufällige Streifen, jeder 2–12 px hoch
            n_stripes = rng.randint(8, 14)
            for _ in range(n_stripes):
                sy     = rng.randint(0, h - 1)
                sh     = rng.randint(2, 12)
                shift  = rng.randint(-18, 18)          # horizontale Verschiebung
                bright = rng.uniform(0.6, 1.5)         # Aufhellen oder Abdunkeln
                band   = img.crop((0, sy, w, min(h, sy + sh)))
                # Helligkeit anpassen
                band = ImageEnhance.Brightness(band).enhance(bright)
                # Horizontal verschieben (wrap-around)
                if shift != 0:
                    band = PILImage.new("RGB", (w, band.height))
                    orig = img.crop((0, sy, w, min(h, sy + sh)))
                    orig = ImageEnhance.Brightness(orig).enhance(bright)
                    if shift > 0:
                        band.paste(orig.crop((0, 0, w - shift, orig.height)), (shift, 0))
                        band.paste(orig.crop((w - shift, 0, w, orig.height)), (0, 0))
                    else:
                        s = -shift
                        band.paste(orig.crop((s, 0, w, orig.height)), (0, 0))
                        band.paste(orig.crop((0, 0, s, orig.height)), (w - s, 0))
                img.paste(band, (0, sy))

            # ── 3. Chroma-Shift: R leicht nach links, B nach rechts ──
            r_ch, g_ch, b_ch = img.split()
            shift_px = max(1, w // 120)
            r_shifted = PILImage.new("L", (w, h), 0)
            b_shifted = PILImage.new("L", (w, h), 0)
            r_shifted.paste(r_ch.crop((shift_px, 0, w, h)), (0, 0))
            b_shifted.paste(b_ch.crop((0, 0, w - shift_px, h)), (shift_px, 0))
            img = PILImage.merge("RGB", (r_shifted, g_ch, b_shifted))

            # ── 4. Leichtes Rauschen ──────────────────────────────────
            import array as arr
            noise_data = arr.array('B', [
                min(255, max(0, v + rng.randint(-12, 12)))
                for v in img.tobytes()
            ])
            img = PILImage.frombytes("RGB", (w, h), bytes(noise_data))

            return img

        self._filt(vhs)

    def apply_anaglyph_3d(self):
        """
        Anaglyphen-3D-Filter: Simuliert den Rot/Cyan-Brillen-Effekt.

        ALGORITHMUS:
        1. Linkes Auge  = Original (Rot-Kanal)
        2. Rechtes Auge = horizontal verschobene Kopie (Cyan = G+B-Kanal)
        3. Shift ≈ Bildbreite / 40  → realistischer Stereo-Abstand
        4. Kombination: R aus links, G+B aus rechts → Anaglyphen-Bild

        Für optimalen Effekt eine Rot/Cyan-3D-Brille aufsetzen.
        """
        def anaglyph(img):
            img   = img.convert("RGB")
            w, h  = img.size
            shift = max(2, w // 40)

            # Linkes Auge: nur R-Kanal
            r_ch, _, _ = img.split()

            # Rechtes Auge: um `shift` Pixel nach rechts verschoben
            shifted = PILImage.new("RGB", (w, h), (0, 0, 0))
            shifted.paste(img.crop((0, 0, w - shift, h)), (shift, 0))
            _, g_ch, b_ch = shifted.split()

            # Zusammensetzen: R von links, G+B von rechts
            return PILImage.merge("RGB", (r_ch, g_ch, b_ch))

        self._filt(anaglyph)

    # ══════════════════════════════════════════════
    #  FILTER-VORSCHAU
    # ══════════════════════════════════════════════

    def _generate_filter_previews(self, thumb_size: int = 100) -> dict:
        """
        Erzeugt Thumbnail-Vorschauen für alle Filter.

        TECHNIK — Temporäres Monkey-Patching:
        _filt() hat Seiteneffekte (canvas.update_image, _reset_sliders, _update_status).
        Diese werden für die Vorschau-Generierung auf No-Ops gesetzt und danach
        vollständig wiederhergestellt. So bleibt der Editor-Zustand unverändert.
        """
        if not self.current_pil:
            return {}

        thumb = self.current_pil.copy().convert("RGBA")
        thumb.thumbnail((thumb_size, thumb_size))

        saved_current  = self.current_pil
        saved_original = self.original_pil
        saved_history  = self.history[:]
        saved_layers   = self.layers
        saved_active   = self.active_layer_idx

        orig_update     = self.canvas.update_image
        orig_reset      = self._reset_sliders
        orig_status     = self._update_status
        orig_update_disp = self._update_display
        self.canvas.update_image = lambda pix: None
        self._reset_sliders      = lambda: None
        self._update_status      = lambda: None
        self._update_display     = lambda: None  # type: ignore[method-assign]

        previews = {}
        for name, fn in self._filter_map.items():
            tmp = thumb.copy()
            self.layers           = [Layer(tmp, "preview")]
            self.active_layer_idx = 0
            self.original_pil     = tmp.copy()
            self.history          = []
            try:
                fn()
                result = self.layers[0].image
                previews[name] = pil_to_qpixmap(result) if result else pil_to_qpixmap(thumb)
            except Exception:
                previews[name] = pil_to_qpixmap(thumb)

        self.canvas.update_image = orig_update
        self._reset_sliders      = orig_reset
        self._update_status      = orig_status
        self._update_display     = orig_update_disp  # type: ignore[method-assign]
        self.current_pil         = saved_current
        self.original_pil        = saved_original
        self.history             = saved_history
        self.layers              = saved_layers
        self.active_layer_idx    = saved_active
        return previews

    def open_filter_preview(self):
        """Filter-Vorschau-Dialog öffnen. Thumbnails werden live berechnet."""
        if not self.current_pil:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000)
            return
        self.status_bar.showMessage("⏳  Generiere Filter-Vorschauen…")
        QApplication.processEvents()
        previews = self._generate_filter_previews()
        dlg = FilterPreviewDialog(self, previews)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen_filter:
            fn = self._filter_map.get(dlg.chosen_filter)
            if fn:
                fn()
                self.status_bar.showMessage(
                    f"✅  Filter angewendet: {dlg.chosen_filter.strip()}", 3000)

    # ══════════════════════════════════════════════
    #  COLLAGE-EDITOR
    # ══════════════════════════════════════════════

    def open_gif_editor(self):
        """GIF-Editor öffnen."""
        if not self.layers:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        GifEditorDialog(self, self).exec()

    def open_3d_dialog(self):
        """2D → 3D Modell-Viewer öffnen."""
        if not self.layers:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        ThreeDModelDialog(self, self).exec()

    def open_collage_dialog(self):
        """Collage-Editor öffnen. Das Ergebnis ersetzt das aktuelle Bild."""
        dlg = CollageDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_image:
            self._push()
            result = dlg.result_image.convert("RGBA")
            Layer._counter = 0
            self.layers           = [Layer(result, "Collage")]
            self.active_layer_idx = 0
            self.original_pil     = result.copy()
            self.current_file     = None
            self._reset_sliders()
            self.btn_ai.setEnabled(True)
            self._update_display()
            self._update_status()
            img = self.layers[0].image
            size_str = f"{img.width}×{img.height} px" if img else ""
            self.status_bar.showMessage(
                f"✅  Collage erstellt  {size_str}  "
                "| Strg+Z zum Rückgängigmachen", 4000)

    # ══════════════════════════════════════════════
    #  ZEICHEN-WERKZEUGE
    # ══════════════════════════════════════════════

    def set_draw_tool(self, tool: str):
        """
        Aktives Zeichen-Werkzeug wechseln — sofort wirksam.

        BUG-FIX (wichtig für die Prüfung erklären!):
        Problem: Altes Overlay blieb aktiv nach Werkzeugwechsel.
        Ursache: Overlay speichert das Werkzeug bei Erstellung.
                 Wechsel des self.draw_tool änderte das laufende Overlay nicht.
        Lösung:  Altes Overlay mit blockSignals(True) schließen
                 (blockSignals verhindert Callback-Loop durch destroyed-Signal),
                 dann neues Overlay mit neuem Werkzeug sofort starten.
        """
        # Zauberstab ist kein Zeichen-Werkzeug → direkt starten
        if tool == "magic_wand":
            self.start_magic_wand()
            return

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
            self.draw_tool, self.draw_color, self.draw_size,
            texture=self.draw_texture
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
        Wendet eine Zeichenfunktion permanent auf die aktive Ebene an.
        Legt einen Undo-Schritt an. Startet danach das Overlay
        mit dem AKTUELL eingestellten Werkzeug neu (nicht dem alten).
        """
        if not self.layers:
            return
        layer = self.layers[self.active_layer_idx]
        if not layer.image:
            return
        self._push()
        result        = draw_fn(layer.image.copy())
        layer.image   = result.convert("RGBA")
        self.original_pil = layer.image.copy()
        self._update_display()
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
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return
        self._push()
        color_tuple = (self.draw_color.red(), self.draw_color.green(),
                       self.draw_color.blue(), 255)
        size      = self.sl_shape_size.value()
        lw        = max(2, size // 40)   # Strichbreite proportional zur Größe
        result = draw_shape_on_pil(
            layer.image.copy(),
            shape_key, ix, iy, size, color_tuple, lw
        )
        layer.image       = result.convert("RGBA")
        self.original_pil = layer.image.copy()
        self.canvas._overlay = None
        self._update_display()
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
        self.ai_text.setPlainText(f"🤖  KI-Beschreibung (Beta):\n\n{desc.strip()}")
        # Zum Anfang scrollen damit kein Text abgeschnitten wirkt
        self.ai_text.verticalScrollBar().setValue(0)
        self._reset_ai_btn()

    def _on_ai_error(self, err: str):
        self.ai_text.setPlainText(f"⚠  Fehler:\n\n{err}")
        self._reset_ai_btn()

    def _reset_ai_btn(self):
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("🤖  Analysieren  (Strg+A)")

    # ══════════════════════════════════════════════
    #  TEXTUR-PINSEL
    # ══════════════════════════════════════════════

    def pick_texture(self):
        """Textur-Bild laden und als Pinsel-Stempel verwenden."""
        path, _ = QFileDialog.getOpenFileName(self, "Textur wählen", "",
            "Bilder (*.png *.jpg *.jpeg *.bmp *.webp);;Alle (*)")
        if not path:
            return
        try:
            self.draw_texture = PILImage.open(path).convert("RGBA")
            self.status_bar.showMessage(
                f"🖼  Textur geladen: {path.split('/')[-1]}  |  "
                "Werkzeug auf 'Textur-Pinsel' wechseln zum Zeichnen.", 4000)
            # Automatisch zum Textur-Pinsel wechseln
            self.set_draw_tool("texture_brush")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))

    # ══════════════════════════════════════════════
    #  ZAUBERSTAB-WERKZEUG
    # ══════════════════════════════════════════════

    def start_magic_wand(self):
        """Zauberstab-Overlay starten — Klick wählt Farbbereiche aus."""
        if not self.layers:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        layer = self.layers[self.active_layer_idx]
        if not layer.image:
            return
        # Zeichenmodus deaktivieren falls aktiv
        if self.btn_draw_start.isChecked():
            self.btn_draw_start.setChecked(False)
        if self.canvas._overlay:
            self.canvas._overlay.close()
            self.canvas._overlay = None
        overlay = MagicWandOverlay(
            self.canvas, layer.image, self.wand_tolerance, self.canvas.get_zoom())
        overlay.selection_ready.connect(self._on_wand_ready)
        overlay.cancelled.connect(lambda: (
            self.status_bar.showMessage("Auswahl abgebrochen.", 2000),
        ))
        overlay.setGeometry(self.canvas.rect())
        overlay.show()
        self.canvas._overlay = overlay
        self.status_bar.showMessage(
            "🪄 Zauberstab: Klick = auswählen  |  Shift+Klick = hinzufügen  "
            "|  Enter = bestätigen  |  ESC = abbrechen")

    def _on_wand_ready(self, mask):
        """Callback wenn Zauberstab-Auswahl bestätigt wurde (Enter)."""
        self.selection_mask = mask
        if self.canvas._overlay:
            self.canvas._overlay.close()
            self.canvas._overlay = None
        self.status_bar.showMessage(
            "✅ Auswahl aktiv  |  'Auswahl aufheben' oder 'Auf neue Ebene ausschneiden'")

    def clear_selection(self):
        """Aktive Auswahl aufheben."""
        self.selection_mask = None
        self.status_bar.showMessage("Auswahl aufgehoben.", 2000)

    def cut_selection_to_layer(self):
        """Ausgewählten Bereich ausschneiden und auf neue Ebene verschieben."""
        if self.selection_mask is None:
            self.status_bar.showMessage("⚠ Keine Auswahl aktiv.", 2000); return
        if not self.layers:
            return
        layer = self.layers[self.active_layer_idx]
        if not layer.image:
            return
        self._push()
        # Ausschneiden: ausgewählte Pixel auf neue Ebene kopieren
        src  = layer.image.convert("RGBA")
        mask = self.selection_mask.resize(src.size, PILImage.Resampling.NEAREST)
        # Neue Ebene: nur ausgewählte Pixel sichtbar
        cut_img = PILImage.new("RGBA", src.size, (0, 0, 0, 0))
        cut_img.paste(src, mask=mask)
        # Original-Ebene: ausgewählte Pixel transparent machen
        inv_mask = mask.point([255 - v for v in range(256)])
        kept = PILImage.new("RGBA", src.size, (0, 0, 0, 0))
        kept.paste(src, mask=inv_mask)
        layer.image = kept
        self.original_pil = kept.copy()
        # Neue Ebene darüber einfügen
        self.layers.append(Layer(cut_img, "Ausschnitt"))
        self.active_layer_idx = len(self.layers) - 1
        self.selection_mask   = None
        self._update_display()
        self.status_bar.showMessage("✂ Ausschnitt auf neue Ebene — Verschieben/Skalieren möglich.", 3000)
        # Transform-Overlay sofort starten
        self._start_layer_transform(self.active_layer_idx)

    # ══════════════════════════════════════════════
    #  EBENEN-TRANSFORM: Verschieben & Skalieren
    # ══════════════════════════════════════════════

    def _start_layer_transform(self, layer_idx: int):
        """
        Startet das TransformOverlay für die angegebene Ebene.
        Die Ebene wird während des Transforms ausgeblendet, damit nur das
        verschobene Vorschaubild sichtbar ist (kein Duplikat).
        Enter bestätigt, ESC bricht ab und stellt die Sichtbarkeit wieder her.
        """
        if layer_idx >= len(self.layers):
            return
        layer = self.layers[layer_idx]
        if not layer.image:
            return
        # Laufendes Overlay schließen
        if self.canvas._overlay:
            self.canvas._overlay.close()
            self.canvas._overlay = None
        # Original-Ebene AUSBLENDEN während des Transforms
        layer.visible = False
        self._update_display()

        overlay = TransformOverlay(
            self.canvas, layer.image,
            layer.x, layer.y, self.canvas.get_zoom()
        )
        overlay.transform_done.connect(
            lambda x, y, s, i=layer_idx: self._on_transform_done(i, x, y, s))
        overlay.cancelled.connect(
            lambda i=layer_idx: self._on_transform_cancelled(i))
        overlay.setGeometry(self.canvas.rect())
        self.canvas._overlay = overlay
        overlay.setFocus()
        self.status_bar.showMessage(
            "🔀  Ziehen = Verschieben  |  Mausrad = Skalieren  |  Enter = OK  |  ESC = Abbruch")

    def _on_transform_done(self, layer_idx: int, new_x: int, new_y: int, scale: float):
        """Callback wenn Transform bestätigt — Ebene verschieben und skalieren."""
        if layer_idx >= len(self.layers):
            return
        layer = self.layers[layer_idx]
        if not layer.image:
            return
        self._push()
        # Bild skalieren wenn nötig
        if abs(scale - 1.0) > 0.001:
            w = max(1, int(layer.image.width  * scale))
            h = max(1, int(layer.image.height * scale))
            layer.image = layer.image.resize((w, h), PILImage.Resampling.LANCZOS)
        layer.x       = new_x
        layer.y       = new_y
        layer.visible = True          # Ebene wieder einblenden
        self.original_pil = layer.image.copy()
        self.canvas._overlay = None
        self._update_display()
        self.status_bar.showMessage("✅  Transform angewendet.", 2000)

    def _on_transform_cancelled(self, layer_idx: int):
        """Callback wenn Transform abgebrochen — Ebene wieder einblenden."""
        if layer_idx < len(self.layers):
            self.layers[layer_idx].visible = True
            self._update_display()
        self.canvas._overlay = None
        self.status_bar.showMessage("Transform abgebrochen.", 2000)

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
        if hasattr(self, "histogram_widget") and self.current_pil:
            self.histogram_widget.update_histogram(self.current_pil)


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

"""
sbs/utils.py
Hilfsfunktionen und Form-Bibliothek (SHAPE_LIBRARY) für den SBS Bildeditor.
"""
import math

from PyQt6.QtGui import QPixmap, QImage

try:
    from PIL import Image as PILImage, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

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

"""
sbs/dialogs.py
Dialog-Klassen für den SBS Bildeditor:
  FilterPreviewDialog, GifEditorDialog.
Enthält auch die internen Filterfunktionen (_cf_sepia, _cf_cool, etc.).
"""
import io, math, os

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QSpinBox, QSlider, QComboBox, QFrame, QSizePolicy,
    QProgressDialog, QMessageBox, QFileDialog, QGroupBox, QRadioButton, QStackedWidget
)
from PyQt6.QtGui import QPixmap, QColor, QFont, QImage, QPainter, QCursor
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal

try:
    from PIL import Image as PILImage, ImageFilter, ImageEnhance, ImageOps, ImageDraw, ImageChops
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .utils import pil_to_qpixmap

class FilterPreviewDialog(QDialog):
    """
    Zeigt alle verfügbaren Filter als 100px-Thumbnails in einem 5×4-Grid.
    Klick auf ein Thumbnail → Dialog schließt, Filter wird vom Aufrufer angewendet.

    Die Thumbnails werden von ImageEditor._generate_filter_previews() vorberechnet
    und als dict {filtername: QPixmap} übergeben.
    """
    def __init__(self, parent, previews: dict):
        """
        Erstellt den Filter-Vorschau-Dialog.
        Parameter:
          parent   – Eltern-Widget
          previews – Dict {filtername: QPixmap} mit vorberechneten Thumbnails
        """
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
        """Speichert den gewählten Filternamen und schließt den Dialog mit Accept."""
        self.chosen_filter = name
        self.accept()


# ══════════════════════════════════════════════════════════════
#  COLLAGE-FILTER: Standalone PIL-Transforms für den Collage-Editor
#  Unabhängig von ImageEditor – direkt auf PIL-Images anwendbar.
# ══════════════════════════════════════════════════════════════

def _cf_sepia(img):
    """
    Sepia-Filter für Collage-Zellen: Graustufenbild mit bräunlichem Ton.
    Rot-Kanal +10%, Grün-Kanal -10%, Blau-Kanal -30% → warmer Vintage-Look.
    """
    g = ImageOps.grayscale(img.convert("RGB"))
    return PILImage.merge("RGB", (
        g.point([min(255, int(x * 1.1)) for x in range(256)]),
        g.point([min(255, int(x * 0.9)) for x in range(256)]),
        g.point([min(255, int(x * 0.7)) for x in range(256)]),
    )).convert("RGBA")

def _cf_cool(img):
    """
    Kühler-Ton-Filter für Collage-Zellen: Rot -20, Blau +30.
    Simuliert einen Kälte- oder Morgengrauen-Farbstich.
    """
    r, g, b, *a = img.convert("RGBA").split()
    alpha = a[0] if a else PILImage.new("L", img.size, 255)
    return PILImage.merge("RGBA", (
        r.point([max(0, x - 20) for x in range(256)]), g,
        b.point([min(255, x + 30) for x in range(256)]), alpha))

def _cf_warm(img):
    """
    Warmer-Ton-Filter für Collage-Zellen: Rot +30, Blau -20.
    Simuliert Sonnenuntergangs- oder Kerzenlicht-Farbstich.
    """
    r, g, b, *a = img.convert("RGBA").split()
    alpha = a[0] if a else PILImage.new("L", img.size, 255)
    return PILImage.merge("RGBA", (
        r.point([min(255, x + 30) for x in range(256)]), g,
        b.point([max(0, x - 20) for x in range(256)]), alpha))

def _cf_psychedelic(img):
    """
    Psychedelic-Filter für Collage-Zellen: Kanal-Rotation (R→G, G→B, B→R)
    mit 3× Sättigungs-Verstärkung für extreme Farbverfälschung.
    """
    r, g, b, *a = img.convert("RGBA").split()
    alpha = a[0] if a else PILImage.new("L", img.size, 255)
    result = PILImage.merge("RGBA", (g, b, r, alpha))
    return ImageEnhance.Color(result).enhance(3.0)

def _cf_kaleidoscope(img):
    """
    Kaleidoskop-Filter für Collage-Zellen:
    Linke Hälfte spiegeln → obere Hälfte spiegeln → 4-fach-Symmetrie.
    """
    img = img.convert("RGBA")
    w, h = img.size
    hf_w = w // 2
    left = img.crop((0, 0, hf_w, h))
    top  = PILImage.new("RGBA", (w, h))
    top.paste(left, (0, 0))
    # Gespiegelte rechte Hälfte erzeugen
    top.paste(ImageOps.mirror(left), (hf_w, 0))
    hf_h     = h // 2
    top_half = top.crop((0, 0, w, hf_h))
    result   = PILImage.new("RGBA", (w, h))
    result.paste(top_half, (0, 0))
    # Gespiegelte untere Hälfte erzeugen
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

    Workflow:
      1. Modus wählen (RadioButton)
      2. Parameter einstellen (SpinBoxen)
      3. 'GIF generieren' → Frames werden berechnet und in Vorschau angezeigt
      4. 'Als GIF exportieren' oder 'Als Video exportieren'
    """

    _PW, _PH = 480, 320        # Vorschau-Größe in Pixeln
    _GOLD    = (255, 210, 0, 255)

    def __init__(self, parent, editor):
        """
        Erstellt den GIF-Editor-Dialog.

        Parameter:
          parent – Eltern-Widget (ImageEditor-Hauptfenster)
          editor – Referenz auf den ImageEditor (für Ebenen-Zugriff)
        """
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
        """Erstellt die gesamte UI des GIF-Editors: linke Steuerseite + rechte Vorschau."""
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
        """Erstellt das Parameter-Panel für den VHS-Distortion-Modus (Intensitäts-Spinner)."""
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
        """Erstellt das Parameter-Panel für den Sternschauer-Modus (Anzahl, Größe, Startpunkt)."""
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
        """Erstellt das Parameter-Panel für den Pfad-Animations-Modus (Wegpunkte + Ebenenauswahl)."""
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
        """Erstellt das Parameter-Panel für den Parallax-Modus (Amplitude, Schwingungen, Richtung)."""
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
        """Aktualisiert das zusammengesetzte Basis-Bild aus allen Ebenen des Editors."""
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
        """Löscht alle gesetzten Pfadpunkte und aktualisiert die Vorschau."""
        self._path_pts.clear()
        self._lbl_path.setText("Pfad: 0 Punkte")
        self._draw_preview_overlay()

    # ── Frame-Vorschau ────────────────────────────────────────

    def _show_frame(self, idx: int):
        """Zeigt den Frame mit Index idx (zyklisch) als Thumbnail in der Vorschau an."""
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
        """Generiert die GIF-Frames je nach gewähltem Modus und zeigt den ersten Frame in der Vorschau."""
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
        """Generiert VHS-Distortion-Frames: gerade Frames zeigen das Original, ungerade verzerrt."""
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
        """
        Erzeugt einen VHS-verzerrten Frame.
        Gerade Zeilen werden abgedunkelt, dann werden zufällige horizontale
        Bänder verschoben und aufgehellt, und abschließend werden die
        Farbkanäle R und B leicht gegeneinander verschoben (Chroma-Shift).
        """
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
        """
        Generiert Sternschauer-Frames.
        Goldene Sterne fallen vom Startpunkt nach unten, schwingen dabei
        sinusförmig horizontal und sammeln sich am unteren Bildrand.
        """
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
        """Zeichnet einen goldenen 5-Zack-Stern mit Mittelpunkt (cx, cy) und Außenradius r_outer."""
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
        """
        Generiert Pfad-Animations-Frames.
        Die ausgewählte Ebene bewegt sich entlang eines Catmull-Rom-Splines,
        der aus den gesetzten Wegpunkten berechnet wird.
        Das Objekt rotiert dabei entsprechend der Tangentenrichtung des Pfades.
        """
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
        """Exportiert die generierten Frames als GIF-Datei, optional mit zugeschnittenem Sound."""
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



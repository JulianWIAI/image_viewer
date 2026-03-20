"""
sbs/editor.py
Hauptfenster des SBS Bildeditors: ImageEditor (QMainWindow).
"""
import sys, os, io, math

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QFileDialog, QScrollArea, QStatusBar, QSizePolicy, QToolBar,
    QFrame, QVBoxLayout, QHBoxLayout, QSlider, QGroupBox,
    QDockWidget, QMessageBox, QDialog, QDialogButtonBox,
    QSpinBox, QComboBox, QTextEdit, QLineEdit,
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

from .layer import Layer
from .utils import pil_to_qpixmap, SHAPE_LIBRARY, draw_shape_on_pil
from .ai_worker import AIWorker
from .overlays import (
    CropOverlay, ShapePlacerOverlay, DrawOverlay,
    TransformOverlay, MovableRectOverlay, MovableLassoOverlay, MagicWandOverlay
)
from .widgets import HistogramWidget, LayerPanel, LabeledSlider, ImageCanvas
from .dialogs import FilterPreviewDialog, GifEditorDialog
from .threed import ThreeDModelDialog
from .collage import CollageDialog

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
        """
        Initialisiert den ImageEditor: setzt alle Zustandsvariablen auf Default-Werte
        und ruft alle _setup_*-Methoden auf um die UI vollständig aufzubauen.
        """
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
        """Setzt Fenstertitel, Größe, Mindestgröße und Anwendungsicon."""
        self.setWindowTitle("SBS Bildeditor v3")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)
        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "app_icon.png")
        self.setWindowIcon(QIcon(_icon_path))

    # ── Menüleiste ──────────────────────────────
    def _setup_menu(self):
        """Erstellt die gesamte Menüleiste mit allen Menüs und Aktionen inkl. Shortcuts."""
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
        """Erstellt die Toolbar mit häufig genutzten Aktionen (Öffnen, Speichern, Undo, Zoom, ...)."""
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
        """Erstellt den zentralen ScrollArea-Container mit dem ImageCanvas."""
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
        """Erstellt die Statusleiste am unteren Fensterrand mit einem Begrüßungstext."""
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
        """Speichert das Bild unter dem aktuellen Dateipfad (oder öffnet 'Speichern unter')."""
        if self.current_pil and self.current_file:
            self._save_to(self.current_file)
        else:
            self.save_file_as()

    def save_file_as(self):
        """Öffnet einen Speicherdialog und speichert das Bild unter einem neuen Pfad."""
        if not self.current_pil: return
        path, _ = QFileDialog.getSaveFileName(self, "Speichern unter", "",
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)")
        if path: self._save_to(path)

    def _save_to(self, path: str):
        """Speichert das aktuelle Composite-Bild unter path (JPEG-Konvertierung bei .jpg)."""
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
        """Stellt den letzten Undo-Snapshot wieder her (Ebenen-Zustand + Original-PIL)."""
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
        """Setzt alle Korrektur-Schieberegler auf den Standardwert 100 zurück."""
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

    def rotate_cw(self):
        """Dreht die aktive Ebene 90° im Uhrzeigersinn."""
        self._transform(lambda i: i.rotate(-90, expand=True))
    def rotate_ccw(self):
        """Dreht die aktive Ebene 90° gegen den Uhrzeigersinn."""
        self._transform(lambda i: i.rotate( 90, expand=True))
    def flip_horizontal(self):
        """Spiegelt die aktive Ebene horizontal."""
        self._transform(lambda i: ImageOps.mirror(i))
    def flip_vertical(self):
        """Spiegelt die aktive Ebene vertikal."""
        self._transform(lambda i: ImageOps.flip(i))

    def _transform(self, fn):
        """Wendet eine Transformationsfunktion auf die aktive Ebene an (mit Undo-Schritt)."""
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
        """Wandelt das Bild in Graustufen um."""
        self._filt(lambda i: ImageOps.grayscale(i).convert("RGBA"))

    def apply_sepia(self):
        """Wendet einen Sepia-Vintage-Filter (rotbraune Tönung) an."""
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
        """Invertiert alle Farbkanäle (Negativ-Effekt)."""
        self._filt(lambda i: ImageOps.invert(i.convert("RGB")).convert("RGBA"))

    # ── Schärfe/Weiche
    def apply_sharpen(self):
        """Schärft das Bild einmalig mit PIL SHARPEN-Filter."""
        self._filt(lambda i: i.filter(ImageFilter.SHARPEN))

    def apply_sharpen_strong(self):
        """Dreifaches Schärfen für deutlichen Effekt."""
        def sharpen3(img):
            for _ in range(3):
                img = img.filter(ImageFilter.SHARPEN)
            return img
        self._filt(sharpen3)

    def apply_blur(self):
        """Weichzeichner mit Gauss-Radius 2 (leichter Unschärfe-Effekt)."""
        self._filt(lambda i: i.filter(ImageFilter.GaussianBlur(radius=2)))

    def apply_blur_strong(self):
        """Starker Weichzeichner mit Gauss-Radius 6."""
        self._filt(lambda i: i.filter(ImageFilter.GaussianBlur(radius=6)))

    # ── Effekte
    def apply_emboss(self):
        """Emboss (Relief)-Filter: erzeugt einen geprägten 3D-Effekt."""
        self._filt(lambda i: i.filter(ImageFilter.EMBOSS))

    def apply_edges(self):
        """Kanten-Betonung: hebt Konturkanten stark hervor."""
        self._filt(lambda i: i.filter(ImageFilter.EDGE_ENHANCE_MORE))

    def apply_autocontrast(self):
        """Auto-Kontrast: streckt das Histogramm auf den vollen 0–255 Bereich."""
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
        """Vergrößert den Zoom um ZOOM_STEP (15%)."""
        self.canvas.set_zoom(self.canvas.get_zoom() + self.ZOOM_STEP)
        self._update_status()

    def zoom_out(self):
        """Verkleinert den Zoom um ZOOM_STEP (15%)."""
        self.canvas.set_zoom(self.canvas.get_zoom() - self.ZOOM_STEP)
        self._update_status()

    def zoom_reset(self):
        """Setzt Zoom auf 100% (1:1) zurück."""
        self.canvas.set_zoom(1.0); self._update_status()

    def zoom_fit(self):
        """Passt den Zoom so an, dass das gesamte Bild in den Viewport passt."""
        if not self.canvas._pix_orig: return
        aw = self.scroll.viewport().width()  - 20
        ah = self.scroll.viewport().height() - 20
        iw = self.canvas._pix_orig.width()
        ih = self.canvas._pix_orig.height()
        self.canvas.set_zoom(min(aw / iw, ah / ih))
        self._update_status()

    def wheelEvent(self, event):
        """Strg+Mausrad = Zoom rein/raus; ohne Strg = normales Scrollen."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_in() if event.angleDelta().y() > 0 else self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def event(self, event):
        """Verarbeitet Pinch-Gesten (Touch-Zoom) auf dem Hauptfenster."""
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
        """Zeigt das KI-Analyseergebnis im Textfeld an und scrollt nach oben."""
        self.ai_text.setPlainText(f"🤖  KI-Beschreibung (Beta):\n\n{desc.strip()}")
        # Zum Anfang scrollen damit kein Text abgeschnitten wirkt
        self.ai_text.verticalScrollBar().setValue(0)
        self._reset_ai_btn()

    def _on_ai_error(self, err: str):
        """Zeigt eine KI-Fehlermeldung im Textfeld an."""
        self.ai_text.setPlainText(f"⚠  Fehler:\n\n{err}")
        self._reset_ai_btn()

    def _reset_ai_btn(self):
        """Stellt den KI-Analyse-Button nach Erfolg oder Fehler wieder auf seinen Standardzustand zurück."""
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
        """Aktualisiert die Statusleiste mit Dateiname, Bildgröße, Zoom und Undo-Anzahl."""
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

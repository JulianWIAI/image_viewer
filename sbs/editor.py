"""
sbs/editor.py
Main window of the SBS Image Editor: ImageEditor (QMainWindow).
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
    Main window of the SBS Image Editor.

    RESPONSIBILITIES:
    • Manages application state (current image, undo stack, active tools)
    • Creates the menu bar, toolbar, canvas, dock panels, and status bar
    • Connects all signals to their slots (Signal/Slot pattern)
    • Delegates image operations to PIL (Pillow library)
    • Delegates AI analysis to AIWorker (separate thread)

    STATE VARIABLES:
      self.original_pil  – Unmodified original (used for reset and slider baseline)
      self.current_pil   – Currently edited composite image (what gets saved)
      self.history       – Undo stack, max. 20 layer-state snapshots
      self.draw_tool     – Active drawing tool ('pen', 'brush', ...)
      self.draw_color    – Current drawing colour (QColor)
      self.draw_size     – Brush size in pixels

    UNDO MECHANISM:
    Before every destructive operation: self._push() copies the current layer
    state into history. self.undo() restores the most recent snapshot.
    """
    ZOOM_STEP = 0.15   # Zoom step size per click (15%)

    def __init__(self):
        """
        Initialises the ImageEditor: sets all state variables to their default
        values and calls every _setup_* method to build the UI completely.
        """
        super().__init__()
        self.current_file       = None
        self.original_pil       = None   # Original of the active layer (used for slider reset)
        self.current_pil        = None   # Composite of all layers (used for display / saving)
        self.history            = []     # Undo stack (max. 20, stores layer state snapshots)
        self.ai_worker          = None

        # Layer system
        self.layers             = []     # List of Layer objects
        self.active_layer_idx   = 0      # Index of the currently active layer

        # Selection (magic wand)
        self.selection_mask     = None   # PIL "L" image or None
        self.wand_tolerance     = 30     # Tolerance 0–100

        # Texture brush
        self.draw_texture       = None   # PIL RGBA texture or None

        # Drawing state
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

    # ── Window ──────────────────────────────────
    def _setup_window(self):
        """Sets the window title, initial size, minimum size, and application icon."""
        self.setWindowTitle("SBS Bildeditor v3")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)
        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets", "app_icon.png")
        self.setWindowIcon(QIcon(_icon_path))

    # ── Menu bar ────────────────────────────────
    def _setup_menu(self):
        """Builds the complete menu bar with all menus, actions, and keyboard shortcuts."""
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
        """Creates the toolbar with the most frequently used actions (open, save, undo, zoom, ...)."""
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
        # Drawing tools
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

    # ── Canvas (centre) ─────────────────────────
    def _setup_central(self):
        """Creates the central ScrollArea container that holds the ImageCanvas."""
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

    # ── Settings panel (right) ──────────────────
    def _setup_panel(self):
        """
        Right dock panel containing adjustments, filters, and AI analysis.
        A scroll area ensures all sections remain accessible regardless of window height.
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

        # ── Crop tools
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

        # ── Drawing tools
        grp_draw = self._grp("✏️  ZEICHNEN")
        dl = QVBoxLayout(grp_draw); dl.setSpacing(5)

        # Tool selection (2×4 grid)
        draw_tools = [
            ("✏️ Stift",    "pen"),          ("🖌 Pinsel",       "brush"),
            ("💧 Unschärfe","blur"),         ("⬜ Radierer",     "eraser"),
            ("╱ Linie",    "line"),         ("▭ Rechteck",     "rect"),
            ("◯ Ellipse",  "ellipse"),      ("T Text",         "text"),
            ("〜 Kurve",   "curve"),        ("🖼 Textur-Pinsel","texture_brush"),
            ("🎨 Farbe…",  "__color__"),    ("",               "__noop__"),
        ]
        # Style for active/inactive buttons
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

        # Colour preview
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

        # Brush size
        self.sl_brush_size = LabeledSlider("Pinselgröße", 1, 50, self.draw_size)

        def on_size_change(v):
            self.draw_size = v
            # Restart overlay immediately with the new brush size
            if self.btn_draw_start.isChecked() and self.current_pil:
                if self.canvas._overlay:
                    self.canvas._overlay.blockSignals(True)
                    self.canvas._overlay.close()
                    self.canvas._overlay = None
                self._start_draw_overlay()

        self.sl_brush_size.value_changed.connect(on_size_change)
        dl.addWidget(self.sl_brush_size)

        # Start / stop drawing button
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

        # Load texture button
        _btn_style2 = ("background:#2d2d2d; color:#ccc; border:1px solid #3a3a3a; "
                       "border-radius:4px; padding:6px; font-size:11px;")
        btn_tex = QPushButton("🖼  Textur laden…")
        btn_tex.setStyleSheet(_btn_style2)
        btn_tex.setToolTip("Bild als Textur-Stempel laden (aktiviert Textur-Pinsel-Werkzeug)")
        btn_tex.clicked.connect(self.pick_texture)
        dl.addWidget(btn_tex)

        layout.addWidget(grp_draw)

        # ── Magic wand tool
        grp_wand = self._grp("🪄  ZAUBERSTAB")
        wl = QVBoxLayout(grp_wand); wl.setSpacing(5)

        wand_info = QLabel("Klick = Bereich auswählen\nShift+Klick = hinzufügen\nEnter = bestätigen")
        wand_info.setStyleSheet("color:#888; font-size:10px; background:#141414; "
                                "border-radius:3px; padding:5px;")
        wl.addWidget(wand_info)

        # Tolerance slider
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

        # Selection actions
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

        # ── Text-to-Drawing (shape library)
        grp_shapes = self._grp("✨  TEXT-TO-DRAWING")
        sl_layout  = QVBoxLayout(grp_shapes); sl_layout.setSpacing(6)

        info_lbl = QLabel("Form wählen → Größe setzen\n→ auf dem Bild platzieren")
        info_lbl.setStyleSheet(
            "color:#888; font-size:10px; background:#141414; "
            "border-radius:3px; padding:5px;")
        sl_layout.addWidget(info_lbl)

        # Shape dropdown
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

        # Shape size slider
        self.sl_shape_size = LabeledSlider("Formgröße (px)", 30, 400, 120)
        sl_layout.addWidget(self.sl_shape_size)

        # Place button
        btn_place = QPushButton("✨  Form auf Bild platzieren")
        btn_place.setStyleSheet("""
            QPushButton { background:#1a3050; color:#7ec8f7; border:1px solid #2a5080;
                border-radius:4px; padding:8px; font-size:12px; font-weight:bold; }
            QPushButton:hover    { background:#1e4070; border-color:#4fc3f7; color:#fff; }
            QPushButton:disabled { background:#1a1a1a; color:#444; }
        """)
        btn_place.clicked.connect(self.start_shape_placer)
        sl_layout.addWidget(btn_place)

        # Quick-pick preview grid (small buttons for each shape)
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

        # ── Basic adjustments
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
                # Disable separator entries so they are not selectable
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

        # ── AI analysis panel
        grp_ai = self._grp("🤖  KI-ANALYSE  (Moondream – Beta)")
        al = QVBoxLayout(grp_ai); al.setSpacing(6)

        # Large text area for the AI response
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
        """Create the status bar at the bottom of the window with a welcome message."""
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            "background:#111; color:#666; font-size:11px; border-top:1px solid #2a2a2a;")
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "SBS Bildeditor  –  Öffne ein Bild  |  ✂ Crop  |  🎨 Filter  |  📐 Ebenen  |  🤖 KI")

    # ── Ebenen-Dock (links) ──────────────────────
    def _setup_layers_dock(self):
        """
        Second dock widget on the left side: hosts the layer panel.
        Keeping it separate from the right settings panel keeps the UI uncluttered.
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
        Flatten all visible layers into a single RGBA image.

        Order: layers[0] = background (painted first),
               layers[-1] = top layer (painted last).
        Opacity is applied to each layer's alpha channel before
        it is pasted onto the result canvas.
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
        Recomposite all layers and refresh:
          • canvas display
          • histogram
          • layer panel thumbnails
        Called after every layer change.
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
        """Open an image file and load it into the editor as a PIL Image."""
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
        """Save the image to the current file path, or open Save-As if no path is set."""
        if self.current_pil and self.current_file:
            self._save_to(self.current_file)
        else:
            self.save_file_as()

    def save_file_as(self):
        """Open a save dialog and write the image to a new file path."""
        if not self.current_pil: return
        path, _ = QFileDialog.getSaveFileName(self, "Speichern unter", "",
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)")
        if path: self._save_to(path)

    def _save_to(self, path: str):
        """Write the current composite image to *path* (converts to RGB for JPEG)."""
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
        Push the current layer state onto the undo stack.

        UNDO MECHANISM:
        Every destructive operation calls _push() first.
        A snapshot of all layers (image copy + metadata) is stored.
        Memory cost: ~4 bytes × width × height × number of layers per step.
        Maximum of 20 steps.
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
        """Restore the last undo snapshot (layer state + original PIL image)."""
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
        """Reset the active layer to its last committed state (before any slider adjustments)."""
        if not self.original_pil or not self.layers:
            return
        self._push()
        self.layers[self.active_layer_idx].image = self.original_pil.copy()
        self._reset_sliders()
        self._update_display()
        self.status_bar.showMessage("🔄 Original wiederhergestellt", 2000)

    def _reset_sliders(self):
        """Reset all correction sliders to their neutral default value of 100."""
        for sl in [self.sl_brightness, self.sl_contrast,
                   self.sl_saturation, self.sl_sharpness]:
            sl.reset(100)

    # ══════════════════════════════════════════════
    #  CROP-WERKZEUGE
    # ══════════════════════════════════════════════

    def start_rect_crop(self):
        """
        Rectangle crop: launch the overlay so the user can drag a crop region.
        The overlay is displayed on top of the canvas.
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
        Lasso crop: launch the overlay so the user can draw a freehand shape.
        The area outside the polygon becomes transparent.
        """
        if not self.current_pil:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        self.status_bar.showMessage(
            "🔮 Lasso-Crop: Mit gedrückter Maustaste zeichnen → loslassen zum Zuschneiden  |  ESC = Abbrechen")
        overlay = self.canvas.start_crop_overlay("lasso")
        overlay.lasso_selected.connect(self._on_lasso_selected)
        overlay.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))

    def _on_rect_selected(self, rect: QRect):
        """After the rectangle is drawn: show a MovableRectOverlay for fine-tuning."""
        mov = MovableRectOverlay(self.canvas, rect)
        mov.confirmed.connect(self._do_rect_crop)
        mov.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))
        mov.setGeometry(self.canvas.rect())
        self.canvas._overlay = mov
        self.status_bar.showMessage(
            "Auswahl anpassen: Ziehen=Verschieben  |  Ecken=Skalieren  |  Enter=Zuschneiden  |  ESC=Abbruch")

    def _on_lasso_selected(self, points: list):
        """After the lasso is drawn: show a MovableLassoOverlay for repositioning."""
        mov = MovableLassoOverlay(self.canvas, points)
        mov.confirmed.connect(self._do_lasso_crop)
        mov.cancelled.connect(lambda: self.status_bar.showMessage("Abgebrochen.", 2000))
        mov.setGeometry(self.canvas.rect())
        self.canvas._overlay = mov
        self.status_bar.showMessage(
            "Auswahl verschieben: Ziehen=Verschieben  |  Enter=Zuschneiden  |  ESC=Abbruch")

    def _do_rect_crop(self, rect: QRect):
        """
        Convert rectangle coordinates from overlay (screen) space to image space
        and crop the PIL image.

        COORDINATE CONVERSION (zoom correction):
        The overlay works in screen pixels which are scaled by the zoom factor.
        PIL.Image.crop() requires actual image pixels.
        Formula: image_px = screen_px / zoom_factor
        Example: zoom=2.0, click at x=400 → image pixel x=200
        """
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return

        # Divide overlay coordinates by the zoom factor to get image coordinates
        z = self.canvas.get_zoom()
        x1 = int(rect.left()   / z)
        y1 = int(rect.top()    / z)
        x2 = int(rect.right()  / z)
        y2 = int(rect.bottom() / z)

        # Clamp to image bounds
        w, h = layer.image.width, layer.image.height
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 > x1 and y2 > y1:
            self._push()
            rgba = layer.image.convert("RGBA")
            # ── Copy cropped region to a new layer ────────────
            cut_img = rgba.crop((x1, y1, x2, y2))
            new_layer   = Layer(cut_img, "Ausschnitt")
            new_layer.x = layer.x + x1
            new_layer.y = layer.y + y1
            # ── Punch a transparent hole in the original ───────
            orig_mod = rgba.copy()
            hole = PILImage.new("RGBA", (x2 - x1, y2 - y1), (0, 0, 0, 0))
            orig_mod.paste(hole, (x1, y1))
            layer.image = orig_mod
            # ── Update the layer stack ─────────────────────────
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
        Lasso crop: apply a freehand polygon as a mask.

        MASKING CONCEPT:
        1. Create a black mask (mode "L") at full image size.
        2. Fill the polygon with white → white = visible, black = transparent.
        3. PIL.Image.paste() with the mask copies only the white (selected) region.
        4. crop() to the polygon bounding box → final result.

        The result is an RGBA image with a transparent background,
        suitable for saving as PNG with transparency.
        """
        if not self.layers or len(points) < 3: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return

        z = self.canvas.get_zoom()
        w, h = layer.image.width, layer.image.height

        # Convert overlay points to image pixels
        img_pts = [(int(p.x() / z), int(p.y() / z)) for p in points]

        # Compute the bounding box of the polygon
        xs = [p[0] for p in img_pts]
        ys = [p[1] for p in img_pts]
        x1, y1 = max(0, min(xs)), max(0, min(ys))
        x2, y2 = min(w, max(xs)), min(h, max(ys))

        if x2 <= x1 or y2 <= y1: return

        self._push()

        # Build the mask: fill the polygon with white
        mask = PILImage.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        draw.polygon(img_pts, fill=255)

        # Composite image with mask (area outside polygon → transparent)
        rgba = layer.image.copy().convert("RGBA")
        result = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        result.paste(rgba, mask=mask)

        # ── Copy the polygon region to a new layer ────────────
        cut_img = result.crop((x1, y1, x2, y2))
        new_layer   = Layer(cut_img, "Lasso-Ausschnitt")
        new_layer.x = layer.x + x1
        new_layer.y = layer.y + y1
        # ── Punch a transparent hole in the original (inverse mask) ──
        inv_mask  = mask.point([255 - v for v in range(256)])
        orig_mod  = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
        orig_mod.paste(rgba, mask=inv_mask)
        layer.image = orig_mod
        # ── Update the layer stack ─────────────────────────────
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
        Apply brightness/contrast/saturation/sharpness adjustments live.

        IMPORTANT — always start from the original:
        If current_pil were used as the base, adjustments would compound on each
        slider move. Example: slider set to 150 twice → brightness 150% × 150% = 225%.
        Solution: always use self.original_pil as the base and apply all sliders
        in a single pass.

        ImageEnhance values: 1.0 = no change, <1 = less, >1 = more.
        Slider value 100 → 100/100 = 1.0 (neutral)
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
        """Rotate the active layer 90° clockwise."""
        self._transform(lambda i: i.rotate(-90, expand=True))
    def rotate_ccw(self):
        """Rotate the active layer 90° counter-clockwise."""
        self._transform(lambda i: i.rotate( 90, expand=True))
    def flip_horizontal(self):
        """Flip the active layer horizontally."""
        self._transform(lambda i: ImageOps.mirror(i))
    def flip_vertical(self):
        """Flip the active layer vertically."""
        self._transform(lambda i: ImageOps.flip(i))

    def _transform(self, fn):
        """Apply a transformation function to the active layer (with an undo step)."""
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
        """Read the active filter from the dropdown and apply it."""
        name = self.filter_combo.currentText()
        fn   = self._filter_map.get(name)
        if fn:
            fn()
        else:
            self.status_bar.showMessage("Bitte einen Filter auswählen.", 2000)

    def _filt(self, fn):
        """Helper: apply a filter function to the active layer with an undo step."""
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return
        self._push()
        layer.image       = fn(layer.image).convert("RGBA")
        self.original_pil = layer.image.copy()
        self._reset_sliders()
        self._update_display()
        self._update_status()

    # ── Colour filters
    def apply_grayscale(self):
        """Convert the image to greyscale."""
        self._filt(lambda i: ImageOps.grayscale(i).convert("RGBA"))

    def apply_sepia(self):
        """Apply a sepia vintage filter (reddish-brown tint)."""
        def sepia(img):
            g = ImageOps.grayscale(img)
            r = g.point(lambda x: min(255, int(x * 1.1)))
            gch = g.point(lambda x: min(255, int(x * 0.9)))
            b = g.point(lambda x: min(255, int(x * 0.7)))
            return PILImage.merge("RGB", (r, gch, b))
        self._filt(sepia)

    def apply_cool(self):
        """Cool blue tone — simulates a cooling filter."""
        def cool(img):
            r, g, b, *a = img.convert("RGBA").split()
            r = r.point(lambda x: max(0, x - 20))
            b = b.point(lambda x: min(255, x + 30))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(cool)

    def apply_warm(self):
        """Warm orange tone — simulates a warming filter."""
        def warm(img):
            r, g, b, *a = img.convert("RGBA").split()
            r = r.point(lambda x: min(255, x + 30))
            b = b.point(lambda x: max(0, x - 20))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(warm)

    def apply_purple(self):
        """Purple/violet colour cast."""
        def purple(img):
            r, g, b, *a = img.convert("RGBA").split()
            r = r.point(lambda x: min(255, x + 20))
            b = b.point(lambda x: min(255, x + 40))
            g = g.point(lambda x: max(0, x - 10))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(purple)

    def apply_green(self):
        """Green colour cast filter."""
        def green(img):
            r, g, b, *a = img.convert("RGBA").split()
            g = g.point(lambda x: min(255, x + 30))
            r = r.point(lambda x: max(0, x - 10))
            return PILImage.merge("RGBA", (r, g, b, a[0]) if a else (r, g, b))
        self._filt(green)

    def apply_invert(self):
        """Invert all colour channels (negative effect)."""
        self._filt(lambda i: ImageOps.invert(i.convert("RGB")).convert("RGBA"))

    # ── Sharpness / Blur
    def apply_sharpen(self):
        """Sharpen the image once with PIL's SHARPEN filter."""
        self._filt(lambda i: i.filter(ImageFilter.SHARPEN))

    def apply_sharpen_strong(self):
        """Apply the sharpen filter three times for a pronounced effect."""
        def sharpen3(img):
            for _ in range(3):
                img = img.filter(ImageFilter.SHARPEN)
            return img
        self._filt(sharpen3)

    def apply_blur(self):
        """Gaussian blur with radius 2 (subtle softening effect)."""
        self._filt(lambda i: i.filter(ImageFilter.GaussianBlur(radius=2)))

    def apply_blur_strong(self):
        """Strong Gaussian blur with radius 6."""
        self._filt(lambda i: i.filter(ImageFilter.GaussianBlur(radius=6)))

    # ── Effects
    def apply_emboss(self):
        """Emboss filter: produces a raised, 3D relief effect."""
        self._filt(lambda i: i.filter(ImageFilter.EMBOSS))

    def apply_edges(self):
        """Edge enhancement: strongly emphasises contour edges."""
        self._filt(lambda i: i.filter(ImageFilter.EDGE_ENHANCE_MORE))

    def apply_autocontrast(self):
        """Auto-contrast: stretches the histogram to cover the full 0–255 range."""
        self._filt(lambda i: ImageOps.autocontrast(i.convert("RGB")))

    def apply_noise(self):
        """Add random noise to the image (analogue film look)."""
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

    # ── Creative filters
    def apply_comic(self):
        """
        Comic/cartoon filter:
        edge enhancement + posterise (reduce colour levels) + boost saturation.
        Produces a drawn, comic-book look.
        """
        def comic(img):
            rgb = img.convert("RGB")
            # Extract edges
            edges = rgb.filter(ImageFilter.EDGE_ENHANCE_MORE)
            # Posterise: reduce to 4 colour levels
            poster = ImageOps.posterize(rgb, 4)
            # Boost saturation
            poster = ImageEnhance.Color(poster).enhance(2.5)
            # Sharpen
            poster = ImageEnhance.Sharpness(poster).enhance(3.0)
            return poster
        self._filt(comic)

    def apply_dog_vision(self):
        """
        Dog vision (dichromacy):
        Dogs cannot see red — only blue and yellow.
        The red channel is replaced by green to simulate this.
        """
        def dog(img):
            r, g, b, *a = img.convert("RGBA").split()
            # Replace red with yellow/green (dichromacy simulation)
            new_r = g   # kein echtes Rot
            new_g = g
            new_b = b.point(lambda x: min(255, int(x * 1.3)))
            result = PILImage.merge("RGBA", (new_r, new_g, new_b, a[0]) if a else (new_r, new_g, new_b))
            return ImageEnhance.Contrast(result).enhance(0.8)
        self._filt(dog)

    def apply_psychedelic(self):
        """
        Psychedelic filter: extreme colour shift via
        channel rotation (R→G, G→B, B→R) + high saturation.
        """
        def psycho(img):
            r, g, b, *a = img.convert("RGBA").split()
            # Rotate channels to create colour distortion
            result = PILImage.merge("RGBA", (g, b, r, a[0]) if a else (g, b, r))
            return ImageEnhance.Color(result).enhance(3.0)
        self._filt(psycho)

    def apply_night(self):
        """
        Night mode: blue cast + darkening + slight noise.
        Simulates night photography or night-vision devices.
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
        Photo vignette: dark edges, bright centre.
        A classic retro photo effect.
        """
        def vignette(img):
            img = img.convert("RGBA")
            w, h = img.width, img.height
            # Create a black vignette mask
            mask = PILImage.new("L", (w, h), 255)
            draw = ImageDraw.Draw(mask)
            # Gradient from centre outward
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
        """Film grain: fine noise + slight saturation reduction."""
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
        Watercolour effect: blur + edge enhancement + high saturation.
        Produces a painted, soft look.
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
        """High-contrast swatch: extreme posterise + contrast boost."""
        def hc(img):
            img = ImageOps.posterize(img.convert("RGB"), 2)
            return ImageEnhance.Contrast(img).enhance(3.0)
        self._filt(hc)

    def apply_kaleidoscope(self):
        """
        Kaleidoscope filter: split the image into 4 mirrored segments.

        ALGORITHM:
        1. Take the left half of the image.
        2. Mirror it horizontally → right half (left↔right symmetry).
        3. Take the top half of the result.
        4. Flip it vertically → bottom half (top↔bottom symmetry).
        Result: a 4-fold symmetric kaleidoscope pattern.
        """
        def kaleidoscope(img):
            img = img.convert("RGBA")
            w, h = img.size
            half_w = w // 2
            # Steps 1+2: left half + mirrored right half
            left   = img.crop((0, 0, half_w, h))
            right  = ImageOps.mirror(left)
            top    = PILImage.new("RGBA", (w, h))
            top.paste(left,  (0,      0))
            top.paste(right, (half_w, 0))
            # Steps 3+4: top half + flipped bottom half
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
        VHS flicker filter: simulates a flickering 1980s CRT screen.

        ALGORITHM:
        1. Horizontal scan lines: darken every other row (CRT effect).
        2. Random glitch stripes: narrow horizontal bands are shifted
           horizontally (tracking error) and brightened/darkened.
        3. Subtle chroma shift: red channel slightly left, blue slightly right.
        4. Light noise across the entire image.
        Result: only a subset of the stripes is visible — not the whole image.
        """
        import random
        def vhs(img):
            img  = img.convert("RGB")
            w, h = img.size
            pixels = list(img.getdata())

            rng = random.Random(42)   # Fixed seed for reproducible filter preview

            # ── 1. Scan lines: darken every other row by 15 % ────────
            for y in range(0, h, 2):
                for x in range(w):
                    idx = y * w + x
                    r, g, b = pixels[idx]
                    pixels[idx] = (int(r * 0.85), int(g * 0.85), int(b * 0.85))

            img.putdata(pixels)

            # ── 2. Glitch stripes (tracking error) ───────────────────
            # Approx. 8–14 random stripes, each 2–12 px tall
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
        Anaglyph 3D filter: simulates the red/cyan glasses effect.

        ALGORITHM:
        1. Left eye  = original (red channel).
        2. Right eye = horizontally shifted copy (cyan = G+B channels).
        3. Shift ≈ image width / 40 → realistic stereo separation.
        4. Combine: R from left, G+B from right → anaglyph image.

        For the best effect, wear a pair of red/cyan 3D glasses.
        """
        def anaglyph(img):
            img   = img.convert("RGB")
            w, h  = img.size
            shift = max(2, w // 40)

            # Left eye: red channel only
            r_ch, _, _ = img.split()

            # Right eye: shifted `shift` pixels to the right
            shifted = PILImage.new("RGB", (w, h), (0, 0, 0))
            shifted.paste(img.crop((0, 0, w - shift, h)), (shift, 0))
            _, g_ch, b_ch = shifted.split()

            # Combine: R from left, G+B from right
            return PILImage.merge("RGB", (r_ch, g_ch, b_ch))

        self._filt(anaglyph)

    # ══════════════════════════════════════════════
    #  FILTER-VORSCHAU
    # ══════════════════════════════════════════════

    def _generate_filter_previews(self, thumb_size: int = 100) -> dict:
        """
        Generate thumbnail previews for all available filters.

        TECHNIQUE — temporary monkey-patching:
        _filt() has side-effects (canvas.update_image, _reset_sliders, _update_status).
        These are replaced with no-ops for the duration of preview generation and then
        fully restored, leaving the editor state unchanged.
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
        """Open the filter preview dialog. Thumbnails are computed on the fly."""
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
        """Open the GIF editor dialog."""
        if not self.layers:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        GifEditorDialog(self, self).exec()

    def open_3d_dialog(self):
        """Open the 2D → 3D model viewer dialog."""
        if not self.layers:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        ThreeDModelDialog(self, self).exec()

    def open_collage_dialog(self):
        """Open the collage editor. The result replaces the current image."""
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
        Switch the active drawing tool — takes effect immediately.

        BUG FIX:
        Problem: The old overlay remained active after switching tools.
        Cause:   The overlay stores the tool at creation time;
                 changing self.draw_tool did not update the running overlay.
        Fix:     Close the old overlay with blockSignals(True)
                 (blockSignals prevents a callback loop via the destroyed signal),
                 then immediately start a new overlay with the new tool.
        """
        # Magic wand is not a drawing tool — launch it directly
        if tool == "magic_wand":
            self.start_magic_wand()
            return

        self.draw_tool = tool

        # All tool buttons: activate only the selected one
        for k, btn in self._draw_btns.items():
            btn.setChecked(k == tool)

        # Restart the overlay immediately if drawing mode is active
        if self.btn_draw_start.isChecked() and self.current_pil:
            # Close old overlay cleanly (blockSignals prevents callback loop)
            if self.canvas._overlay:
                self.canvas._overlay.blockSignals(True)
                self.canvas._overlay.close()
                self.canvas._overlay = None
            # Start a new overlay with the new tool immediately
            self._start_draw_overlay()

        self.status_bar.showMessage(
            f"✏️  Werkzeug: {tool}  |  Farbe: {self.draw_color.name()}  |  Größe: {self.draw_size}px"
        )

    def pick_color(self):
        """Open the colour dialog and immediately restart the overlay with the new colour."""
        color = QColorDialog.getColor(self.draw_color, self, "Farbe wählen")
        if color.isValid():
            self.draw_color = color
            self.color_preview.setStyleSheet(
                f"background:{color.name()}; border:1px solid #555; border-radius:3px;")
            # Restart the overlay immediately with the new colour
            if self.btn_draw_start.isChecked() and self.current_pil:
                if self.canvas._overlay:
                    self.canvas._overlay.blockSignals(True)
                    self.canvas._overlay.close()
                    self.canvas._overlay = None
                self._start_draw_overlay()

    def _on_draw_toggle(self, active: bool):
        """
        Toggle drawing mode on or off.
        When activated: start the DrawOverlay.
        When deactivated: close the overlay.
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
        Start a new DrawOverlay with the current tool, colour, and brush size.
        Connects the drawing_done signal to the method that commits the stroke
        permanently to the PIL image.
        """
        overlay = self.canvas.start_draw_overlay(
            self.draw_tool, self.draw_color, self.draw_size,
            texture=self.draw_texture
        )
        overlay.drawing_done.connect(self._apply_draw_fn)
        # If overlay is closed via ESC → reset the toggle button
        overlay.destroyed.connect(lambda: (
            self.btn_draw_start.blockSignals(True),
            self.btn_draw_start.setChecked(False),
            self.btn_draw_start.setText("▶  Zeichnen aktivieren"),
            self.btn_draw_start.blockSignals(False)
        ))

    def _apply_draw_fn(self, draw_fn):
        """
        Commit a drawing function permanently to the active layer.
        Creates an undo step, then restarts the overlay with the
        CURRENTLY selected tool (not the old one).
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
        # Restart overlay with the CURRENTLY selected tool
        if self.btn_draw_start.isChecked():
            if self.canvas._overlay:
                self.canvas._overlay.blockSignals(True)
                self.canvas._overlay.close()
                self.canvas._overlay = None
            self._start_draw_overlay()  # uses self.draw_tool (always current)

    # ══════════════════════════════════════════════
    #  TEXT-TO-DRAWING: Formen platzieren
    # ══════════════════════════════════════════════

    def start_shape_placer(self):
        """
        Start the shape placer overlay.
        The selected shape is shown as a preview under the cursor;
        a click places it permanently on the image.
        """
        if not self.current_pil:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000)
            return

        # Deactivate drawing mode if it is currently active
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
        Callback fired when the user clicks on the canvas.
        Draws the selected shape permanently onto the PIL image.
        """
        if not self.layers: return
        layer = self.layers[self.active_layer_idx]
        if not layer.image: return
        self._push()
        color_tuple = (self.draw_color.red(), self.draw_color.green(),
                       self.draw_color.blue(), 255)
        size      = self.sl_shape_size.value()
        lw        = max(2, size // 40)   # Line width proportional to shape size
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
        """Increase the zoom level by ZOOM_STEP (15 %)."""
        self.canvas.set_zoom(self.canvas.get_zoom() + self.ZOOM_STEP)
        self._update_status()

    def zoom_out(self):
        """Decrease the zoom level by ZOOM_STEP (15 %)."""
        self.canvas.set_zoom(self.canvas.get_zoom() - self.ZOOM_STEP)
        self._update_status()

    def zoom_reset(self):
        """Reset zoom to 100 % (1:1 pixel mapping)."""
        self.canvas.set_zoom(1.0); self._update_status()

    def zoom_fit(self):
        """Adjust zoom so the entire image fits within the viewport."""
        if not self.canvas._pix_orig: return
        aw = self.scroll.viewport().width()  - 20
        ah = self.scroll.viewport().height() - 20
        iw = self.canvas._pix_orig.width()
        ih = self.canvas._pix_orig.height()
        self.canvas.set_zoom(min(aw / iw, ah / ih))
        self._update_status()

    def wheelEvent(self, event):
        """Ctrl+scroll = zoom in/out; without Ctrl = normal scrolling."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_in() if event.angleDelta().y() > 0 else self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def event(self, event):
        """Handle pinch gestures (touch zoom) on the main window."""
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
        """Start Moondream image analysis in a background thread."""
        if not self.current_pil: return
        self.ai_text.setPlainText("🔄  Moondream analysiert das Bild…\n\nBitte warten.")
        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("⏳  Analysiere…")
        self.ai_worker = AIWorker(self.current_pil)
        self.ai_worker.result_ready.connect(self._on_ai_result)
        self.ai_worker.error_occurred.connect(self._on_ai_error)
        self.ai_worker.start()

    def _on_ai_result(self, desc: str):
        """Display the AI analysis result in the text field and scroll to the top."""
        self.ai_text.setPlainText(f"🤖  KI-Beschreibung (Beta):\n\n{desc.strip()}")
        # Scroll to top so the beginning of the description is immediately visible
        self.ai_text.verticalScrollBar().setValue(0)
        self._reset_ai_btn()

    def _on_ai_error(self, err: str):
        """Display an AI error message in the text field."""
        self.ai_text.setPlainText(f"⚠  Fehler:\n\n{err}")
        self._reset_ai_btn()

    def _reset_ai_btn(self):
        """Restore the AI analyse button to its default state after success or error."""
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("🤖  Analysieren  (Strg+A)")

    # ══════════════════════════════════════════════
    #  TEXTUR-PINSEL
    # ══════════════════════════════════════════════

    def pick_texture(self):
        """Load a texture image and use it as a brush stamp."""
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
        """Launch the magic wand overlay — clicks select colour regions."""
        if not self.layers:
            self.status_bar.showMessage("⚠ Zuerst ein Bild öffnen.", 2000); return
        layer = self.layers[self.active_layer_idx]
        if not layer.image:
            return
        # Deactivate drawing mode if currently active
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
        """Callback fired when the magic wand selection is confirmed (Enter)."""
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
        """Cut the selected region and move it onto a new layer."""
        if self.selection_mask is None:
            self.status_bar.showMessage("⚠ Keine Auswahl aktiv.", 2000); return
        if not self.layers:
            return
        layer = self.layers[self.active_layer_idx]
        if not layer.image:
            return
        self._push()
        # Copy selected pixels to a new layer
        src  = layer.image.convert("RGBA")
        mask = self.selection_mask.resize(src.size, PILImage.Resampling.NEAREST)
        # New layer: only the selected pixels are visible
        cut_img = PILImage.new("RGBA", src.size, (0, 0, 0, 0))
        cut_img.paste(src, mask=mask)
        # Original layer: make selected pixels transparent
        inv_mask = mask.point([255 - v for v in range(256)])
        kept = PILImage.new("RGBA", src.size, (0, 0, 0, 0))
        kept.paste(src, mask=inv_mask)
        layer.image = kept
        self.original_pil = kept.copy()
        # Insert the new layer above
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
        Start the TransformOverlay for the specified layer.
        The layer is hidden during the transform so only the draggable preview
        is visible (no duplicate). Enter confirms, ESC cancels and restores
        layer visibility.
        """
        if layer_idx >= len(self.layers):
            return
        layer = self.layers[layer_idx]
        if not layer.image:
            return
        # Close any running overlay first
        if self.canvas._overlay:
            self.canvas._overlay.close()
            self.canvas._overlay = None
        # HIDE the original layer during the transform
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
        """Callback fired when the transform is confirmed — move and scale the layer."""
        if layer_idx >= len(self.layers):
            return
        layer = self.layers[layer_idx]
        if not layer.image:
            return
        self._push()
        # Scale the image only if the scale factor changed meaningfully
        if abs(scale - 1.0) > 0.001:
            w = max(1, int(layer.image.width  * scale))
            h = max(1, int(layer.image.height * scale))
            layer.image = layer.image.resize((w, h), PILImage.Resampling.LANCZOS)
        layer.x       = new_x
        layer.y       = new_y
        layer.visible = True          # Make the layer visible again
        self.original_pil = layer.image.copy()
        self.canvas._overlay = None
        self._update_display()
        self.status_bar.showMessage("✅  Transform angewendet.", 2000)

    def _on_transform_cancelled(self, layer_idx: int):
        """Callback fired when the transform is cancelled — restore layer visibility."""
        if layer_idx < len(self.layers):
            self.layers[layer_idx].visible = True
            self._update_display()
        self.canvas._overlay = None
        self.status_bar.showMessage("Transform abgebrochen.", 2000)

    # ══════════════════════════════════════════════
    #  STATUS
    # ══════════════════════════════════════════════

    def _update_status(self):
        """Update the status bar with file name, image size, zoom level, and undo count."""
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

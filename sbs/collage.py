"""
sbs/collage.py – Collage editor dialog for the SBS Image Editor.

Provides CollageDialog, which lets the user arrange multiple images in a
configurable grid, apply per-cell filters, swap cells, and export the result
as a single composite PIL image.
"""
import os

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QSpinBox, QComboBox, QFileDialog, QMessageBox, QFrame, QSizePolicy
)
from PyQt6.QtGui import QPixmap, QColor, QFont, QImage, QPainter, QCursor
from PyQt6.QtCore import Qt, QSize

try:
    from PIL import Image as PILImage, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .utils import pil_to_qpixmap

from .dialogs import COLLAGE_FILTER_FNS

class CollageDialog(QDialog):
    """
    Dialog for creating a photo collage from multiple user-supplied images.

    Workflow
    --------
    1. Choose a grid size (2×2 to 5×4).
    2. Click each cell to load an individual image.
    3. Optionally fill all cells at once via multi-file selection.
    4. Per-cell filters can be previewed live via the dropdown.
    5. Cell swap mode: click a filled cell to select it (orange border),
       then click another cell to exchange their contents.
    6. Click 'Collage erstellen' → PIL composites all cells → returned to editor.

    The resulting image replaces the current image in the editor.
    """
    def __init__(self, parent):
        """
        Create the collage editor dialog.

        Parameters
        ----------
        parent : Parent widget (ImageEditor main window).
        """
        super().__init__(parent)
        self.setWindowTitle("🖼  Collage-Editor")
        self.setModal(True)
        self.resize(920, 660)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")

        self.result_image = None
        self._grid_rows   = 3
        self._grid_cols   = 3
        self._cell_images = {}   # (row, col) → PIL Image (always the original)
        self._cell_labels = {}   # (row, col) → QLabel
        self._cell_combos = {}   # (row, col) → QComboBox (per-cell filter)
        self._swap_source = None # (row, col) or None — cell selected for swapping

        self._build_ui()

    def _build_ui(self):
        """Build the collage UI: grid selector at top, scrollable cell grid, buttons at bottom."""
        main = QVBoxLayout(self)
        main.setSpacing(8)

        # ── Top bar: grid size + cell size
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

        # ── Grid area (scrollable)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet("background:#111; border:1px solid #2a2a2a;")
        self._rebuild_grid()
        main.addWidget(self._scroll_area, 1)

        # ── Bottom bar: action buttons
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
        """
        React to a grid-size change: clear all cells and rebuild the grid widget.

        Parameters
        ----------
        text : New grid string such as '3×3'.
        """
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
        """Rebuild the QGridLayout with all cell widgets (image label + filter dropdown)."""
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
                # ── Cell container (image label + filter dropdown)
                cell = QWidget()
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px dashed #333; border-radius:4px;")
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(3, 3, 3, 3)
                cell_layout.setSpacing(3)

                # Image label
                lbl = QLabel("📂\nKlick zum\nLaden")
                lbl.setFixedSize(162, 126)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("color:#555; font-size:11px; border:none;")
                lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                lbl.mousePressEvent = lambda ev, row=r, col=c: self._on_cell_click(row, col)
                cell_layout.addWidget(lbl)

                # Filter dropdown (live preview on change)
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
        """
        Open a file dialog and load an image into the specified grid cell.

        Parameters
        ----------
        row, col : Cell coordinates in the grid.
        """
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
        Update the thumbnail for a cell, applying the selected filter as a live preview.

        Always reads the original from ``_cell_images`` and applies the filter on the fly.
        The original stored in ``_cell_images`` is never modified.

        Parameters
        ----------
        row, col : Cell coordinates in the grid.
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
                    pass  # On error, fall back to the original

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
                # Orange border = selected as the swap source
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px solid #f0a030; border-radius:4px;")
            else:
                cell.setStyleSheet(
                    "background:#1e1e1e; border:2px solid #4fc3f7; border-radius:4px;")

    def _fill_all(self):
        """Load multiple images at once and fill cells sequentially from top-left."""
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

    # ── Live filter preview ───────────────────────────────────────
    def _on_filter_change(self, row: int, col: int):
        """
        Called when the filter dropdown for a cell changes — refreshes the thumbnail.

        Parameters
        ----------
        row, col : Cell coordinates.
        """
        self._refresh_thumb(row, col)

    # ── Cell click: load or swap ──────────────────────────────────
    def _on_cell_click(self, row: int, col: int):
        """
        Handle a click on a grid cell.

        Behaviour
        ---------
        - Empty cell                        → load an image (cancels swap mode if active).
        - Filled cell, no swap active       → mark as swap source (orange border).
        - Filled cell, same cell as source  → cancel the selection.
        - Filled cell, different source     → perform the swap.

        Parameters
        ----------
        row, col : Cell coordinates.
        """
        if (row, col) in self._cell_images:
            if self._swap_source is None:
                # Mark this cell as the swap source
                self._swap_source = (row, col)
                self._refresh_thumb(row, col)   # → orange border
                self.btn_create.setText(
                    "🔄  Tausch-Modus: Ziel-Zelle anklicken  |  ESC = Abbrechen")
            elif self._swap_source == (row, col):
                # Same cell clicked again → cancel selection
                self._swap_source = None
                self._refresh_thumb(row, col)   # → back to blue
                self._update_btn()
            else:
                # Different cell → execute the swap.
                # Reset _swap_source BEFORE the swap so _refresh_thumb draws both cells blue.
                src_saved = self._swap_source
                self._swap_source = None
                self._swap_cells(src_saved, (row, col))
                self._update_btn()
        else:
            # Empty cell: cancel any running swap, then load
            if self._swap_source is not None:
                prev = self._swap_source
                self._swap_source = None
                self._refresh_thumb(*prev)
                self._update_btn()
            self._load_cell(row, col)

    def _swap_cells(self, src: tuple, dst: tuple):
        """
        Swap the image and filter selection of two cells.

        The original PIL images are preserved in ``_cell_images``; only the
        dict entries are exchanged.

        Parameters
        ----------
        src, dst : (row, col) tuples of the two cells to swap.
        """
        img_s = self._cell_images.get(src)
        img_d = self._cell_images.get(dst)

        # Exchange images in _cell_images
        if img_d is not None:
            self._cell_images[src] = img_d
        elif src in self._cell_images:
            del self._cell_images[src]
        if img_s is not None:
            self._cell_images[dst] = img_s
        elif dst in self._cell_images:
            del self._cell_images[dst]

        # Exchange filter dropdown selections
        cb_s = self._cell_combos.get(src)
        cb_d = self._cell_combos.get(dst)
        if cb_s and cb_d:
            idx_s, idx_d = cb_s.currentIndex(), cb_d.currentIndex()
            cb_s.blockSignals(True);  cb_d.blockSignals(True)
            cb_s.setCurrentIndex(idx_d);  cb_d.setCurrentIndex(idx_s)
            cb_s.blockSignals(False); cb_d.blockSignals(False)

        # Refresh both thumbnails
        for key in (src, dst):
            if key in self._cell_images:
                self._refresh_thumb(*key)
            else:
                self._clear_cell(*key)

    def _clear_cell(self, row: int, col: int):
        """
        Reset a cell to its empty initial state.

        Parameters
        ----------
        row, col : Cell coordinates.
        """
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
        """
        ESC cancels swap mode without closing the dialog.

        Parameters
        ----------
        event : Key press event.
        """
        if event.key() == Qt.Key.Key_Escape and self._swap_source is not None:
            prev = self._swap_source
            self._swap_source = None
            self._refresh_thumb(*prev)
            self._update_btn()
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_btn(self):
        """Update the text and enabled state of the 'Collage erstellen' button."""
        filled = len(self._cell_images)
        total  = self._grid_rows * self._grid_cols
        self.btn_create.setEnabled(filled > 0)
        self.btn_create.setText(
            f"▶  Collage erstellen  ({filled}/{total} Zellen)")

    def _create_collage(self):
        """Composite all cells onto a single PIL canvas and store the result."""
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

                # Apply cell filter if one is selected
                combo = self._cell_combos.get((r, c))
                if combo and combo.currentIndex() > 0:
                    filter_fn = COLLAGE_FILTER_FNS.get(combo.currentText())
                    if filter_fn:
                        try:
                            img = filter_fn(img.copy()).convert("RGBA")
                        except Exception:
                            pass  # Keep original on error

                cell_img = img.copy().convert("RGBA")
                if self.btn_equal_size.isChecked():
                    # Crop-Fill: scale image to exact cell size (no letterboxing)
                    cell_img = ImageOps.fit(
                        cell_img, (cell_w, cell_h), PILImage.Resampling.LANCZOS)
                    result.paste(cell_img, (x, y), cell_img)
                else:
                    # Proportional scale + centre within the cell
                    cell_img.thumbnail((cell_w, cell_h), PILImage.Resampling.LANCZOS)
                    ox = (cell_w - cell_img.width)  // 2
                    oy = (cell_h - cell_img.height) // 2
                    result.paste(cell_img, (x + ox, y + oy), cell_img)

        self.result_image = result
        self.accept()


# ══════════════════════════════════════════════════════════════
#  IMAGE CANVAS with zoom and overlay support
# ══════════════════════════════════════════════════════════════

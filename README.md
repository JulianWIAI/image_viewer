# SBS Bildeditor v3

A feature-rich desktop image editor built with **Python** and **PyQt6**, developed as a school portfolio project at SBS Herzogenaurach (March 2026). It combines a clean, layer-based editing workflow with AI-powered image analysis, animated GIF creation, and interactive 2D-to-3D visualisation — all running entirely offline on your local machine.

---

## Screenshots

| Main Editor | Filter Preview |
|:-----------:|:--------------:|
| ![Main Editor](screenshots/01_main.png) | ![Filter Preview](screenshots/02_filter_preview.png) |

| Collage Editor | GIF / Animation Editor |
|:--------------:|:----------------------:|
| ![Collage Editor](screenshots/03_collage.png) | ![GIF Editor](screenshots/04_gif_editor.png) |

| 2D → 3D Viewer |
|:--------------:|
| ![3D Viewer](screenshots/05_3d_viewer.png) |

> Save your five screenshots as `screenshots/01_main.png` through `screenshots/05_3d_viewer.png` in the project root.

---

## Features

### Layer System
- Multiple RGBA layers with individual **opacity** and **visibility** controls
- Add, delete, rename, merge, and flatten layers
- **Layer panel** in the left dock with live thumbnails
- **Transform overlay** — move and scale any layer interactively with mouse + scroll wheel

### Image Adjustments
- Real-time **brightness, contrast, saturation, and sharpness** sliders
- Always applied from the original — no compounding artefacts
- **Reset** restores the original at any time; **Undo** (up to 20 steps) covers all operations

### Crop & Selection Tools
- **Rectangle Crop** — drag a region, then freely reposition and resize it before confirming
- **Lasso Crop** — freehand polygon selection with a movable overlay before applying
- **Magic Wand** — tolerance-based flood-fill colour selection; Shift+click adds regions; cut to a new layer

### Drawing Tools
| Tool | Description |
|---|---|
| ✏️ Pen | Precise freehand pixel drawing |
| 🖌️ Brush | Soft rounded strokes with configurable size |
| ⬜ Eraser | Transparent eraser with smooth edges |
| 💧 Blur Brush | Localised Gaussian softening |
| ╱ Line | Straight lines with variable width |
| ▭ Rectangle | Outlined rectangles |
| ◯ Ellipse | Outlined circles and ellipses |
| T Text | Place text at any position with font selection |
| ～ Curve | Smooth Catmull-Rom spline via control points |
| 🖼️ Texture Brush | Paint with a custom loaded PNG/JPG texture |

### Filter Library (20+ Filters)
- **Colour** — Greyscale, Sepia, Cool, Warm, Purple, Green Cast, Invert
- **Sharpness** — Sharpen, Strong Sharpen, Blur, Strong Blur
- **Effects** — Emboss, Edge Enhance, Auto Contrast, Film Grain, Watercolour, Noise, Vignette
- **Creative** — Comic/Cartoon, Dog Vision, Psychedelic, Night Mode, Kaleidoscope, VHS Flicker, Anaglyph 3D
- **Live Filter Preview** — see all filters applied to your image as thumbnails before choosing

### Shape Library
Pre-defined vector shapes (house, star, arrow, heart, sun, and more) that can be placed and scaled anywhere on the canvas.

### Collage Editor
Arrange multiple images in a configurable grid (up to 4×4), apply per-cell filters, swap cells, and export as a single image.

### GIF & Animation Editor
- **VHS Distortion Loop** — flickering CRT-style animation
- **Star Shower** — procedural particle animation
- **Path Animation** — image travels along a user-drawn Catmull-Rom path
- **Parallax GIF** — depth-layer parallax effect
- Optional audio track trimmed to GIF duration and saved as `.wav`
- Export as animated **GIF** or **MP4 video**

### 2D → 3D Viewer (Beta)
- AI depth estimation via **Depth-Anything-V2** (recommended) or **MiDaS** (fallback)
- Silhouette-aware mesh with edge feathering for a natural rounded look
- Interactive 3D mesh: mouse drag = rotation, scroll = zoom
- Adjustable depth scale, depth inversion, and generated/AI back face
- AI-synthesised back face via **Zero123plus** Novel View Synthesis
- Export as **GLB** (Binary glTF), **OBJ + MTL + texture**, or **STL** for 3D printing (scaled to 100 mm)

### AI Image Analysis (Beta)
Describes image content in natural language using **Moondream** via **Ollama** — runs fully locally, no cloud or API key required.

---

## Project Structure

The project follows a strict **modular architecture** — each class has its own dedicated file inside the `sbs/` package, keeping every component independently readable and maintainable.

```
image_viewer/
├── main.py              # Entry point — initialises Qt app and dark Fusion theme
├── assets/
│   └── app_icon.png     # Application icon
├── screenshots/         # README screenshots (add your own here)
└── sbs/
    ├── __init__.py      # Package exports
    ├── editor.py        # ImageEditor (QMainWindow) — main orchestrator
    ├── layer.py         # Layer — data class for a single image layer
    ├── widgets.py       # ImageCanvas, LayerPanel, HistogramWidget, LabeledSlider
    ├── overlays.py      # CropOverlay, DrawOverlay, TransformOverlay, MagicWandOverlay, …
    ├── dialogs.py       # FilterPreviewDialog, GifEditorDialog
    ├── collage.py       # CollageDialog
    ├── threed.py        # DepthWorker, NovelViewWorker, ThreeDViewerWidget, ThreeDModelDialog
    ├── ai_worker.py     # AIWorker (QThread) — Moondream via Ollama
    └── utils.py         # pil_to_qpixmap, SHAPE_LIBRARY, draw_shape_on_pil
```

---

## Setup & Execution

### Prerequisites

- **Python 3.10** or newer
- **pip**

### 1 — Clone the repository

```bash
git clone https://github.com/JulianWIAI/image-viewer.git
cd image-viewer
```

### 2 — Install core dependencies

```bash
pip install PyQt6 Pillow
```

### 3 — Optional dependencies (enhanced features)

| Feature | Install command |
|---------|-----------------|
| 3D viewer (Matplotlib) | `pip install matplotlib` |
| Depth-Anything-V2 depth estimation *(recommended)* | `pip install transformers torch` |
| MiDaS depth estimation *(fallback)* | `pip install torch timm` |
| AI Novel View Synthesis (3D back face) | `pip install diffusers transformers accelerate torch torchvision` |
| GLB 3D export | `pip install trimesh` |
| MP4 video export | `pip install opencv-python` |

### 4 — Optional: AI image analysis (Moondream)

1. Install [Ollama](https://ollama.com)
2. Pull the model and start the server:

```bash
ollama pull moondream
ollama serve
```

### 5 — Run the application

```bash
python main.py
```

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Open image |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+Z` | Undo |
| `Ctrl+R` | Reset to original |
| `Ctrl+0` | Zoom 1:1 |
| `Ctrl+F` | Zoom to fit |
| `Ctrl++` | Zoom in |
| `Ctrl+-` | Zoom out |
| `Ctrl+Shift+R` | Rectangle crop |
| `Ctrl+Shift+L` | Lasso crop |
| `Ctrl+A` | AI image analysis |

---

## Export Formats

| Format | Use case |
|--------|----------|
| PNG | Lossless with transparency |
| JPG / BMP | Compressed photos |
| GIF | Animated images |
| MP4 | Video with optional audio |
| GLB | 3D model for Blender, web viewers |
| OBJ + MTL | Universal 3D format |
| STL | 3D printing (Cura, PrusaSlicer, Bambu Studio) |

---

## Development Note

This project was developed, polished, and refactored with the assistance of Artificial Intelligence.

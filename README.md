# SBS Bildeditor

A feature-rich desktop image editor built with Python and PyQt6, combining classic image editing tools with modern AI-powered features like depth estimation, novel-view synthesis, and 2D-to-3D model generation.

> Developed as a Bachelor Professional exam project at SBS Herzogenaurach, March 2026.

---

## Features

### Image Editing
- **Adjustments** — Brightness, contrast, saturation, sharpness via real-time sliders
- **Transformations** — Rotate 90°, flip horizontal/vertical
- **Cropping** — Rectangle crop with drag handles; freehand lasso crop
- **Undo** — Single-step undo for all operations
- **Reset** — Restore the original image at any time

### Drawing Tools
| Tool | Description |
|---|---|
| ✏️ Pen | Precise freehand drawing |
| 🖌️ Brush | Soft rounded strokes with variable size |
| ⬜ Eraser | Transparent eraser with smooth edges |
| 💧 Blur Brush | Soft smudge / local Gaussian blur |
| ╱ Line | Straight lines with variable width |
| ▭ Rectangle | Outlined rectangles |
| ◯ Ellipse | Outlined circles and ellipses |
| T Text | Insert text with font selection |
| ～ Curve | Smooth cubic Bézier curves |
| 🖼️ Texture Brush | Paint with a custom loaded texture |

### Selection Tools
- **Magic Wand** — Flood-fill selection by colour similarity with tolerance control
- **Rectangle Crop** — Drag-resizable selection area
- **Lasso Crop** — Freehand polygon selection

### Layer System
- Multiple RGBA layers with individual opacity controls
- Toggle layer visibility
- Add, delete, rename, and reorder layers
- Layer panel in the left dock

### Filter Library (20+ Filters)
**Colour** — Grayscale, Sepia, Cool, Warm, Invert, Psychedelic, Dog Vision, Night Mode
**Sharpness** — Sharpen, Strong Sharpen, Blur, Strong Blur
**Effects** — Emboss, Edge Enhance, Auto Contrast, Film Grain, Watercolor, Noise, Vignette
**Creative** — Comic/Cartoon, VHS Flicker, Anaglyph 3D, Kaleidoscope

A **Filter Preview Dialog** (Ctrl+Shift+V) shows all filters as thumbnails at once for quick comparison.

### Shape Library
Predefined vector shapes (heart, star, sun, airplane, and more) that can be placed and scaled on the canvas via the Shape Placer tool.

### Collage Editor
Arrange multiple images in a configurable grid layout and export the result as a single image.

### GIF & Video Editor
- Build multi-frame animations from images
- Control per-frame delay
- Export as animated **GIF** or **MP4 video**
- Optional audio track support (WAV, with auto-resampling and looping)

### AI Features
| Feature | Model | Description |
|---|---|---|
| Image Analysis | Moondream | Generates a natural-language description of the image |
| Depth Estimation | Depth-Anything-V2 / MiDaS | Creates a depth map from a single 2D image |
| Novel View Synthesis | zero123plus-v1.1 | Generates a synthetic back side from a front-facing photo |

### 2D → 3D Model Generator *(Beta)*
Converts any image into a 3D mesh using AI depth estimation:
- Interactive 3D viewer (Matplotlib)
- Depth scale and inversion controls
- AI-generated backside texture with edge dilation for clean seams
- **Export as GLB** (Binary glTF, requires `trimesh`)
- **Export as OBJ** (Wavefront + MTL + texture PNG, pure Python fallback)
- **Export as STL** for 3D printing — auto-scaled to 100 mm longest dimension, compatible with Cura, PrusaSlicer, and Bambu Studio

---

## Requirements

### Required
```
Python 3.10+
PyQt6
Pillow
```

### Recommended (for full functionality)
```
matplotlib          # 3D viewer
trimesh             # GLB export
opencv-python       # Texture dilation (cv2)
scipy               # Depth maps, mask processing
```

### Optional (AI features)
```
torch               # Deep learning runtime
transformers        # Depth-Anything-V2 / Moondream
diffusers           # zero123plus novel-view synthesis
accelerate          # Faster model inference
ollama              # Local Moondream API
```

Install all at once:
```bash
pip install PyQt6 Pillow matplotlib trimesh opencv-python scipy torch transformers diffusers accelerate
```

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-username/image_viewer.git
cd image_viewer

# 2. Install dependencies
pip install PyQt6 Pillow matplotlib trimesh opencv-python scipy

# 3. Run the application
python image_viewer.py
```

---

## Usage

```bash
python image_viewer.py
```

Open an image via **File → Open** or `Ctrl+O`, then use the toolbar, right-panel sliders, and menus to edit.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Open file |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+Z` | Undo |
| `Ctrl+R` | Reset to original |
| `Ctrl+F` | Zoom to fit |
| `Ctrl+0` | Zoom 1:1 |
| `Ctrl++` | Zoom in |
| `Ctrl+-` | Zoom out |
| `Ctrl+Shift+R` | Rectangle crop |
| `Ctrl+Shift+L` | Lasso crop |
| `Ctrl+Shift+V` | Filter preview |
| `Ctrl+Shift+C` | Collage editor |
| `Ctrl+Shift+G` | GIF / video editor |
| `Ctrl+Shift+3` | 3D model generator |
| `Ctrl+Shift+P` | Pen tool |
| `Ctrl+Shift+B` | Brush tool |
| `Ctrl+Shift+E` | Eraser tool |
| `Ctrl+Shift+W` | Magic wand |
| `Ctrl+Shift+F` | Shape placer |
| `Ctrl+A` | AI image analysis |

---

## Project Structure

```
image_viewer/
├── image_viewer.py   # Entire application (single-file)
├── app_icon.png      # Application icon
└── README.md         # This file
```

---

## Export Formats

| Format | Use case |
|---|---|
| PNG | Lossless with transparency |
| JPG | Compressed photos |
| GIF | Animated images |
| MP4 | Video with optional audio |
| GLB | 3D model for Blender, web viewers |
| OBJ | 3D model, universal fallback |
| STL | 3D printing (Cura, PrusaSlicer, Bambu) |

---

## License

This project was created for educational purposes.

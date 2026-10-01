"""Build album PDF from processed images."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image


def build_album_pdf(
    image_paths: list[Path],
    out_path: Path,
    *,
    title: str,
    layout: str = "page",
    grid_cols: int = 2,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = letter
    margin = 0.5 * inch
    c = canvas.Canvas(str(out_path), pagesize=letter)

    # Cover
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin, page_h - margin - 24, title[:80] or "Album Press")
    c.setFont("Helvetica", 11)
    c.drawString(margin, page_h - margin - 48, f"{len(image_paths)} photos")
    c.showPage()

    if layout == "grid":
        cols = max(1, min(grid_cols, 4))
        rows = cols
        cell_w = (page_w - 2 * margin) / cols
        cell_h = (page_h - 2 * margin) / rows
        for i, path in enumerate(image_paths):
            if i > 0 and i % (cols * rows) == 0:
                c.showPage()
            slot = i % (cols * rows)
            col = slot % cols
            row = slot // cols
            x = margin + col * cell_w
            y = page_h - margin - (row + 1) * cell_h
            _draw_fitted(c, path, x + 4, y + 4, cell_w - 8, cell_h - 8)
        c.showPage()
    else:
        usable_w = page_w - 2 * margin
        usable_h = page_h - 2 * margin - 18
        for path in image_paths:
            _draw_fitted(c, path, margin, margin + 14, usable_w, usable_h)
            c.setFont("Helvetica", 8)
            c.drawString(margin, margin, path.stem[:90])
            c.showPage()

    c.save()
    return out_path


def _draw_fitted(c: canvas.Canvas, path: Path, x: float, y: float, max_w: float, max_h: float) -> None:
    with Image.open(path) as im:
        iw, ih = im.size
    if iw <= 0 or ih <= 0:
        return
    scale = min(max_w / iw, max_h / ih)
    dw, dh = iw * scale, ih * scale
    ox = x + (max_w - dw) / 2
    oy = y + (max_h - dh) / 2
    c.drawImage(
        ImageReader(str(path)),
        ox,
        oy,
        width=dw,
        height=dh,
        preserveAspectRatio=True,
        mask="auto",
    )

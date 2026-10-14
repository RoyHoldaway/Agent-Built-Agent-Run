"""Turn whatever the user hands us (MS Paint BMP/PNG, phone photo of paper, screenshot) into a clean data URL."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
MAX_SIDE = 1568  # larger images get downscaled by most VL models anyway; this keeps tokens/VRAM sane


def prepare(path: Path, max_side: int = MAX_SIDE) -> str:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # phone photos arrive rotated
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))  # transparent PNG -> white page
        bg.alpha_composite(img)
        img = bg
    img = img.convert("RGB")

    photo_of_paper = path.suffix.lower() in {".jpg", ".jpeg"}
    if photo_of_paper:
        img = ImageOps.autocontrast(img, cutoff=1)  # lift grey paper toward white, pencil toward black

    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    if photo_of_paper:
        img.save(buf, "JPEG", quality=88)
        mime = "image/jpeg"
    else:
        img.save(buf, "PNG", optimize=True)
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def find_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)

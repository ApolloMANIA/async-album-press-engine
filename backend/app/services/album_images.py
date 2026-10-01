"""Discover and process album images with Pillow."""

from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


class AlbumError(ValueError):
    pass


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith(".")


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                if not name or name.startswith("."):
                    continue
                # Zip slip guard
                target = (dest / name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    continue
                with zf.open(info) as src, target.open("wb") as out:
                    out.write(src.read())
    except zipfile.BadZipFile as exc:
        raise AlbumError("Upload is not a valid ZIP file") from exc


def discover_images(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files = [p for p in root.rglob("*") if p.is_file() and is_image_path(p)]
    files.sort(key=lambda p: p.name.lower())
    return files


def process_image(
    src: Path,
    dest: Path,
    *,
    max_width: int = 1600,
    quality: int = 85,
    strip_exif: bool = True,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            elif im.mode == "L":
                im = im.convert("RGB")

            if max_width > 0 and im.width > max_width:
                ratio = max_width / float(im.width)
                new_size = (max_width, max(1, int(im.height * ratio)))
                im = im.resize(new_size, Image.Resampling.LANCZOS)

            save_kwargs: dict = {"quality": max(40, min(quality, 95)), "optimize": True}
            if not strip_exif and "exif" in im.info:
                save_kwargs["exif"] = im.info["exif"]

            out = dest.with_suffix(".jpg")
            im.save(out, format="JPEG", **save_kwargs)
            return out
    except UnidentifiedImageError as exc:
        raise AlbumError(f"Cannot read image: {src.name}") from exc
    except OSError as exc:
        raise AlbumError(f"Failed processing {src.name}: {exc}") from exc

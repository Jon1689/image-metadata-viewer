from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from PIL import Image, ExifTags
import exifread


@dataclass
class FileInfo:
    path: str
    filename: str
    size_bytes: int
    sha256: str
    md5: str


def _hash_file(path: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_file_info(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        filename=os.path.basename(path),
        size_bytes=os.path.getsize(path),
        sha256=_hash_file(path, "sha256"),
        md5=_hash_file(path, "md5"),
    )


def _pillow_exif(path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return out
            for tag_id, value in exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                out[tag_name] = value
    except Exception:
        return out
    return out


def _exifread_exif(path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        for k, v in tags.items():
            out[str(k)] = str(v)
    except Exception:
        return out
    return out


def _extract_gps(merged: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    gps: Dict[str, Any] = {}
    if "GPSInfo" in merged and isinstance(merged["GPSInfo"], dict):
        for k, v in merged["GPSInfo"].items():
            gps[str(k)] = v

    for key in list(merged.keys()):
        if key.startswith("GPS "):
            gps[key] = merged[key]

    return gps or None


def extract_metadata(path: str) -> Dict[str, Any]:
    file_info = get_file_info(path)
    pillow_exif = _pillow_exif(path)
    exifread_exif = _exifread_exif(path)

    merged = dict(pillow_exif)
    for k, v in exifread_exif.items():
        merged.setdefault(k, v)

    result = {
        "file": {
            "path": file_info.path,
            "filename": file_info.filename,
            "size_bytes": file_info.size_bytes,
            "md5": file_info.md5,
            "sha256": file_info.sha256,
        },
        "exif_pillow": pillow_exif,
        "exif_exifread": exifread_exif,
        "gps": _extract_gps(merged),
    }

    return make_json_safe(result)

def make_json_safe(obj: Any) -> Any:
    """
    Recursively convert EXIF / PIL types into JSON-serializable Python types.
    - IFDRational -> float (or str fallback)
    - bytes -> hex string
    - tuples/sets -> lists
    - dict -> dict with string keys
    """
    # Basic JSON types
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Pillow rationals and similar (they often behave like numbers)
    try:
        # IFDRational supports float() in many cases
        if obj.__class__.__name__ == "IFDRational":
            return float(obj)
    except Exception:
        pass

    # Bytes (sometimes EXIF fields are raw bytes)
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()

    # Lists / tuples / sets
    if isinstance(obj, (list, tuple, set)):
        return [make_json_safe(x) for x in obj]

    # Dicts
    if isinstance(obj, dict):
        return {str(k): make_json_safe(v) for k, v in obj.items()}

    # Fallback: stringify anything unknown
    return str(obj)

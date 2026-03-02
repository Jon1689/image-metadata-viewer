from __future__ import annotations
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from PIL import Image, ExifTags
import exifread
import re
from fractions import Fraction
from urllib.parse import quote_plus

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
        "gps_decimal": extract_gps_decimal(merged),
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

def _to_float(x: str) -> float:
    x = x.strip()
    if "/" in x:
        return float(Fraction(x))
    return float(x)


def _parse_dms(value: Any) -> Optional[tuple[float, float, float]]:
    """
    Accepts DMS in many forms:
      - list/tuple like [deg, min, sec]
      - string like '[42, 30, 123/10]' or '42/1, 30/1, 1234/100'
    Returns (deg, min, sec) as floats.
    """
    if value is None:
        return None

    # Already a list/tuple of numbers/strings
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            deg = float(value[0])
            mins = float(value[1])
            sec = float(value[2])
            return (deg, mins, sec)
        except Exception:
            # fall through to string parsing
            pass

    s = str(value)

    # Pull out numbers and fractions like 123/10
    parts = re.findall(r"-?\d+(?:\.\d+)?(?:/\d+)?", s)
    if len(parts) < 3:
        return None

    try:
        deg = _to_float(parts[0])
        mins = _to_float(parts[1])
        sec = _to_float(parts[2])
        return (deg, mins, sec)
    except Exception:
        return None


def _dms_to_decimal(dms: tuple[float, float, float], ref: str) -> float:
    deg, mins, sec = dms
    dec = abs(deg) + (mins / 60.0) + (sec / 3600.0)
    if ref.upper() in ("S", "W"):
        dec = -dec
    return dec


def extract_gps_decimal(merged_exif: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Tries to derive decimal latitude/longitude from EXIF.
    Supports exifread-style keys:
      GPS GPSLatitude, GPS GPSLatitudeRef, GPS GPSLongitude, GPS GPSLongitudeRef
    """
    lat_val = merged_exif.get("GPS GPSLatitude")
    lat_ref = merged_exif.get("GPS GPSLatitudeRef")
    lon_val = merged_exif.get("GPS GPSLongitude")
    lon_ref = merged_exif.get("GPS GPSLongitudeRef")

    if not (lat_val and lat_ref and lon_val and lon_ref):
        return None

    lat_dms = _parse_dms(lat_val)
    lon_dms = _parse_dms(lon_val)
    if not lat_dms or not lon_dms:
        return None

    lat = _dms_to_decimal(lat_dms, str(lat_ref))
    lon = _dms_to_decimal(lon_dms, str(lon_ref))

    # Maps link (Google Maps query)
    maps_url = f"https://www.google.com/maps?q={quote_plus(f'{lat},{lon}')}"
    return {"latitude": lat, "longitude": lon, "maps_url": maps_url}
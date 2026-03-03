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
import piexif

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

def analyze_privacy_risks(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze extracted metadata and return:
      {
        "level": "LOW|MEDIUM|HIGH",
        "score": int,
        "findings": [{"id": "...", "severity": "...", "message": "..."}],
        "recommendations": [ ... ]
      }
    """
    findings = []
    score = 0

    def add(fid: str, severity: str, message: str, points: int):
        nonlocal score
        findings.append({"id": fid, "severity": severity, "message": message})
        score += points

    # Pull useful sections
    exif_p = metadata.get("exif_pillow") or {}
    exif_e = metadata.get("exif_exifread") or {}
    gps_dec = metadata.get("gps_decimal") or {}
    gps_raw = metadata.get("gps") or {}

    # Merge EXIF for easier searching
    merged = dict(exif_p)
    for k, v in exif_e.items():
        merged.setdefault(k, v)

    # 1) GPS location (highest risk)
    if gps_dec.get("latitude") is not None and gps_dec.get("longitude") is not None:
        add("gps_coords", "HIGH", "GPS coordinates are present (precise location).", 50)
    elif gps_raw:
        add("gps_present", "HIGH", "GPS metadata is present (location-related fields detected).", 45)

    # 2) Device identifiers (often overlooked)
    device_keys = [
        "BodySerialNumber", "SerialNumber", "CameraSerialNumber",
        "LensSerialNumber", "InternalSerialNumber",
        "EXIF BodySerialNumber", "EXIF SerialNumber",
        "EXIF LensSerialNumber", "MakerNote",
    ]
    for k in device_keys:
        if k in merged and str(merged.get(k)).strip():
            add("device_id", "HIGH", f"Device identifier present: {k}.", 35)
            break

    # 3) Owner / author / copyright / contact
    owner_keys = [
        "Artist", "XPAuthor", "Copyright", "OwnerName",
        "ImageDescription", "UserComment",
        "IPTC Contact", "IPTC Creator", "IPTC Copyright",
    ]
    for k in owner_keys:
        if k in merged and str(merged.get(k)).strip():
            add("identity", "MEDIUM", f"Identity/attribution field present: {k}.", 20)
            break

    # 4) Timestamps (can reveal routine/location over time)
    time_keys = [
        "DateTimeOriginal", "DateTimeDigitized", "DateTime",
        "EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime",
    ]
    for k in time_keys:
        if k in merged and str(merged.get(k)).strip():
            add("timestamps", "MEDIUM", f"Capture timestamp present: {k}.", 15)
            break

    # 5) Software tag (signals editing pipeline; not always bad)
    software_keys = ["Software", "ProcessingSoftware", "EXIF Software", "Image Software"]
    for k in software_keys:
        if k in merged and str(merged.get(k)).strip():
            add("software", "LOW", f"Software tag present: {k} (may indicate editing/export pipeline).", 5)
            break

    # Decide level
    if score >= 50:
        level = "HIGH"
    elif score >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"

    # Recommendations
    recs = []
    if any(f["id"].startswith("gps") for f in findings):
        recs.append("Remove GPS metadata before sharing publicly.")
    if any(f["id"] == "device_id" for f in findings):
        recs.append("Remove device serial identifiers to reduce traceability.")
    if any(f["id"] == "timestamps" for f in findings):
        recs.append("Consider removing capture timestamps if they reveal patterns or routines.")
    if any(f["id"] == "identity" for f in findings):
        recs.append("Remove author/owner fields if you don’t want attribution or identity leakage.")
    if not recs:
        recs.append("No major privacy risks detected; share with normal caution.")

    return {
        "level": level,
        "score": score,
        "findings": findings,
        "recommendations": recs,
    }

def sanitize_image(
    input_path: str,
    output_path: str,
    *,
    remove_gps: bool = False,
    remove_timestamps: bool = False,
    remove_device_ids: bool = False,
    remove_identity: bool = False,
    remove_all: bool = False,
    keep_orientation: bool = True,
) -> Dict[str, Any]:
    """
    Save a sanitized copy of the image.

    Best support: JPEG/TIFF EXIF (via piexif).
    For other formats: re-save pixel data to strip typical metadata containers.

    Returns a small report dict describing what was removed.
    """
    report = {
        "input": input_path,
        "output": output_path,
        "applied": {
            "remove_all": remove_all,
            "remove_gps": remove_gps,
            "remove_timestamps": remove_timestamps,
            "remove_device_ids": remove_device_ids,
            "remove_identity": remove_identity,
            "keep_orientation": keep_orientation,
        },
        "notes": [],
    }

    # If remove_all is chosen, treat it as "strip everything" (but optionally keep orientation)
    if remove_all:
        remove_gps = remove_timestamps = remove_device_ids = remove_identity = True

    from PIL import Image  # local import to avoid circular import headaches
    with Image.open(input_path) as img:
        fmt = (img.format or "").upper()

        # Try EXIF-aware sanitization for JPEG/TIFF
        if fmt in ("JPEG", "JPG", "TIFF"):
            exif_bytes = img.info.get("exif", b"")
            if not exif_bytes:
                # No EXIF present; still re-save to ensure clean container
                img.save(output_path, format=fmt)
                report["notes"].append("No EXIF found; re-saved image container.")
                return report

            exif_dict = piexif.load(exif_bytes)

            # Keep orientation if requested
            orientation_value = None
            if keep_orientation:
                orientation_value = exif_dict.get("0th", {}).get(piexif.ImageIFD.Orientation)

            # Remove GPS IFD entirely
            if remove_gps and "GPS" in exif_dict:
                exif_dict["GPS"] = {}

            # Remove timestamps
            if remove_timestamps:
                # 0th IFD
                exif_dict.get("0th", {}).pop(piexif.ImageIFD.DateTime, None)
                # Exif IFD
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.DateTimeOriginal, None)
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.DateTimeDigitized, None)
                # Sometimes SubSecTime tags exist too
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.SubSecTime, None)
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.SubSecTimeOriginal, None)
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.SubSecTimeDigitized, None)

            # Remove device identifiers (best-effort)
            if remove_device_ids:
                for ifd_name in ("0th", "Exif", "1st"):
                    ifd = exif_dict.get(ifd_name, {})
                    # These tags vary by maker; we remove common ones where defined
                    ifd.pop(getattr(piexif.ImageIFD, "BodySerialNumber", 0), None)
                    ifd.pop(getattr(piexif.ExifIFD, "BodySerialNumber", 0), None)
                    ifd.pop(getattr(piexif.ExifIFD, "LensSerialNumber", 0), None)

                # MakerNote often contains device-specific data
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.MakerNote, None)

            # Remove identity fields
            if remove_identity:
                exif_dict.get("0th", {}).pop(piexif.ImageIFD.Artist, None)
                exif_dict.get("0th", {}).pop(piexif.ImageIFD.Copyright, None)
                exif_dict.get("0th", {}).pop(piexif.ImageIFD.ImageDescription, None)
                # UserComment sometimes holds personal info
                exif_dict.get("Exif", {}).pop(piexif.ExifIFD.UserComment, None)

            # If "remove_all": wipe almost everything but optionally restore orientation
            if remove_all:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                if keep_orientation and orientation_value is not None:
                    exif_dict["0th"][piexif.ImageIFD.Orientation] = orientation_value

            # Dump new EXIF
            new_exif = piexif.dump(exif_dict)

            # Save sanitized copy; this preserves pixel data + strips selected metadata
            img.save(output_path, format=fmt, exif=new_exif)
            report["notes"].append("Sanitized EXIF using piexif for JPEG/TIFF.")
            return report

        # Fallback for PNG/WebP/BMP/etc:
        # Re-save pixel data without passing metadata containers.
        img = img.copy()
        img.save(output_path, format=fmt if fmt else None)
        report["notes"].append(f"Re-saved image without EXIF-aware editing (format={fmt or 'unknown'}).")
        return report

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
        "privacy": analyze_privacy_risks({
            "exif_pillow": pillow_exif,
            "exif_exifread": exifread_exif,
            "gps": _extract_gps(merged),
            "gps_decimal": extract_gps_decimal(merged) if "extract_gps_decimal" in globals() else None,
        }),
    }

    result["privacy"] = analyze_privacy_risks(result)
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
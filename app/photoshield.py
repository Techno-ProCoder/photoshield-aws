"""Finds and removes hidden metadata from photos.

Pure image processing with no AWS dependencies, so it can be unit-tested locally.
"""
import io
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

# Larger images are refused before decoding: a small compressed file can expand to
# gigabytes of pixels (a "decompression bomb") and exhaust a t3.micro's memory.
MAX_PIXELS = 40_000_000

ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "TIFF"}

# EXIF tag numbers
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_SOFTWARE = 0x0131
TAG_DATETIME = 0x0132
TAG_DATETIME_ORIGINAL = 0x9003
TAG_BODY_SERIAL = 0xA431
TAG_LENS_MODEL = 0xA434
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON, GPS_ALT_REF, GPS_ALT = 1, 2, 3, 4, 5, 6
TIFF_XMP_TAG = 700

# XMP stores GPS as text, e.g. exif:GPSLatitude="30,21.24N" or <exif:GPSLatitude>30,21,14.4N</...>
_XMP_COORD = r'exif:{name}(?:\s*=\s*["\']|>)\s*([0-9.]+(?:,[0-9.]+){{0,2}})\s*([NSEW])'
XMP_LAT = re.compile(_XMP_COORD.format(name="GPSLatitude"))
XMP_LON = re.compile(_XMP_COORD.format(name="GPSLongitude"))


class InvalidImage(ValueError):
    """The upload is not an image this service can process."""


@dataclass
class Findings:
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    device: str | None = None
    lens: str | None = None
    serial: str | None = None
    software: str | None = None
    taken_at: str | None = None
    location_source: str | None = None  # "EXIF" or "XMP"
    other: list[str] = field(default_factory=list)
    removed_names: list[str] = field(default_factory=list)

    @property
    def fields_removed(self):
        return len(self.removed_names)

    @property
    def has_location(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def has_device(self):
        return bool(self.device or self.lens or self.serial)

    @property
    def has_timestamp(self):
        return self.taken_at is not None

    @property
    def found_anything(self):
        return self.fields_removed > 0 or bool(self.other)

    @property
    def risk(self):
        """high: reveals a location; medium: identifies a device or time; low: nothing personal."""
        if self.has_location:
            return "high"
        if self.has_device or self.has_timestamp:
            return "medium"
        return "low"


@dataclass
class Result:
    findings: Findings
    cleaned: bytes
    width: int
    height: int


def _text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if value is None:
        return None
    value = str(value).replace("\x00", "").strip()
    return value or None


def _ref(value):
    return (_text(value) or "").upper()[:1]


def _dms_to_decimal(dms, ref, limit):
    try:
        degrees, minutes, seconds = (float(part) for part in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    if not math.isfinite(value) or value > limit:
        return None
    return round(-value if ref in ("S", "W") else value, 6)


def _xmp_text(img):
    raw = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp")
    if raw is None and hasattr(img, "tag_v2"):
        raw = img.tag_v2.get(TIFF_XMP_TAG)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return raw or ""


def _xmp_coordinate(pattern, xmp, limit):
    """Parse XMP 'DD,MM.mmK' or 'DD,MM,SSK' (K = N/S/E/W) into signed decimal degrees."""
    match = pattern.search(xmp)
    if not match:
        return None
    parts = [float(p) for p in match.group(1).split(",")]
    parts += [0.0] * (3 - len(parts))
    return _dms_to_decimal(parts, match.group(2), limit)


def _tag_name(tag, table):
    return table.get(tag, f"Tag 0x{tag:04X}")


def analyse(img):
    """Report the privacy-relevant metadata embedded in an image."""
    findings = Findings()
    exif = img.getexif()
    gps = exif.get_ifd(GPS_IFD)
    sub = exif.get_ifd(EXIF_IFD)
    pointer_tags = {EXIF_IFD, GPS_IFD}
    findings.removed_names = (
        [_tag_name(t, ExifTags.TAGS) for t in exif if t not in pointer_tags]
        + [_tag_name(t, ExifTags.TAGS) for t in sub]
        + [_tag_name(t, ExifTags.GPSTAGS) for t in gps]
    )

    if GPS_LAT in gps and GPS_LON in gps:
        lat = _dms_to_decimal(gps[GPS_LAT], _ref(gps.get(GPS_LAT_REF)), 90)
        lon = _dms_to_decimal(gps[GPS_LON], _ref(gps.get(GPS_LON_REF)), 180)
        if lat is not None and lon is not None:
            findings.latitude, findings.longitude = lat, lon
            findings.location_source = "EXIF"

    xmp = _xmp_text(img)
    if xmp and not findings.has_location:
        # Editors such as Lightroom and Photoshop can keep the location only in XMP.
        lat = _xmp_coordinate(XMP_LAT, xmp, 90)
        lon = _xmp_coordinate(XMP_LON, xmp, 180)
        if lat is not None and lon is not None:
            findings.latitude, findings.longitude = lat, lon
            findings.location_source = "XMP"
    if GPS_ALT in gps:
        try:
            altitude = float(gps[GPS_ALT])
            if math.isfinite(altitude):
                below_sea = gps.get(GPS_ALT_REF) in (1, b"\x01")
                findings.altitude_m = round(-altitude if below_sea else altitude, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    make, model = _text(exif.get(TAG_MAKE)), _text(exif.get(TAG_MODEL))
    # Avoid "NIKON CORPORATION NIKON Z 7" or "Canon Canon EOS R5": drop the make
    # when the model already starts with the brand's first word.
    if make and model and model.split()[0].lower() == make.split()[0].lower():
        make = None
    findings.device = " ".join(part for part in (make, model) if part) or None
    findings.lens = _text(sub.get(TAG_LENS_MODEL))
    findings.serial = _text(sub.get(TAG_BODY_SERIAL))
    findings.software = _text(exif.get(TAG_SOFTWARE))
    findings.taken_at = _text(sub.get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME))

    if xmp:
        findings.other.append("XMP metadata block (can repeat location and editing history)")
    if img.info.get("comment"):
        findings.other.append("Embedded comment")
    text_chunks = getattr(img, "text", None) or {}
    text_chunks = {k: v for k, v in text_chunks.items() if k != "XML:com.adobe.xmp"}
    if text_chunks:
        findings.other.append(f"{len(text_chunks)} text field(s): {', '.join(sorted(text_chunks))[:80]}")
    return findings


def clean(img):
    """Return JPEG bytes containing only the pixels: no EXIF, XMP, comments or text fields."""
    # Apply the EXIF rotation to the pixels first, since the orientation tag is about to be removed.
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel("A"))
        img = background
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # Rebuild the image from raw pixel data so no metadata can be carried over.
    pixels_only = Image.frombytes(img.mode, img.size, img.tobytes())
    out = io.BytesIO()
    pixels_only.save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out.getvalue(), pixels_only.size


def process(data):
    """Analyse and clean an uploaded image. Raises InvalidImage for anything unusable."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in ALLOWED_FORMATS:
                raise InvalidImage("Unsupported format. Use JPEG, PNG, WebP or TIFF.")
            if img.width * img.height > MAX_PIXELS:
                raise InvalidImage("Image is too large (over 40 megapixels).")
            img.load()
            findings = analyse(img)
            cleaned, (width, height) = clean(img)
    except InvalidImage:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImage("Image is too large.") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise InvalidImage("That file is not a readable image.") from None
    return Result(findings, cleaned, width, height)


@lru_cache(maxsize=1)
def sample_photo():
    """A 6 MP test photo tagged like a phone camera shot on the TIET campus, Patiala."""
    width, height = 3000, 2000
    red = Image.linear_gradient("L").resize((width, height))
    green = Image.effect_noise((width, height), 40)  # noise makes encoding as costly as a real photo
    blue = Image.radial_gradient("L").resize((width, height))
    img = Image.merge("RGB", (red, green, blue))

    exif = Image.Exif()
    exif[TAG_MAKE] = "Google"
    exif[TAG_MODEL] = "Pixel 8"
    exif[TAG_SOFTWARE] = "HDR+ 1.0.612345"
    exif[TAG_DATETIME] = "2026:11:18 08:41:07"
    exif.get_ifd(EXIF_IFD).update({
        TAG_DATETIME_ORIGINAL: "2026:11:18 08:41:07",
        TAG_LENS_MODEL: "Pixel 8 back camera 6.9mm f/1.68",
    })
    exif.get_ifd(GPS_IFD).update({
        GPS_LAT_REF: "N", GPS_LAT: (30.0, 21.0, 14.4),
        GPS_LON_REF: "E", GPS_LON: (76.0, 22.0, 12.0),
        GPS_ALT: 251.0,
    })
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92, exif=exif)
    return out.getvalue()

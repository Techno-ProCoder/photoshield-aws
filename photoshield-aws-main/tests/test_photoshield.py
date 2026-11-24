import io

import pytest
from PIL import Image, PngImagePlugin

import photoshield


def reopen(data):
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_sample_photo_reveals_location_device_and_time():
    result = photoshield.process(photoshield.sample_photo())
    f = result.findings
    assert f.latitude == pytest.approx(30.354, abs=1e-4)
    assert f.longitude == pytest.approx(76.37, abs=1e-4)
    assert f.altitude_m == 251.0
    assert f.device == "Google Pixel 8"
    assert f.taken_at == "2026:11:18 08:41:07"
    assert f.has_location and f.has_device and f.has_timestamp
    assert f.fields_removed >= 8


def test_removed_field_names_and_risk():
    f = photoshield.process(photoshield.sample_photo()).findings
    assert {"Make", "Model", "GPSLatitude", "GPSLongitude", "DateTimeOriginal"} <= set(f.removed_names)
    assert f.fields_removed == len(f.removed_names)
    assert f.risk == "high" and f.location_source == "EXIF"


@pytest.mark.parametrize("make, model, expected", [
    ("NIKON CORPORATION", "NIKON Z 7", "NIKON Z 7"),
    ("Canon", "Canon EOS R5", "Canon EOS R5"),
    ("SONY", "ILCE-7M3", "SONY ILCE-7M3"),
    ("DJI", "FC6310", "DJI FC6310"),
])
def test_device_name_without_repeated_brand(make, model, expected):
    img = Image.new("RGB", (20, 20))
    exif = Image.Exif()
    exif[photoshield.TAG_MAKE], exif[photoshield.TAG_MODEL] = make, model
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    f = photoshield.process(buf.getvalue()).findings
    assert f.device == expected
    assert f.risk == "medium"


@pytest.mark.parametrize("xmp, lat, lon", [
    ('<rdf:Description exif:GPSLatitude="30,21.24N" exif:GPSLongitude="76,22.2E"/>', 30.354, 76.37),
    ("<exif:GPSLatitude>35,0,36.0N</exif:GPSLatitude><exif:GPSLongitude>135,46,12.0E</exif:GPSLongitude>",
     35.01, 135.77),
    ('<x exif:GPSLatitude="33,51.4167S" exif:GPSLongitude="151,12.9W"/>', -33.856945, -151.215),
])
def test_location_only_in_xmp(xmp, lat, lon):
    packet = f'<x:xmpmeta xmlns:x="adobe:ns:meta/">{xmp}</x:xmpmeta>'.encode()
    buf = io.BytesIO()
    Image.new("RGB", (30, 30)).save(buf, "JPEG", xmp=packet)
    f = photoshield.process(buf.getvalue()).findings
    assert f.latitude == pytest.approx(lat, abs=1e-4)
    assert f.longitude == pytest.approx(lon, abs=1e-4)
    assert f.location_source == "XMP" and f.risk == "high"
    assert "xmp" not in Image.open(io.BytesIO(photoshield.process(buf.getvalue()).cleaned)).info


def test_cleaned_output_has_no_metadata_and_keeps_size():
    result = photoshield.process(photoshield.sample_photo())
    cleaned = reopen(result.cleaned)
    assert cleaned.format == "JPEG"
    assert cleaned.size == (3000, 2000) == (result.width, result.height)
    assert len(cleaned.getexif()) == 0
    assert "exif" not in cleaned.info
    again = photoshield.process(result.cleaned).findings
    assert not again.found_anything


def test_southern_western_coordinates_are_negative():
    img = Image.new("RGB", (40, 30), "white")
    exif = Image.Exif()
    exif.get_ifd(photoshield.GPS_IFD).update({1: "S", 2: (33.0, 52.0, 0.0), 3: "W", 4: (151.0, 12.0, 36.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    f = photoshield.process(buf.getvalue()).findings
    assert f.latitude == pytest.approx(-33.866667, abs=1e-5)
    assert f.longitude == pytest.approx(-151.21, abs=1e-5)


def test_rotation_is_applied_before_the_orientation_tag_is_removed():
    img = Image.new("RGB", (200, 100), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise when displayed
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    result = photoshield.process(buf.getvalue())
    assert reopen(result.cleaned).size == (100, 200)


def test_png_with_transparency_and_text_chunks():
    img = Image.new("RGBA", (60, 40), (255, 0, 0, 128))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Jane Doe")
    info.add_text("Location", "Hostel J, room 214")
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info)
    result = photoshield.process(buf.getvalue())
    assert any("2 text field(s)" in item for item in result.findings.other)
    cleaned = reopen(result.cleaned)
    assert cleaned.mode == "RGB" and not getattr(cleaned, "text", {})


def test_photo_without_metadata():
    img = Image.new("RGB", (50, 50), "blue")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    f = photoshield.process(buf.getvalue()).findings
    assert not f.found_anything and not f.has_location


@pytest.mark.parametrize("data", [b"", b"not an image at all", b"\xff\xd8\xff\xe0broken jpeg"])
def test_rejects_non_images(data):
    with pytest.raises(photoshield.InvalidImage):
        photoshield.process(data)


def test_rejects_unsupported_format():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "GIF")
    with pytest.raises(photoshield.InvalidImage, match="Unsupported"):
        photoshield.process(buf.getvalue())


def test_rejects_oversized_images():
    buf = io.BytesIO()
    Image.new("L", (8000, 6000)).save(buf, "PNG")  # 48 MP
    with pytest.raises(photoshield.InvalidImage, match="too large"):
        photoshield.process(buf.getvalue())

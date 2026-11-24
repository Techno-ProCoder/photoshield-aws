"""Route tests with S3 and MySQL replaced by in-memory fakes."""
import io

import pytest
from PIL import Image

import app as webapp
import photoshield


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.last = sql
        if sql.startswith("INSERT INTO scans"):
            self.db.rows.append(params)

    def fetchone(self):
        located = sum(1 for row in self.db.rows if row[3])
        return (len(self.db.rows), located, sum(row[6] for row in self.db.rows), 123)

    def fetchall(self):
        return [(i + 1, "2026-11-21 10:00:00", r[0], r[1], r[2], r[3], r[6], r[9])
                for i, r in enumerate(self.db.rows)][::-1]


class FakeDB:
    def __init__(self):
        self.rows = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        assert Key.startswith("clean/") and ContentType == "image/jpeg"
        self.objects[Key] = Body

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://s3.test/{Params['Key']}?expires={ExpiresIn}"


@pytest.fixture
def client(monkeypatch):
    db, s3 = FakeDB(), FakeS3()
    monkeypatch.setattr(webapp, "connect", db)
    monkeypatch.setattr(webapp, "s3", s3)
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        c.db, c.s3 = db, s3
        yield c


def test_health(client):
    assert client.get("/health").data == b"ok"


def test_home_shows_stats(client):
    page = client.get("/").get_data(as_text=True)
    assert "photos scanned" in page and "Try a sample phone photo" in page


def test_sample_scan_end_to_end(client):
    page = client.post("/sample").get_data(as_text=True)
    assert "reveals exactly where it was taken" in page
    assert "30.354000, 76.370000" in page
    assert "Google Pixel 8" in page
    assert len(client.s3.objects) == 1
    stored = next(iter(client.s3.objects.values()))
    assert len(Image.open(io.BytesIO(stored)).getexif()) == 0
    row = client.db.rows[0]
    assert row[0] == "sample" and row[3] is True  # had_location
    assert not any(isinstance(v, float) for v in row)  # coordinates are never stored
    home = client.get("/").get_data(as_text=True)
    assert "Latest scans" in home and "sample" in home


def test_upload_scan(client):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "green").save(buf, "JPEG")
    resp = client.post("/scan", data={"photo": (io.BytesIO(buf.getvalue()), "x.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    assert "No hidden metadata found" in resp.get_data(as_text=True)


def test_upload_rejects_non_image(client):
    resp = client.post("/scan", data={"photo": (io.BytesIO(b"hello"), "x.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "not a readable image" in resp.get_data(as_text=True)
    assert client.s3.objects == {}


def test_upload_without_file(client):
    resp = client.post("/scan", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_too_large_upload(client):
    big = b"\xff" * (webapp.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    resp = client.post("/scan", data={"photo": (io.BytesIO(big), "big.jpg")},
                       content_type="multipart/form-data")
    assert resp.status_code == 413


def test_database_outage_still_scans(client, monkeypatch):
    def broken():
        raise OSError("db down")
    monkeypatch.setattr(webapp, "connect", broken)
    resp = client.post("/sample")
    assert resp.status_code == 200
    assert "could not be added to the statistics" in resp.get_data(as_text=True)
    assert "database unreachable" in client.get("/").get_data(as_text=True)

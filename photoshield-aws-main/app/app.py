"""PhotoShield: shows what a photo's hidden metadata reveals, then removes it.

Runs on every web instance behind the load balancer. Cleaned photos are stored in S3
(auto-deleted after 24 hours); a record of each scan goes to RDS MySQL. Coordinates are
shown to the uploader but never stored.
"""
import json
import logging
import os
import time
import urllib.request
import uuid

import boto3
import pymysql
from botocore.config import Config
from flask import Flask, render_template, request

import photoshield

REGION = os.environ["AWS_REGION"]
DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
S3_BUCKET = os.environ["S3_BUCKET"]
RDS_CA_BUNDLE = os.environ.get("RDS_CA_BUNDLE", "/opt/app/rds-ca.pem")
CREDENTIAL_CACHE_SECONDS = 300
MAX_UPLOAD_MB = 15
CLEAN_PREFIX = "clean/"
LINK_SECONDS = 600

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
logging.basicConfig(level=logging.INFO)
s3 = boto3.client("s3", region_name=REGION, config=Config(signature_version="s3v4"))
secrets = boto3.client("secretsmanager", region_name=REGION)


def imds(path):
    """Read instance metadata using IMDSv2 (session token required)."""
    token_request = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
    )
    token = urllib.request.urlopen(token_request, timeout=2).read().decode()
    request_ = urllib.request.Request(
        "http://169.254.169.254/latest/meta-data/" + path,
        headers={"X-aws-ec2-metadata-token": token},
    )
    return urllib.request.urlopen(request_, timeout=2).read().decode()


try:
    INSTANCE_ID = imds("instance-id")
    AZ = imds("placement/availability-zone")
except OSError:
    INSTANCE_ID, AZ = "local", "local"


# RDS rotates the managed master password, so credentials are cached briefly
# and re-fetched from Secrets Manager when they expire or stop working.
_credentials = {"value": None, "fetched_at": 0.0}


def db_credentials(force_refresh=False):
    expired = time.time() - _credentials["fetched_at"] > CREDENTIAL_CACHE_SECONDS
    if force_refresh or expired or _credentials["value"] is None:
        secret = secrets.get_secret_value(SecretId=DB_SECRET_ARN)
        _credentials["value"] = json.loads(secret["SecretString"])
        _credentials["fetched_at"] = time.time()
    return _credentials["value"]


def connect():
    for attempt in (1, 2):
        creds = db_credentials(force_refresh=attempt == 2)
        try:
            return pymysql.connect(
                host=DB_HOST,
                user=creds["username"],
                password=creds["password"],
                database=DB_NAME,
                connect_timeout=3,
                autocommit=True,
                ssl_ca=RDS_CA_BUNDLE,
                ssl_verify_cert=True,
                ssl_verify_identity=True,
            )
        except pymysql.err.OperationalError as exc:
            access_denied = exc.args and exc.args[0] == 1045
            if attempt == 2 or not access_denied:
                raise
    raise RuntimeError("unreachable")


_schema_ready = False


def ensure_schema(cur):
    global _schema_ready
    if _schema_ready:
        return
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source VARCHAR(10) NOT NULL,
            instance_id VARCHAR(32) NOT NULL,
            az VARCHAR(32) NOT NULL,
            had_location BOOLEAN NOT NULL,
            had_device BOOLEAN NOT NULL,
            had_timestamp BOOLEAN NOT NULL,
            fields_removed INT NOT NULL,
            bytes_in INT NOT NULL,
            bytes_out INT NOT NULL,
            processing_ms INT NOT NULL,
            s3_key VARCHAR(100) NOT NULL
        )
        """
    )
    _schema_ready = True


def record_scan(source, result, bytes_in, processing_ms, key):
    f = result.findings
    with connect() as conn, conn.cursor() as cur:
        ensure_schema(cur)
        cur.execute(
            "INSERT INTO scans (source, instance_id, az, had_location, had_device, had_timestamp,"
            " fields_removed, bytes_in, bytes_out, processing_ms, s3_key)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (source, INSTANCE_ID, AZ, f.has_location, f.has_device, f.has_timestamp,
             f.fields_removed, bytes_in, len(result.cleaned), processing_ms, key),
        )


def load_stats():
    """Totals and the latest scans for the home page. Returns None if the database is unreachable."""
    try:
        with connect() as conn, conn.cursor() as cur:
            ensure_schema(cur)
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(had_location), 0), COALESCE(SUM(fields_removed), 0),"
                " COALESCE(ROUND(AVG(processing_ms)), 0) FROM scans"
            )
            total, located, fields, avg_ms = cur.fetchone()
            cur.execute(
                "SELECT id, created_at, source, instance_id, az, had_location, fields_removed, processing_ms"
                " FROM scans ORDER BY id DESC LIMIT 8"
            )
            recent = cur.fetchall()
        return {"total": int(total), "located": int(located), "fields": int(fields),
                "avg_ms": int(avg_ms), "recent": recent}
    except Exception:
        app.logger.exception("loading stats failed")
        return None


@app.context_processor
def served_by():
    return {"instance_id": INSTANCE_ID, "az": AZ, "max_mb": MAX_UPLOAD_MB}


def home(error=None, status=200):
    return render_template("index.html", stats=load_stats(), error=error), status


def scan(data, source):
    started = time.perf_counter()
    try:
        result = photoshield.process(data)
    except photoshield.InvalidImage as exc:
        return home(error=str(exc), status=400)
    processing_ms = round((time.perf_counter() - started) * 1000)

    key = f"{CLEAN_PREFIX}{uuid.uuid4().hex}.jpg"
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=result.cleaned, ContentType="image/jpeg")
        preview_url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": S3_BUCKET, "Key": key}, ExpiresIn=LINK_SECONDS)
        download_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key,
                    "ResponseContentDisposition": 'attachment; filename="photoshield-clean.jpg"'},
            ExpiresIn=LINK_SECONDS,
        )
    except Exception:
        app.logger.exception("S3 upload failed")
        return home(error="Storage is unavailable right now. Please try again.", status=503)

    recorded = True
    try:
        record_scan(source, result, len(data), processing_ms, key)
    except Exception:
        app.logger.exception("recording scan failed")
        recorded = False

    return render_template(
        "result.html",
        f=result.findings,
        result=result,
        source=source,
        bytes_in=len(data),
        processing_ms=processing_ms,
        preview_url=preview_url,
        download_url=download_url,
        link_minutes=LINK_SECONDS // 60,
        recorded=recorded,
    )


@app.get("/")
def index():
    return home()


@app.post("/scan")
def scan_upload():
    upload = request.files.get("photo")
    if upload is None or not upload.filename:
        return home(error="Choose a photo first.", status=400)
    return scan(upload.read(), "upload")


@app.post("/sample")
def scan_sample():
    return scan(photoshield.sample_photo(), "sample")


@app.errorhandler(413)
def too_large(_exc):
    return home(error=f"That file is over {MAX_UPLOAD_MB} MB.", status=413)


@app.get("/health")
def health():
    # Deliberately independent of RDS/S3: a database outage must not make the
    # load balancer mark every web instance unhealthy.
    return "ok", 200

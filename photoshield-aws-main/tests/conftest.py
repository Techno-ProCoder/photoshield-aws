import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("DB_HOST", "db.invalid")
os.environ.setdefault("DB_NAME", "appdb")
os.environ.setdefault("DB_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("S3_BUCKET", "test-bucket")

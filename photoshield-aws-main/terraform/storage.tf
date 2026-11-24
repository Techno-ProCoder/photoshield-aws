data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "uploads" {
  bucket = "${var.project}-uploads-${data.aws_caller_identity.current.account_id}"

  # Lets `terraform destroy` remove the bucket even when it holds test files.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "uploads_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.uploads.arn,
      "${aws_s3_bucket.uploads.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  policy = data.aws_iam_policy_document.uploads_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.uploads]
}

# Cleaned photos are only needed long enough to download: delete them after a day.
resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "expire-cleaned-photos"
    status = "Enabled"

    filter {
      prefix = "clean/"
    }

    expiration {
      days = 1
    }
  }
}

# --- Application code: uploaded to s3://<bucket>/app/, downloaded by each instance at boot ---

locals {
  app_dir   = "${path.module}/../app"
  app_files = setsubtract(fileset(local.app_dir, "**"), fileset(local.app_dir, "**/__pycache__/**"))
  # Changes whenever any app file changes; it is part of the user data, so a new app
  # version produces a new launch template version and a rolling instance refresh.
  app_hash = sha1(join(",", [for f in sort(tolist(local.app_files)) : filemd5("${local.app_dir}/${f}")]))
}

resource "aws_s3_object" "app" {
  for_each = local.app_files

  bucket = aws_s3_bucket.uploads.id
  key    = "app/${each.value}"
  source = "${local.app_dir}/${each.value}"
  etag   = filemd5("${local.app_dir}/${each.value}")
}

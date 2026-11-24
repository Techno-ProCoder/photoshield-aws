# Instance role: no access keys on the servers. Grants only what the app needs
# (its bucket and its DB secret) plus SSM Session Manager for keyless shell
# access and the CloudWatch agent for OS-level metrics.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "web" {
  name               = "${var.project}-web-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.web.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.web.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

data "aws_iam_policy_document" "web_app" {
  # Read the app code and cleaned photos; write only cleaned photos, so a
  # compromised instance cannot replace the application code in app/.
  statement {
    sid       = "ReadObjects"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/*"]
  }

  statement {
    sid       = "WriteCleanedPhotos"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/clean/*"]
  }

  statement {
    sid       = "UploadsBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.uploads.arn]
  }

  statement {
    sid       = "DatabaseSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.main.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "web_app" {
  name   = "${var.project}-web-app"
  role   = aws_iam_role.web.id
  policy = data.aws_iam_policy_document.web_app.json
}

resource "aws_iam_instance_profile" "web" {
  name = "${var.project}-web-profile"
  role = aws_iam_role.web.name
}

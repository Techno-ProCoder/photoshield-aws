data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_launch_template" "web" {
  name                   = "${var.project}-web-lt"
  image_id               = data.aws_ssm_parameter.al2023.insecure_value
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.web.id]
  update_default_version = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.web.arn
  }

  # IMDSv2 only: blocks the SSRF-style credential theft that IMDSv1 allows.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # "standard" CPU credits: a long load test can't run up surprise charges the way
  # "unlimited" mode can. Load tests set cpu_credits = "unlimited" so a fresh
  # instance with an empty credit balance isn't throttled to its 10% baseline.
  credit_specification {
    cpu_credits = var.cpu_credits
  }

  # 1-minute CPU metrics (default is 5-minute), so target tracking reacts in
  # minutes instead of a quarter of an hour.
  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    app_hash      = local.app_hash
    region        = var.region
    db_host       = aws_db_instance.main.address
    db_name       = aws_db_instance.main.db_name
    db_secret_arn = aws_db_instance.main.master_user_secret[0].secret_arn
    s3_bucket     = aws_s3_bucket.uploads.bucket
    app_port      = local.app_port
  }))

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "${var.project}-web"
      Project = var.project
    }
  }
}

resource "aws_autoscaling_group" "web" {
  name                = "${var.project}-web-asg"
  min_size            = var.asg_min
  max_size            = var.asg_max
  desired_capacity    = var.asg_desired
  vpc_zone_identifier = local.subnet_ids
  target_group_arns   = [aws_lb_target_group.web.arn]
  health_check_type   = "ELB"
  # First boot installs Python packages (~3-5 min on a t3.micro).
  health_check_grace_period = 420
  wait_for_capacity_timeout = "15m"

  enabled_metrics = [
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupPendingInstances",
    "GroupTerminatingInstances",
    "GroupTotalInstances",
  ]

  launch_template {
    id      = aws_launch_template.web.id
    version = aws_launch_template.web.latest_version
  }

  # A changed launch template (new app version, new AMI) rolls out one
  # instance at a time while the rest keep serving traffic.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-web"
    propagate_at_launch = true
  }

  lifecycle {
    # The scaling policy owns desired capacity after creation.
    ignore_changes = [desired_capacity]
  }

  # Instances download the app from S3 at boot, so it must be uploaded first.
  depends_on = [aws_s3_object.app]
}

resource "aws_autoscaling_policy" "cpu_target" {
  name                   = "${var.project}-cpu-target"
  autoscaling_group_name = aws_autoscaling_group.web.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value = var.cpu_target
  }
}

output "app_url" {
  description = "Public URL of the application (via the load balancer)."
  value       = "http://${aws_lb.web.dns_name}"
}

output "asg_name" {
  value = aws_autoscaling_group.web.name
}

output "db_endpoint" {
  description = "Private RDS endpoint (reachable only from the web tier)."
  value       = aws_db_instance.main.address
}

output "db_secret_arn" {
  description = "Secrets Manager secret holding the RDS master credentials."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "uploads_bucket" {
  value = aws_s3_bucket.uploads.bucket
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.main.dashboard_name}"
}

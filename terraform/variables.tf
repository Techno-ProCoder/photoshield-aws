variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "cloud-ha"
}

variable "azs" {
  description = "Availability Zones used by the load balancer, web tier and DB subnet group."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.azs) >= 2
    error_message = "High availability needs at least two Availability Zones."
  }
}

variable "instance_type" {
  description = "Web tier instance type (free-tier eligible)."
  type        = string
  default     = "t3.micro"
}

variable "asg_min" {
  type    = number
  default = 2
}

variable "asg_max" {
  type    = number
  default = 4
}

variable "asg_desired" {
  type    = number
  default = 2
}

variable "cpu_target" {
  description = "Average CPU utilisation (%) the target-tracking policy keeps the web tier at."
  type        = number
  default     = 50
}

variable "cpu_credits" {
  description = "T3 CPU credit mode: standard (throttles at baseline, no surprise charges) or unlimited (for load tests)."
  type        = string
  default     = "standard"

  validation {
    condition     = contains(["standard", "unlimited"], var.cpu_credits)
    error_message = "cpu_credits must be \"standard\" or \"unlimited\"."
  }
}

variable "db_instance_class" {
  description = "RDS instance class (free-tier eligible)."
  type        = string
  default     = "db.t3.micro"
}

variable "alert_email" {
  description = "Email address subscribed to CloudWatch alarm notifications. Leave empty to skip."
  type        = string
  default     = ""
}

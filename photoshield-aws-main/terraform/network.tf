# Uses the account's default VPC (public subnets, internet gateway already in
# place) so no NAT gateway is needed. Web instances still accept traffic only
# from the load balancer, and the database only from the web tier.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "selected" {
  for_each = toset(var.azs)

  vpc_id            = data.aws_vpc.default.id
  availability_zone = each.value
  default_for_az    = true
}

locals {
  subnet_ids = [for az in var.azs : data.aws_subnet.selected[az].id]
  app_port   = 8000
}

# --- Load balancer: HTTP from the internet, forwards only to the web tier ---

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb-sg"
  description = "Public HTTP to the application load balancer"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_web" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to web instances"
  referenced_security_group_id = aws_security_group.web.id
  ip_protocol                  = "tcp"
  from_port                    = local.app_port
  to_port                      = local.app_port
}

# --- Web tier: app port only from the ALB; outbound for packages and AWS APIs ---

resource "aws_security_group" "web" {
  name        = "${var.project}-web-sg"
  description = "Web instances; inbound only from the load balancer"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.web.id
  description                  = "App port from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = local.app_port
  to_port                      = local.app_port
}

resource "aws_vpc_security_group_egress_rule" "web_all_out" {
  security_group_id = aws_security_group.web.id
  description       = "Package repos, AWS APIs, RDS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Database: MySQL only from the web tier, no outbound rules ---

resource "aws_security_group" "db" {
  name        = "${var.project}-db-sg"
  description = "RDS MySQL; inbound only from web instances"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_web" {
  security_group_id            = aws_security_group.db.id
  description                  = "MySQL from web instances"
  referenced_security_group_id = aws_security_group.web.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

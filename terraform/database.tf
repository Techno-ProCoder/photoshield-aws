# Single-AZ RDS MySQL to stay within free-tier limits. This is the one known
# single point of failure; Multi-AZ would add a synchronous standby in a second
# AZ with automatic failover (set multi_az = true, roughly doubling DB cost).

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db-subnets"
  subnet_ids = local.subnet_ids
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project}-db"
  engine         = "mysql"
  engine_version = "8.4"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "appdb"
  username = "appadmin"
  # RDS generates the password and stores it in Secrets Manager; it never
  # appears in Terraform code, state or the instances' user data.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period    = 1
  auto_minor_version_upgrade = true
  apply_immediately          = true
  deletion_protection        = false
  skip_final_snapshot        = true
}

# RDS PostgreSQL 16 in the isolated data subnets.
# - The master password is generated and stored by RDS in Secrets Manager
#   (manage_master_user_password), so it never appears in Terraform state.
# - rds.force_ssl = 1 rejects any connection without TLS.

data "aws_partition" "current" {}

resource "aws_db_subnet_group" "this" {
  name        = var.name
  description = "Isolated data subnets for ${var.name}"
  subnet_ids  = var.subnet_ids

  tags = var.tags
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "PostgreSQL from the API and worker tasks only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-db" })
}

resource "aws_vpc_security_group_ingress_rule" "db_from_apps" {
  for_each = var.allowed_security_group_ids

  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from ${each.key} tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = each.value
}

resource "aws_db_parameter_group" "this" {
  name        = "${var.name}-pg16"
  family      = "postgres16"
  description = "Polygraph PostgreSQL 16 settings"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "ssl_min_protocol_version"
    value = "TLSv1.2"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  # Log slow statements, but never statement parameters: they could hold user data.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "log_parameter_max_length"
    value = "0"
  }

  parameter {
    name  = "log_parameter_max_length_on_error"
    value = "0"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  parameter {
    name  = "idle_in_transaction_session_timeout"
    value = "60000"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "postgresql" {
  name              = "/aws/rds/instance/${var.name}/postgresql"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "upgrade" {
  name              = "/aws/rds/instance/${var.name}/upgrade"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitoring" {
  name               = "${var.name}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_157:Multi-AZ is set per environment: on in production, off in staging.
  #checkov:skip=CKV_AWS_293:Deletion protection is set per environment: on in production, off in staging.
  identifier = var.name

  engine                     = "postgres"
  engine_version             = var.engine_version
  auto_minor_version_upgrade = true
  instance_class             = var.instance_class
  ca_cert_identifier         = "rds-ca-rsa2048-g1"

  db_name                             = var.database_name
  username                            = var.master_username
  manage_master_user_password         = true
  master_user_secret_kms_key_id       = var.kms_key_arn
  iam_database_authentication_enabled = true

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  multi_az               = var.multi_az
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  port                   = 5432
  network_type           = "IPV4"

  parameter_group_name = aws_db_parameter_group.this.name

  backup_retention_period  = var.backup_retention_days
  backup_window            = "19:30-20:30" # 01:00-02:00 IST
  maintenance_window       = "sun:21:00-sun:22:00"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name}-final"

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_key_arn
  performance_insights_retention_period = 7

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.monitoring.arn

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  apply_immediately = var.apply_immediately

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.postgresql,
    aws_cloudwatch_log_group.upgrade,
  ]
}

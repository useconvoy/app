# RDS Postgres 16. Row-level security and the pgvector extension are applied
# at the app layer (migrations run CREATE EXTENSION vector; every connection
# sets tenant context). This file only guarantees the substrate: encrypted,
# private, TLS-required Postgres.

resource "aws_db_subnet_group" "this" {
  name       = "${local.name_prefix}-db"
  subnet_ids = aws_subnet.private[*].id

  tags = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

resource "aws_security_group" "db" {
  name        = "${local.name_prefix}-db"
  description = "RDS Postgres for the ${var.stack_name} stack"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

resource "aws_vpc_security_group_ingress_rule" "db_from_control_plane" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from control plane"
  referenced_security_group_id = aws_security_group.control_plane.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_worker" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from Temporal workers"
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_litellm" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from LiteLLM proxy (virtual keys store)"
  referenced_security_group_id = aws_security_group.litellm.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_db_parameter_group" "this" {
  name        = "${local.name_prefix}-pg16"
  family      = "postgres16"
  description = "Convoy ${var.stack_name}: force TLS"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-pg16" })
}

resource "random_password" "db_master" {
  length  = 32
  special = false
}

resource "aws_db_instance" "this" {
  identifier = "${local.name_prefix}-db"

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = var.db_name
  username = "convoy_admin"
  password = random_password.db_master.result
  port     = 5432

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.stack.arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period   = var.db_backup_retention_days
  copy_tags_to_snapshot     = true
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = "${local.name_prefix}-db-final"

  performance_insights_enabled = true
  monitoring_interval          = 0

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

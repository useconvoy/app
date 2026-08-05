# Console Postgres. This instance holds identity, organizations, teams,
# notifications, feedback, catalog, and billing rows — never runs, plans, or
# checkpoints, which are read through the control plane. The schema, the
# RLS-bound convoy_website_app role, and every policy come from
# `npm run db:migrate` as a one-off ECS task; Terraform owns only the
# substrate: encrypted, private, TLS-required.

resource "aws_db_subnet_group" "this" {
  name       = "${local.name_prefix}-db"
  subnet_ids = aws_subnet.private[*].id

  tags = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

resource "aws_security_group" "db" {
  name        = "${local.name_prefix}-db"
  description = "Console Postgres for ${var.stack_name}"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

resource "aws_vpc_security_group_ingress_rule" "db_from_web" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the web service"
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_tasks" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the notifier and one-off tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_db_parameter_group" "this" {
  name        = "${local.name_prefix}-pg16"
  family      = "postgres16"
  description = "Convoy console ${var.stack_name}: force TLS"

  # Refuse plaintext connections at the server. The DSNs this module generates
  # verify the certificate as well, so neither side settles for less.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-pg16" })
}

resource "random_password" "db_master" {
  length = 32
  # Only the migrator uses these credentials, and it receives them as a URL;
  # dropping punctuation keeps that URL free of percent-encoding.
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

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.console.arn

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

  performance_insights_enabled = var.db_performance_insights_enabled
  monitoring_interval          = 0

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

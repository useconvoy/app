resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_db_parameter_group" "this" {
  name   = var.name
  family = "postgres17"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
resource "aws_db_instance" "this" {
  identifier                      = var.name
  engine                          = "postgres"
  engine_version                  = var.postgres_version
  instance_class                  = "db.t4g.small"
  allocated_storage               = 20
  max_allocated_storage           = 0
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "convoy"
  username                        = "convoy_admin"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.this.name
  parameter_group_name            = aws_db_parameter_group.this.name
  vpc_security_group_ids          = [aws_security_group.database.id]
  publicly_accessible             = false
  multi_az                        = false
  availability_zone               = var.availability_zones[0]
  backup_retention_period         = 7
  backup_window                   = "04:00-05:00"
  maintenance_window              = "sun:05:00-sun:06:00"
  auto_minor_version_upgrade      = false
  allow_major_version_upgrade     = false
  apply_immediately               = false
  copy_tags_to_snapshot           = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = var.final_snapshot_identifier
  enabled_cloudwatch_logs_exports = ["postgresql"]
  depends_on                      = [aws_cloudwatch_log_group.database]
}
# Only containers, never secret versions: no password is read into Terraform state.
resource "aws_secretsmanager_secret" "this" {
  for_each                = toset(["runtime-db", "migration-db", "admin", "execution", "probe"])
  name                    = "${var.name}/${each.key}"
  recovery_window_in_days = 7
}
resource "aws_cloudwatch_log_group" "database" {
  name              = "/aws/rds/instance/${var.name}/postgresql"
  retention_in_days = 14
}
resource "aws_budgets_budget" "account_warning" {
  name         = "${var.name}-account-warning"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}

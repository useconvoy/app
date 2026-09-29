resource "aws_vpc" "trial" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
}
resource "aws_internet_gateway" "trial" { vpc_id = aws_vpc.trial.id }
resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.trial.id
  availability_zone = var.availability_zones[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.trial.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.trial.id
  }
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_security_group" "alb" {
  name   = "${var.name}-https"
  vpc_id = aws_vpc.trial.id
}
resource "aws_security_group" "planner" {
  name   = "${var.name}-planner"
  vpc_id = aws_vpc.trial.id
}
resource "aws_vpc_security_group_ingress_rule" "client_https" {
  for_each          = var.client_cidrs
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_ingress_rule" "planner" {
  security_group_id            = aws_security_group.planner.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "alb" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.planner.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "planner_https" {
  security_group_id = aws_security_group.planner.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "ECR, Secrets Manager and logs via the task public IP; no NAT."
}

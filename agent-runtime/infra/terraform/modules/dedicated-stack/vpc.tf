# Stack VPC: public subnets carry only the control-plane ALB and NAT
# gateway(s); every service task runs in private subnets. S3/ECR/logs (and
# supporting sts/secretsmanager/kms) resolve through VPC endpoints so the
# sandbox security group can be locked to endpoint-only egress.

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-igw" })
}

# --- Subnets ---------------------------------------------------------------

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = false

  tags = merge(local.tags, {
    Name          = "${local.name_prefix}-public-${local.azs[count.index]}"
    "convoy:tier" = "public"
  })
}

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)

  tags = merge(local.tags, {
    Name          = "${local.name_prefix}-private-${local.azs[count.index]}"
    "convoy:tier" = "private"
  })
}

# --- NAT -------------------------------------------------------------------

locals {
  nat_gateway_count = var.single_nat_gateway ? 1 : var.az_count
}

resource "aws_eip" "nat" {
  count = local.nat_gateway_count

  domain = "vpc"

  tags = merge(local.tags, { Name = "${local.name_prefix}-nat-${count.index}" })
}

resource "aws_nat_gateway" "this" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(local.tags, { Name = "${local.name_prefix}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

# --- Routing ---------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count = var.az_count

  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-private-${local.azs[count.index]}" })
}

resource "aws_route" "private_nat" {
  count = var.az_count

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[var.single_nat_gateway ? 0 : count.index].id
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# --- VPC endpoints ---------------------------------------------------------

resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.name_prefix}-vpce"
  description = "Interface VPC endpoints for the ${var.stack_name} stack"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-vpce" })
}

resource "aws_vpc_security_group_ingress_rule" "vpce_https_from_vpc" {
  security_group_id = aws_security_group.vpc_endpoints.id
  description       = "HTTPS from inside the VPC"
  cidr_ipv4         = aws_vpc.this.cidr_block
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(local.tags, { Name = "${local.name_prefix}-s3" })
}

locals {
  interface_endpoints = [
    "ecr.api",        # image pulls (auth + metadata)
    "ecr.dkr",        # image pulls (layers, via S3 gateway)
    "logs",           # awslogs driver from private subnets / sandbox
    "secretsmanager", # container secret injection without NAT dependency
    "sts",            # per-run scoped credential minting (DESIGN §12)
    "kms",            # SSE-KMS on artifacts + secrets decryption
  ]
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(local.interface_endpoints)

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${local.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-${replace(each.value, ".", "-")}" })
}

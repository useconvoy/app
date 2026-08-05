# The console's public edge: one ALB serving the marketing site, sign-in, the
# portal, and the Stripe webhook. Everything behind it is private-subnet,
# security-group-to-security-group.
#
# The hosted zone, the certificate, and the alias records are all here so one
# apply turns a registered domain into a working HTTPS address. Nothing waits
# on a human to copy a validation record.

resource "aws_route53_zone" "this" {
  count = var.create_hosted_zone ? 1 : 0

  name    = var.domain_name
  comment = "Convoy console ${var.stack_name}"

  tags = merge(local.tags, { Name = var.domain_name })
}

data "aws_route53_zone" "this" {
  count = var.create_hosted_zone ? 0 : 1

  name         = var.domain_name
  private_zone = false
}

resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"

  # A replacement certificate has to exist and be attached before the old one
  # goes away, or the listener loses its certificate mid-change.
  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-cert" })
}

resource "aws_route53_record" "cert_validation" {
  # Both names on the certificate frequently validate through the same record;
  # keying by record name collapses the duplicate instead of failing on it.
  for_each = {
    for option in aws_acm_certificate.this.domain_validation_options :
    option.resource_record_name => {
      type   = option.resource_record_type
      record = option.resource_record_value
    }...
  }

  zone_id         = local.hosted_zone_id
  name            = each.key
  type            = each.value[0].type
  ttl             = 60
  records         = [each.value[0].record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

# --- Load balancer ---------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public HTTPS ingress to the ${var.stack_name} console"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = toset(var.alb_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from ${each.value}"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each = toset(var.alb_ingress_cidrs)

  # Open only to answer with a redirect. People type the bare hostname and
  # links arrive without a scheme; refusing port 80 would read as an outage.
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from ${each.value} (redirected to HTTPS)"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "alb_to_web" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to web tasks"
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = var.web_port
  to_port                      = var.web_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_lb" "console" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  idle_timeout               = var.alb_idle_timeout
  drop_invalid_header_fields = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_lb_target_group" "web" {
  name        = "${local.name_prefix}-web"
  port        = var.web_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  # Long enough for open SSE streams to end on their own rather than being cut
  # when a task is replaced.
  deregistration_delay = 120

  health_check {
    path                = var.web_health_check_path
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-web" })
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.console.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  # The validated certificate, not the raw one: this makes the listener wait
  # for DNS validation to finish instead of attaching a certificate AWS has
  # not issued yet.
  certificate_arn = aws_acm_certificate_validation.this.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }

  tags = local.tags
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.console.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }

  tags = local.tags
}

# --- Public names ----------------------------------------------------------

resource "aws_route53_record" "console" {
  for_each = toset(local.served_domains)

  zone_id = local.hosted_zone_id
  name    = each.value
  type    = "A"

  alias {
    name                   = aws_lb.console.dns_name
    zone_id                = aws_lb.console.zone_id
    evaluate_target_health = true
  }
}

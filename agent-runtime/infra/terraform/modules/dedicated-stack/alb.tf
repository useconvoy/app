# Control-plane ALB — the ONLY public ingress in the stack, HTTPS only.
# Everything else is private-subnet, security-group-to-security-group.

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public HTTPS ingress to the ${var.stack_name} control plane"
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

resource "aws_vpc_security_group_egress_rule" "alb_to_control_plane" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to control-plane tasks"
  referenced_security_group_id = aws_security_group.control_plane.id
  from_port                    = var.control_plane_port
  to_port                      = var.control_plane_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_lb" "control_plane" {
  name               = substr("${local.name_prefix}-cp", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-cp" })
}

resource "aws_lb_target_group" "control_plane" {
  name        = substr("${local.name_prefix}-cp", 0, 32)
  port        = var.control_plane_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  # SSE streams are long-lived; give connections a generous idle drain.
  deregistration_delay = 60

  health_check {
    path                = var.control_plane_health_check_path
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-cp" })
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.control_plane.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.control_plane.arn
  }

  tags = local.tags
}

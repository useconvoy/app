locals {
  frontends = {
    web       = { ingress = "management", port = 3000, health = "/console" }
    api       = { ingress = "management", port = 8080, health = "/api/health" }
    inference = { ingress = "inference", port = 8080, health = "/health" }
  }
}
resource "aws_lb" "this" {
  for_each                   = toset(["management", "inference"])
  name                       = "${var.name}-${each.key == "management" ? "app" : "model"}"
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.ingress[each.key].id]
  drop_invalid_header_fields = true
  idle_timeout               = 30
}
resource "aws_lb_target_group" "this" {
  for_each             = local.frontends
  name                 = "${var.name}-${each.key}"
  vpc_id               = aws_vpc.this.id
  target_type          = "ip"
  port                 = each.value.port
  protocol             = "HTTP"
  deregistration_delay = 15
  health_check {
    path                = each.value.health
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}
resource "aws_lb_listener" "https" {
  for_each          = aws_lb.this
  load_balancer_arn = each.value.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arns[each.key]
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Not found"
    }
  }
}
resource "aws_lb_listener_rule" "this" {
  for_each     = local.frontends
  listener_arn = aws_lb_listener.https[each.value.ingress].arn
  priority     = each.key == "api" ? 20 : 10
  condition {
    host_header { values = [var.domains[each.key]] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[each.key].arn
  }
}
resource "aws_route53_record" "this" {
  for_each = local.frontends
  zone_id  = var.zone_id
  name     = var.domains[each.key]
  type     = "A"
  alias {
    name                   = aws_lb.this[each.value.ingress].dns_name
    zone_id                = aws_lb.this[each.value.ingress].zone_id
    evaluate_target_health = true
  }
}

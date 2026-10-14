# Application Load Balancer that only CloudFront can use.
#
# Two locks:
#   1. The security group accepts 443 only from the CloudFront origin-facing
#      managed prefix list, so nothing else on the internet can connect.
#   2. Every listener rule also requires a secret header that only our
#      CloudFront distribution adds. Anything else gets a plain 403.

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "HTTPS from CloudFront origin-facing servers only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-alb" })
}

resource "aws_vpc_security_group_ingress_rule" "https_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from CloudFront"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
}

resource "aws_vpc_security_group_egress_rule" "to_targets" {
  for_each = {
    api = var.api_port
    web = var.web_port
  }

  security_group_id = aws_security_group.alb.id
  description       = "To ${each.key} tasks"
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
  cidr_ipv4         = var.vpc_cidr_block
}

resource "aws_lb" "this" {
  #checkov:skip=CKV2_AWS_28:WAF is attached at CloudFront (CLOUDFRONT scope); the ALB is unreachable except through it.
  #checkov:skip=CKV2_AWS_20:There is no HTTP listener at all; CloudFront talks HTTPS only.
  name               = substr(var.name, 0, 32)
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
  ip_address_type    = "ipv4"

  idle_timeout                     = 60
  drop_invalid_header_fields       = true
  desync_mitigation_mode           = "strictest"
  enable_deletion_protection       = var.deletion_protection
  enable_http2                     = true
  enable_cross_zone_load_balancing = true
  preserve_host_header             = false
  xff_header_processing_mode       = "append"

  access_logs {
    bucket  = var.logs_bucket_name
    prefix  = "alb"
    enabled = true
  }

  tags = var.tags
}

resource "aws_lb_target_group" "api" {
  #checkov:skip=CKV_AWS_378:TLS ends at the ALB; ALB-to-task traffic stays inside private subnets.
  name                 = substr("${var.name}-api", 0, 32)
  port                 = var.api_port
  protocol             = "HTTP"
  protocol_version     = "HTTP1"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = var.api_health_check_path
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = var.tags
}

resource "aws_lb_target_group" "web" {
  #checkov:skip=CKV_AWS_378:TLS ends at the ALB; ALB-to-task traffic stays inside private subnets.
  name                 = substr("${var.name}-web", 0, 32)
  port                 = var.web_port
  protocol             = "HTTP"
  protocol_version     = "HTTP1"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = var.web_health_check_path
    matcher             = "200-399"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = var.tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = var.ssl_policy
  certificate_arn   = var.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = "{\"error\":{\"code\":\"forbidden\",\"message\":\"Forbidden.\"}}"
      status_code  = "403"
    }
  }

  tags = var.tags
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  condition {
    http_header {
      http_header_name = var.origin_verify_header_name
      values           = var.origin_verify_secrets
    }
  }

  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  tags = var.tags
}

resource "aws_lb_listener_rule" "web" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  condition {
    http_header {
      http_header_name = var.origin_verify_header_name
      values           = var.origin_verify_secrets
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }

  tags = var.tags
}

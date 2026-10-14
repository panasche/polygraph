# WAF web ACL for CloudFront. Must be created in us-east-1 (pass that provider).
#
# Rules, in order:
#   1. Rate limit on /api/upload per IP (stricter)
#   2. Rate limit on everything per IP
#   3. Amazon IP reputation list
#   4. Known bad inputs (Log4j, etc.)
#   5. Core rule set, everywhere except /api/upload
#   6. Core rule set on /api/upload, with the body rules in count mode:
#      results files are large JSON documents that trip the generic body rules.
#      The API validates them strictly against the results schema instead.

locals {
  upload_path = "/api/upload"

  upload_body_rules = [
    "SizeRestrictions_BODY",
    "CrossSiteScripting_BODY",
    "GenericLFI_BODY",
    "GenericRFI_BODY",
    "EC2MetaDataSSRF_BODY",
  ]
}

resource "aws_wafv2_web_acl" "this" {
  name        = var.name
  description = "Polygraph edge protection (${var.name})"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  custom_response_body {
    key          = "rate_limited"
    content_type = "APPLICATION_JSON"
    content      = "{\"error\":{\"code\":\"rate_limited\",\"message\":\"Too many requests. Slow down and try again.\"}}"
  }

  custom_response_body {
    key          = "blocked"
    content_type = "APPLICATION_JSON"
    content      = "{\"error\":{\"code\":\"forbidden\",\"message\":\"Request blocked.\"}}"
  }

  rule {
    name     = "rate-limit-upload-per-ip"
    priority = 1

    action {
      block {
        custom_response {
          response_code            = 429
          custom_response_body_key = "rate_limited"
        }
      }
    }

    statement {
      rate_based_statement {
        limit                 = var.upload_rate_limit
        evaluation_window_sec = 300
        aggregate_key_type    = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = local.upload_path
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-upload"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit-global-per-ip"
    priority = 2

    action {
      block {
        custom_response {
          response_code            = 429
          custom_response_body_key = "rate_limited"
        }
      }
    }

    statement {
      rate_based_statement {
        limit                 = var.global_rate_limit
        evaluation_window_sec = 300
        aggregate_key_type    = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-global"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-ip-reputation"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesAmazonIpReputationList"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-ip-reputation"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-core-rule-set"
    priority = 5

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"

        scope_down_statement {
          not_statement {
            statement {
              byte_match_statement {
                search_string         = local.upload_path
                positional_constraint = "STARTS_WITH"

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-core"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-core-rule-set-upload"
    priority = 6

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"

        dynamic "rule_action_override" {
          for_each = local.upload_body_rules

          content {
            name = rule_action_override.value

            action_to_use {
              count {}
            }
          }
        }

        scope_down_statement {
          byte_match_statement {
            search_string         = local.upload_path
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-core-upload"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = true
  }

  tags = var.tags
}

# --- Logging -----------------------------------------------------------------
# Only blocked and counted requests are kept, and auth headers and cookies are
# redacted. These logs still hold client IP addresses, so keep retention short.

resource "aws_cloudwatch_log_group" "waf" {
  count = var.logging_enabled ? 1 : 0

  # WAF requires the aws-waf-logs- prefix.
  name              = "aws-waf-logs-${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  count = var.logging_enabled ? 1 : 0

  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf[0].arn]

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }

  redacted_fields {
    single_header {
      name = "cookie"
    }
  }

  redacted_fields {
    single_header {
      name = "x-csrf-token"
    }
  }

  logging_filter {
    default_behavior = "DROP"

    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"

      condition {
        action_condition {
          action = "BLOCK"
        }
      }

      condition {
        action_condition {
          action = "COUNT"
        }
      }
    }
  }
}

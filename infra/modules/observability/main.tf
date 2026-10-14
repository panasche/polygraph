# Log groups, alarms and the monthly budget.
#
# Alarms publish to one encrypted SNS topic. Subscribe a team mailbox or a chat
# integration to it; subscriptions by email need a click to confirm.
#
# Logs never carry payload text, tokens, cookies or JWTs (CONTRACTS.md section 7);
# that is enforced in the apps. Retention here keeps whatever does get logged
# from living forever.

locals {
  log_groups = {
    api               = "/polygraph/${var.name}/api"
    worker            = "/polygraph/${var.name}/worker"
    web               = "/polygraph/${var.name}/web"
    "service-connect" = "/polygraph/${var.name}/service-connect"
  }
}

resource "aws_cloudwatch_log_group" "this" {
  for_each = local.log_groups

  name              = each.value
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = merge(var.tags, { Service = each.key })
}

# --- Alarm topic -------------------------------------------------------------

resource "aws_sns_topic" "alarms" {
  name              = "${var.name}-alarms"
  kms_master_key_id = var.logs_kms_key_arn

  tags = var.tags
}

data "aws_iam_policy_document" "alarms_topic" {
  statement {
    sid       = "AllowCloudWatchAndBudgets"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarms_topic.json
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alarm_emails)

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  alarm_actions = [aws_sns_topic.alarms.arn]
}

# --- ALB: 5xx rate and p95 latency -------------------------------------------

resource "aws_cloudwatch_metric_alarm" "alb_5xx_rate" {
  alarm_name          = "${var.name}-5xx-rate"
  alarm_description   = "More than ${var.error_rate_threshold_percent}% of requests returned 5xx (target or ALB) for 3 of 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_rate_threshold_percent
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "rate"
    expression  = "IF(requests > 20, 100 * (target5xx + elb5xx) / requests, 0)"
    label       = "5xx rate (%)"
    return_data = true
  }

  metric_query {
    id = "requests"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
    }
  }

  metric_query {
    id = "target5xx"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
    }
  }

  metric_query {
    id = "elb5xx"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
    }
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "p95_latency" {
  for_each = {
    api = var.api_target_group_arn_suffix
    web = var.web_target_group_arn_suffix
  }

  alarm_name          = "${var.name}-${each.key}-p95-latency"
  alarm_description   = "p95 response time for ${each.key} above ${var.p95_latency_threshold_seconds}s for 3 of 5 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.p95_latency_threshold_seconds
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = each.value
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  for_each = {
    api = var.api_target_group_arn_suffix
    web = var.web_target_group_arn_suffix
  }

  alarm_name          = "${var.name}-${each.key}-no-healthy-targets"
  alarm_description   = "No healthy ${each.key} targets behind the ALB."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = each.value
  }

  tags = var.tags
}

# --- Queue -------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name}-dlq-not-empty"
  alarm_description   = "An upload failed processing repeatedly and landed in the DLQ. Inspect, fix, then redrive."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = { QueueName = var.dlq_name }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${var.name}-queue-age"
  alarm_description   = "Oldest upload has waited more than ${var.queue_age_threshold_seconds}s. Workers are stuck or under-scaled."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.queue_age_threshold_seconds
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = { QueueName = var.queue_name }

  tags = var.tags
}

# --- Database ----------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.name}-rds-cpu"
  alarm_description   = "RDS CPU above ${var.rds_cpu_threshold_percent}% for 10 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.rds_cpu_threshold_percent
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = { DBInstanceIdentifier = var.db_instance_id }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${var.name}-rds-free-storage"
  alarm_description   = "RDS free storage below ${var.rds_free_storage_threshold_gb} GB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = var.rds_free_storage_threshold_gb * 1024 * 1024 * 1024
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = { DBInstanceIdentifier = var.db_instance_id }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_memory" {
  alarm_name          = "${var.name}-rds-freeable-memory"
  alarm_description   = "RDS freeable memory below 64 MB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeableMemory"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 64 * 1024 * 1024
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  dimensions = { DBInstanceIdentifier = var.db_instance_id }

  tags = var.tags
}

# --- Budget ------------------------------------------------------------------
# AWS Budgets only works in USD. Pick the USD figure that matches the rupee
# limit you want to stay under, and revisit it when the exchange rate moves.

resource "aws_budgets_budget" "monthly" {
  count = var.monthly_budget_usd > 0 ? 1 : 0

  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [join("$", ["user:Environment", var.environment])]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alarms.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.alarms.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alarms.arn]
  }
}

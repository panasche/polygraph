output "log_group_names" {
  description = "Log group names keyed api, worker, web, service-connect."
  value       = { for k, g in aws_cloudwatch_log_group.this : k => g.name }
}

output "log_group_arns" {
  description = "Log group ARNs keyed api, worker, web, service-connect."
  value       = { for k, g in aws_cloudwatch_log_group.this : k => g.arn }
}

output "alarm_topic_arn" {
  description = "SNS topic that receives alarms and budget alerts."
  value       = aws_sns_topic.alarms.arn
}

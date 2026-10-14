variable "name" {
  description = "Name prefix, e.g. polygraph-staging."
  type        = string
}

variable "environment" {
  description = "staging or production (used for the budget's Environment tag filter)."
  type        = string
}

variable "account_id" {
  description = "AWS account id (SNS topic policy condition)."
  type        = string
}

variable "logs_kms_key_arn" {
  description = "KMS key for log groups and the alarm topic."
  type        = string
}

variable "log_retention_days" {
  description = "Retention for application log groups (30 staging, 90 production)."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a value CloudWatch Logs accepts."
  }
}

variable "alarm_emails" {
  description = "Email addresses subscribed to the alarm topic. Use a team alias, not a personal address."
  type        = list(string)
  default     = []
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix."
  type        = string
}

variable "api_target_group_arn_suffix" {
  description = "API target group ARN suffix."
  type        = string
}

variable "web_target_group_arn_suffix" {
  description = "Web target group ARN suffix."
  type        = string
}

variable "queue_name" {
  description = "Upload queue name."
  type        = string
}

variable "dlq_name" {
  description = "Dead-letter queue name."
  type        = string
}

variable "db_instance_id" {
  description = "RDS instance identifier."
  type        = string
}

variable "error_rate_threshold_percent" {
  description = "5xx rate that raises the alarm."
  type        = number
  default     = 5
}

variable "p95_latency_threshold_seconds" {
  description = "p95 target response time that raises the alarm."
  type        = number
  default     = 1.5
}

variable "queue_age_threshold_seconds" {
  description = "Age of the oldest queued upload that raises the alarm."
  type        = number
  default     = 600
}

variable "rds_cpu_threshold_percent" {
  description = "RDS CPU that raises the alarm."
  type        = number
  default     = 80
}

variable "rds_free_storage_threshold_gb" {
  description = "Free storage that raises the alarm."
  type        = number
  default     = 2
}

variable "monthly_budget_usd" {
  description = "Monthly cost limit in USD (AWS Budgets has no INR). 0 disables the budget."
  type        = number
  default     = 0
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

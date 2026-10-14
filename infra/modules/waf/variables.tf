variable "name" {
  description = "Web ACL name, e.g. polygraph-staging."
  type        = string
}

variable "upload_rate_limit" {
  description = "Max requests to /api/upload per client IP in any 5-minute window."
  type        = number
  default     = 100
}

variable "global_rate_limit" {
  description = "Max requests per client IP in any 5-minute window, all paths."
  type        = number
  default     = 2000
}

variable "logging_enabled" {
  description = "Send blocked/counted request logs to CloudWatch (in us-east-1)."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retention for WAF logs."
  type        = number
  default     = 30
}

variable "logs_kms_key_arn" {
  description = "KMS key in us-east-1 for the WAF log group."
  type        = string
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

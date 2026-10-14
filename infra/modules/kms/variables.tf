variable "alias" {
  description = "Alias name without the alias/ prefix, e.g. polygraph-staging-data."
  type        = string
}

variable "description" {
  description = "Human description of what the key protects."
  type        = string
}

variable "logs_service" {
  description = "Allow CloudWatch Logs in this region to use the key for log groups in this account."
  type        = bool
  default     = false
}

variable "service_principals" {
  description = "AWS service principals that publish encrypted messages themselves (e.g. cloudwatch.amazonaws.com, budgets.amazonaws.com for SNS)."
  type        = list(string)
  default     = []
}

variable "deletion_window_in_days" {
  description = "Waiting period before a scheduled key deletion happens."
  type        = number
  default     = 30

  validation {
    condition     = var.deletion_window_in_days >= 7 && var.deletion_window_in_days <= 30
    error_message = "deletion_window_in_days must be between 7 and 30."
  }
}

variable "rotation_period_in_days" {
  description = "How often KMS rotates the key material."
  type        = number
  default     = 365
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

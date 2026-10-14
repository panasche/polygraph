variable "name" {
  description = "Name prefix, e.g. polygraph-staging."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR. Must be a /16 so the /20 subnet split works."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.cidr_block, 0)) && endswith(var.cidr_block, "/16")
    error_message = "cidr_block must be a valid /16."
  }
}

variable "az_count" {
  description = "How many availability zones to use (2 or 3)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "single_nat_gateway" {
  description = "One shared NAT gateway (staging) instead of one per AZ (production)."
  type        = bool
  default     = true
}

variable "flow_logs_enabled" {
  description = "Send rejected-traffic VPC flow logs to CloudWatch."
  type        = bool
  default     = true
}

variable "flow_logs_retention_days" {
  description = "Retention for the flow log group."
  type        = number
  default     = 30
}

variable "logs_kms_key_arn" {
  description = "KMS key for the flow log group."
  type        = string
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

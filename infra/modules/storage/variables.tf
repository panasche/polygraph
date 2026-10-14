variable "name" {
  description = "Name prefix, e.g. polygraph-staging. The account id is appended to bucket names."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key that encrypts the results bucket."
  type        = string
}

variable "runs_retention_days" {
  description = "Days to keep processed per-run files under runs/."
  type        = number
  default     = 90
}

variable "raw_retention_days" {
  description = "Days to keep raw uploads under raw/."
  type        = number
  default     = 730
}

variable "noncurrent_retention_days" {
  description = "Days to keep deleted or overwritten object versions before they are removed for good."
  type        = number
  default     = 7
}

variable "access_logs_retention_days" {
  description = "Days to keep access logs (S3, ALB, CloudFront). These contain IP addresses."
  type        = number
  default     = 90
}

variable "force_destroy" {
  description = "Allow terraform destroy to empty the buckets. Keep false outside throwaway environments."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

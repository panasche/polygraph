variable "name" {
  description = "Instance identifier and name prefix, e.g. polygraph-staging."
  type        = string
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "subnet_ids" {
  description = "Isolated data subnets for the DB subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to connect on 5432, keyed by a static name (e.g. {api = sg-..., worker = sg-...})."
  type        = map(string)
}

variable "kms_key_arn" {
  description = "KMS key for storage, Performance Insights and the master user secret."
  type        = string
}

variable "logs_kms_key_arn" {
  description = "KMS key for the exported PostgreSQL log groups."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL major (or major.minor) version."
  type        = string
  default     = "16"
}

variable "instance_class" {
  description = "Instance class. Graviton (t4g/m7g/r7g) is preferred."
  type        = string
  default     = "db.t4g.micro"
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "polygraph"
}

variable "master_username" {
  description = "Master user name. The password is managed by RDS in Secrets Manager."
  type        = string
  default     = "polygraph_admin"
}

variable "allocated_storage_gb" {
  description = "Initial storage."
  type        = number
  default     = 20
}

variable "max_allocated_storage_gb" {
  description = "Upper bound for storage autoscaling."
  type        = number
  default     = 100
}

variable "multi_az" {
  description = "Run a standby in a second AZ."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Automated backup retention."
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Block deletes of the instance."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on delete. Only for throwaway environments."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "Retention for exported PostgreSQL logs."
  type        = number
  default     = 30
}

variable "apply_immediately" {
  description = "Apply modifications now instead of in the maintenance window."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

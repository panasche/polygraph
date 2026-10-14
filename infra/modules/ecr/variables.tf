variable "name" {
  description = "Repository namespace, e.g. polygraph-staging. Repos are <name>/<image>."
  type        = string
}

variable "repositories" {
  description = "Image names. The worker runs the api image with a different command."
  type        = list(string)
  default     = ["api", "web"]
}

variable "kms_key_arn" {
  description = "KMS key for image encryption."
  type        = string
}

variable "keep_images" {
  description = "How many tagged images to keep per repository (enough for rollbacks)."
  type        = number
  default     = 30
}

variable "pull_principal_arns" {
  description = "Extra IAM principals allowed to pull (e.g. the production deploy role, for image promotion)."
  type        = list(string)
  default     = []
}

variable "force_delete" {
  description = "Allow deleting repositories that still contain images."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

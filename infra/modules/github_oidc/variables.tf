variable "name" {
  description = "Name prefix, e.g. polygraph-staging."
  type        = string
}

variable "create_provider" {
  description = "Create the GitHub OIDC provider. There is one per AWS account; bootstrap normally creates it, so leave false."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "owner/repo allowed to assume the role."
  type        = string
  default     = "polygraph-dev/polygraph"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/repo."
  }
}

variable "github_environment" {
  description = "GitHub Environment whose jobs may assume the role (staging or production)."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.github_environment)
    error_message = "github_environment must be staging or production."
  }
}

variable "ecr_repository_arns" {
  description = "Repositories the role may push to."
  type        = list(string)
}

variable "source_ecr_repository_arns" {
  description = "Repositories the role may pull from to promote images (production: the staging repos)."
  type        = list(string)
  default     = []
}

variable "ecs_cluster_name" {
  description = "ECS cluster name."
  type        = string
}

variable "ecs_cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "ecs_service_arns" {
  description = "Services the role may update."
  type        = list(string)
}

variable "migration_task_family" {
  description = "Task definition family used for the one-off migration task (the API family)."
  type        = string
}

variable "migration_log_group_arn" {
  description = "Log group the migration task writes to (so CI can print it)."
  type        = string
}

variable "pass_role_arns" {
  description = "Task and execution roles the deploy role may hand to ECS."
  type        = list(string)
}

variable "cloudfront_distribution_arn" {
  description = "Distribution the role may invalidate. Null to skip."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

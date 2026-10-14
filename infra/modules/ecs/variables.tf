variable "name" {
  description = "Name prefix, e.g. polygraph-staging. Also the cluster name."
  type        = string
}

# --- Network -----------------------------------------------------------------

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC CIDR (egress to the database and to the API)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for tasks."
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "ALB security group, allowed into api and web."
  type        = string
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix (request-count scaling)."
  type        = string
}

variable "api_target_group_arn" {
  description = "API target group."
  type        = string
}

variable "api_target_group_arn_suffix" {
  description = "API target group ARN suffix."
  type        = string
}

variable "web_target_group_arn" {
  description = "Web target group."
  type        = string
}

variable "web_target_group_arn_suffix" {
  description = "Web target group ARN suffix."
  type        = string
}

# --- Images and containers ---------------------------------------------------

variable "api_image" {
  description = "Full image URI for the API."
  type        = string
}

variable "worker_image" {
  description = "Full image URI for the worker (normally the API image)."
  type        = string
}

variable "web_image" {
  description = "Full image URI for the web app."
  type        = string
}

variable "ecr_repository_arns" {
  description = "Repositories the execution role may pull from."
  type        = list(string)
}

variable "api_port" {
  description = "API container port."
  type        = number
  default     = 8000
}

variable "web_port" {
  description = "Web container port."
  type        = number
  default     = 3000
}

variable "api_command" {
  description = "API start command. Must be set when database_url_shim is on (an entry point replaces the image CMD)."
  type        = list(string)
  default     = ["uvicorn", "polygraph_cloud.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header", "--timeout-graceful-shutdown", "20"]
}

variable "database_url_shim" {
  description = "Wrap api/worker commands in a tiny entry point that builds DATABASE_URL from the DB_* variables."
  type        = bool
  default     = true
}

variable "worker_command" {
  description = "Command that starts the worker."
  type        = list(string)
  default     = ["python", "-m", "polygraph_cloud.worker"]
}

variable "container_user" {
  description = "Force a uid:gid for all containers. Null keeps the image USER (which must be non-root; CI checks this)."
  type        = string
  default     = null
}

variable "readonly_root_filesystem" {
  description = "Mount the container root file system read-only. /tmp (and the Next.js cache for web) stay writable."
  type        = bool
  default     = true
}

variable "web_cache_path" {
  description = "Writable Next.js cache directory inside the web image."
  type        = string
  default     = "/app/.next/cache"
}

variable "api_environment" {
  description = "Plain environment variables for the API."
  type        = map(string)
  default     = {}
}

variable "api_secrets" {
  description = "Env var name to Secrets Manager valueFrom (ARN, or ARN:json-key::) for the API."
  type        = map(string)
  default     = {}
}

variable "worker_environment" {
  description = "Plain environment variables for the worker."
  type        = map(string)
  default     = {}
}

variable "worker_secrets" {
  description = "Env var name to Secrets Manager valueFrom for the worker."
  type        = map(string)
  default     = {}
}

variable "web_environment" {
  description = "Plain environment variables for the web app (no secrets: web has none)."
  type        = map(string)
  default     = {}
}

variable "injected_secret_arns" {
  description = "Secret ARNs (without JSON key suffix) the execution role reads to inject env vars."
  type        = list(string)
}

variable "api_readable_secret_arns" {
  description = "Secret ARNs the API task role may read at runtime."
  type        = list(string)
}

variable "worker_readable_secret_arns" {
  description = "Secret ARNs the worker task role may read at runtime (e.g. the DB secret after rotation)."
  type        = list(string)
  default     = []
}

variable "log_group_names" {
  description = "Log group names keyed api, worker, web, service-connect."
  type        = map(string)

  validation {
    condition     = alltrue([for k in ["api", "worker", "web", "service-connect"] : contains(keys(var.log_group_names), k)])
    error_message = "log_group_names needs keys api, worker, web and service-connect."
  }
}

variable "log_group_arns" {
  description = "Log group ARNs (same keys as log_group_names)."
  type        = map(string)
}

# --- Data plane --------------------------------------------------------------

variable "results_bucket_arn" {
  description = "Results bucket ARN."
  type        = string
}

variable "queue_arn" {
  description = "Upload queue ARN."
  type        = string
}

variable "queue_name" {
  description = "Upload queue name (worker scaling metric)."
  type        = string
}

variable "kms_key_arn" {
  description = "Data KMS key (S3, SQS, secrets, ECR)."
  type        = string
}

# --- Sizing and scaling ------------------------------------------------------

variable "api_cpu" {
  description = "API task CPU units."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "API task memory (MiB)."
  type        = number
  default     = 1024
}

variable "worker_cpu" {
  description = "Worker task CPU units."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Worker task memory (MiB). Presidio needs room for its language model."
  type        = number
  default     = 2048
}

variable "web_cpu" {
  description = "Web task CPU units."
  type        = number
  default     = 512
}

variable "web_memory" {
  description = "Web task memory (MiB)."
  type        = number
  default     = 1024
}

variable "api_min_count" {
  description = "Minimum API tasks."
  type        = number
  default     = 1
}

variable "api_max_count" {
  description = "Maximum API tasks."
  type        = number
  default     = 2
}

variable "worker_min_count" {
  description = "Minimum worker tasks."
  type        = number
  default     = 1
}

variable "worker_max_count" {
  description = "Maximum worker tasks."
  type        = number
  default     = 2
}

variable "web_min_count" {
  description = "Minimum web tasks."
  type        = number
  default     = 1
}

variable "web_max_count" {
  description = "Maximum web tasks."
  type        = number
  default     = 2
}

variable "cpu_target_percent" {
  description = "Target average CPU for api and web scaling."
  type        = number
  default     = 60
}

variable "requests_per_target" {
  description = "Target ALB requests per task per minute for api and web scaling."
  type        = number
  default     = 600
}

variable "worker_backlog_per_task" {
  description = "Target visible messages per running worker task."
  type        = number
  default     = 10
}

variable "enable_execute_command" {
  description = "Allow ECS Exec into tasks. Keep off in production."
  type        = bool
  default     = false
}

variable "scheduled_job_command" {
  description = "Command for a daily job on the worker image (e.g. the retention prune). Empty list = no schedule."
  type        = list(string)
  default     = []
}

variable "scheduled_job_expression" {
  description = "Schedule expression in Asia/Kolkata time."
  type        = string
  default     = "cron(0 3 * * ? *)"
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

# --- DB secret rotation ------------------------------------------------------

variable "restart_on_db_secret_rotation" {
  description = "Deploy a small Lambda that restarts api and worker after the RDS master secret rotates."
  type        = bool
  default     = true
}

variable "db_secret_arn" {
  description = "ARN of the RDS-managed master user secret."
  type        = string
  default     = ""
}

variable "logs_kms_key_arn" {
  description = "KMS key for the rotation Lambda's log group."
  type        = string
}

variable "rotation_restart_log_retention_days" {
  description = "Retention for the rotation Lambda's logs."
  type        = number
  default     = 30
}

# Defaults here are the PRODUCTION sizes. envs/staging/variables.tf has the same
# variables with staging defaults.

# --- Identity ----------------------------------------------------------------

variable "project" {
  description = "Project short name, used as the name prefix."
  type        = string
  default     = "polygraph"
}

variable "environment" {
  description = "Environment name. Also the GitHub Environment allowed to deploy."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  description = "Home region. All user data stays here (DPDP / data residency)."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.region == "ap-south-1"
    error_message = "Polygraph Cloud stores data in ap-south-1 (Mumbai) only."
  }
}

variable "allowed_account_ids" {
  description = "Guard rail: Terraform refuses to run against any other AWS account."
  type        = list(string)
}

variable "github_repository" {
  description = "owner/repo whose GitHub Actions may deploy."
  type        = string
  default     = "polygraph-dev/polygraph"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider here. Leave false when bootstrap created it."
  type        = bool
  default     = false
}

variable "extra_tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

# --- Domain ------------------------------------------------------------------
# Var-driven on purpose: the product name has a pending trademark check.

variable "zone_name" {
  description = "Route 53 public zone (created by bootstrap)."
  type        = string
  default     = "polygraphmcp.dev"
}

variable "create_zone" {
  description = "Create the zone in this environment instead (e.g. a delegated staging subzone in its own account)."
  type        = bool
  default     = false
}

variable "site_domain" {
  description = "Public host name for this environment."
  type        = string
  default     = "polygraphmcp.dev"
}

variable "site_alternative_names" {
  description = "Extra host names on the CloudFront distribution."
  type        = list(string)
  default     = []
}

variable "origin_domain" {
  description = "ALB host name. Null = origin.<site_domain>."
  type        = string
  default     = null
}

variable "oidc_audience" {
  description = "Audience the API expects in GitHub OIDC tokens. Null = https://<site_domain>."
  type        = string
  default     = null
}

# --- App settings ------------------------------------------------------------

variable "image_tag" {
  description = "Image tag Terraform writes into new task definitions. Only matters before the first CI deploy; after that CI sets the image."
  type        = string
  default     = "initial"
}

variable "leaderboard_project" {
  description = "owner/repo whose uploads feed the leaderboard."
  type        = string
  default     = "polygraph-dev/benchmark"
}

variable "privacy_version" {
  description = "Current privacy notice version users consent to."
  type        = string
  default     = "2026-10-13"
}

variable "pii_scanner" {
  description = "presidio or regex."
  type        = string
  default     = "presidio"

  validation {
    condition     = contains(["presidio", "regex"], var.pii_scanner)
    error_message = "pii_scanner must be presidio or regex."
  }
}

variable "log_level" {
  description = "App log level."
  type        = string
  default     = "INFO"
}

variable "worker_command" {
  description = "Worker start command."
  type        = list(string)
  default     = ["python", "-m", "polygraph_cloud.worker"]
}

variable "scheduled_job_command" {
  description = "Daily retention prune command on the worker image. [] disables the schedule."
  type        = list(string)
  default     = ["python", "-m", "polygraph_cloud.jobs.prune"]
}

variable "web_health_check_path" {
  description = "Path the ALB uses to check web tasks."
  type        = string
  default     = "/.well-known/security.txt"
}

variable "github_app_slug" {
  description = "GitHub App slug (github.com/apps/<slug>). Public, not a secret."
  type        = string
  default     = ""
}

# --- Network -----------------------------------------------------------------

variable "vpc_cidr" {
  description = "VPC CIDR (/16)."
  type        = string
  default     = "10.50.0.0/16"
}

variable "az_count" {
  description = "Availability zones."
  type        = number
  default     = 3
}

variable "single_nat_gateway" {
  description = "One NAT gateway for all AZs."
  type        = bool
  default     = false
}

# --- Database ----------------------------------------------------------------

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  description = "Initial storage."
  type        = number
  default     = 50
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling."
  type        = number
  default     = 500
}

variable "db_multi_az" {
  description = "Standby in a second AZ."
  type        = bool
  default     = true
}

variable "db_backup_retention_days" {
  description = "Automated backup retention."
  type        = number
  default     = 30
}

variable "db_deletion_protection" {
  description = "Block deleting the DB."
  type        = bool
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on delete."
  type        = bool
  default     = false
}

# --- Compute -----------------------------------------------------------------

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

variable "api_min_count" {
  description = "Minimum API tasks."
  type        = number
  default     = 2
}

variable "api_max_count" {
  description = "Maximum API tasks."
  type        = number
  default     = 6
}

variable "worker_cpu" {
  description = "Worker task CPU units."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Worker task memory (MiB)."
  type        = number
  default     = 2048
}

variable "worker_min_count" {
  description = "Minimum worker tasks."
  type        = number
  default     = 1
}

variable "worker_max_count" {
  description = "Maximum worker tasks."
  type        = number
  default     = 4
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

variable "web_min_count" {
  description = "Minimum web tasks."
  type        = number
  default     = 2
}

variable "web_max_count" {
  description = "Maximum web tasks."
  type        = number
  default     = 6
}

variable "enable_execute_command" {
  description = "Allow ECS Exec (shell into tasks). Never in production."
  type        = bool
  default     = false
}

# --- Edge --------------------------------------------------------------------

variable "origin_secret_keepers" {
  description = "Change a value to regenerate that origin secret (rotation)."
  type        = map(string)
  default = {
    a = "1"
    b = "1"
  }
}

variable "origin_secret_active" {
  description = "Which origin secret CloudFront sends: a or b."
  type        = string
  default     = "a"

  validation {
    condition     = contains(["a", "b"], var.origin_secret_active)
    error_message = "origin_secret_active must be a or b."
  }
}

variable "waf_upload_rate_limit" {
  description = "Max /api/upload requests per IP per 5 minutes."
  type        = number
  default     = 100
}

variable "waf_global_rate_limit" {
  description = "Max requests per IP per 5 minutes."
  type        = number
  default     = 2000
}

variable "cloudfront_price_class" {
  description = "CloudFront price class (PriceClass_200 includes India)."
  type        = string
  default     = "PriceClass_200"
}

variable "alb_deletion_protection" {
  description = "Block deleting the ALB."
  type        = bool
  default     = true
}

# --- Images ------------------------------------------------------------------

variable "ecr_pull_principal_arns" {
  description = "Principals allowed to pull from this env's repos (staging: the production deploy role, for promotion)."
  type        = list(string)
  default     = []
}

variable "promotion_source_ecr_repository_arns" {
  description = "Repos this env's deploy role may pull from to promote images (production: staging repos)."
  type        = list(string)
  default     = []
}

# --- Operations --------------------------------------------------------------

variable "log_retention_days" {
  description = "Log retention (30 staging, 90 production)."
  type        = number
  default     = 90
}

variable "access_logs_retention_days" {
  description = "Retention for S3/ALB/CloudFront access logs (they hold IP addresses)."
  type        = number
  default     = 90
}

variable "noncurrent_retention_days" {
  description = "Days deleted result objects survive as old versions before removal."
  type        = number
  default     = 7
}

variable "secret_recovery_window_days" {
  description = "Days a deleted secret can be restored."
  type        = number
  default     = 30
}

variable "alarm_emails" {
  description = "Team mailbox(es) for alarms. Use an alias, not a personal address."
  type        = list(string)
  default     = []
}

variable "monthly_budget_usd" {
  description = "Monthly budget in USD (AWS Budgets has no INR). 0 = no budget."
  type        = number
  default     = 0
}

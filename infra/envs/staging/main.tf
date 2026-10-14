# Polygraph Cloud environment.
#
# This file is IDENTICAL in envs/staging and envs/production (CI checks it).
# Everything that differs between the two lives in variables.tf defaults and
# terraform.tfvars.

data "aws_caller_identity" "current" {}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id

  tags = merge(
    {
      Project       = var.project
      Environment   = var.environment
      ManagedBy     = "terraform"
      DataResidency = var.region
      Repository    = var.github_repository
    },
    var.extra_tags,
  )

  public_base_url = "https://${var.site_domain}"
  origin_domain   = coalesce(var.origin_domain, "origin.${var.site_domain}")
  oidc_audience   = coalesce(var.oidc_audience, local.public_base_url)

  api_image = "${module.ecr.repository_urls["api"]}:${var.image_tag}"
  web_image = "${module.ecr.repository_urls["web"]}:${var.image_tag}"

  db_secret_arn = module.database.master_user_secret_arn
  app_secrets   = module.secrets.secret_arns

  # Plain settings shared by the API and the worker (CONTRACTS.md section 6).
  # DATABASE_URL is not set here: the ECS entry point (modules/ecs) builds it from
  # DB_* at start, so the password comes straight from the RDS-managed secret.
  backend_environment = {
    ENVIRONMENT         = var.environment
    PUBLIC_BASE_URL     = local.public_base_url
    STORAGE_BACKEND     = "s3"
    S3_BUCKET           = module.storage.results_bucket_name
    KMS_KEY_ID          = module.kms_data.key_arn
    QUEUE_BACKEND       = "sqs"
    SQS_QUEUE_URL       = module.queue.queue_url
    OIDC_AUDIENCE       = local.oidc_audience
    LEADERBOARD_PROJECT = var.leaderboard_project
    PRIVACY_VERSION     = var.privacy_version
    PII_SCANNER         = var.pii_scanner
    AWS_REGION          = var.region
    AWS_DEFAULT_REGION  = var.region
    LOG_LEVEL           = var.log_level
    DB_HOST             = module.database.address
    DB_PORT             = tostring(module.database.port)
    DB_NAME             = module.database.database_name
    DB_SSLMODE          = "require"
    # Client -> CloudFront -> ALB -> task: the real client IP is 2nd from the right in X-Forwarded-For.
    TRUSTED_PROXY_HOPS = "2"
  }

  backend_secrets = {
    DB_USERNAME              = "${local.db_secret_arn}:username::"
    DB_PASSWORD              = "${local.db_secret_arn}:password::"
    GITHUB_APP_ID            = local.app_secrets["github_app_id"]
    GITHUB_APP_CLIENT_ID     = local.app_secrets["github_app_client_id"]
    GITHUB_APP_CLIENT_SECRET = local.app_secrets["github_app_client_secret"]
    GITHUB_APP_PRIVATE_KEY   = local.app_secrets["github_app_private_key"]
    SESSION_SECRET           = local.app_secrets["session_secret"]
    TOKEN_ENCRYPTION_KEY     = local.app_secrets["token_encryption_key"]
  }

  web_environment = {
    ENVIRONMENT             = var.environment
    PUBLIC_BASE_URL         = local.public_base_url
    PRIVACY_VERSION         = var.privacy_version
    GITHUB_APP_SLUG         = var.github_app_slug
    API_ORIGIN              = "http://api:8000" # Service Connect name, same as docker-compose
    NODE_ENV                = "production"
    PORT                    = "3000"
    HOSTNAME                = "0.0.0.0"
    NEXT_TELEMETRY_DISABLED = "1"
  }
}

# --- Encryption keys ---------------------------------------------------------

module "kms_data" {
  source = "../../modules/kms"

  alias       = "${local.name}-data"
  description = "${local.name}: results bucket, queue, database, secrets, images"
}

module "kms_logs" {
  source = "../../modules/kms"

  alias              = "${local.name}-logs"
  description        = "${local.name}: CloudWatch log groups and the alarm topic"
  logs_service       = true
  service_principals = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
}

module "kms_logs_us_east_1" {
  source = "../../modules/kms"
  providers = {
    aws = aws.us_east_1
  }

  alias        = "${local.name}-waf-logs"
  description  = "${local.name}: WAF logs (CloudFront WAF must log in us-east-1)"
  logs_service = true
}

# --- Network and data --------------------------------------------------------

module "network" {
  source = "../../modules/network"

  name                     = local.name
  cidr_block               = var.vpc_cidr
  az_count                 = var.az_count
  single_nat_gateway       = var.single_nat_gateway
  logs_kms_key_arn         = module.kms_logs.key_arn
  flow_logs_retention_days = var.log_retention_days
}

module "storage" {
  source = "../../modules/storage"

  name                       = local.name
  kms_key_arn                = module.kms_data.key_arn
  runs_retention_days        = 90
  raw_retention_days         = 730
  noncurrent_retention_days  = var.noncurrent_retention_days
  access_logs_retention_days = var.access_logs_retention_days
}

module "queue" {
  source = "../../modules/queue"

  name        = local.name
  kms_key_arn = module.kms_data.key_arn
}

module "secrets" {
  source = "../../modules/secrets"

  name                    = local.name
  kms_key_arn             = module.kms_data.key_arn
  recovery_window_in_days = var.secret_recovery_window_days
}

module "database" {
  source = "../../modules/database"

  name                     = local.name
  vpc_id                   = module.network.vpc_id
  subnet_ids               = module.network.data_subnet_ids
  kms_key_arn              = module.kms_data.key_arn
  logs_kms_key_arn         = module.kms_logs.key_arn
  instance_class           = var.db_instance_class
  allocated_storage_gb     = var.db_allocated_storage_gb
  max_allocated_storage_gb = var.db_max_allocated_storage_gb
  multi_az                 = var.db_multi_az
  backup_retention_days    = var.db_backup_retention_days
  deletion_protection      = var.db_deletion_protection
  skip_final_snapshot      = var.db_skip_final_snapshot
  log_retention_days       = var.log_retention_days

  allowed_security_group_ids = {
    api    = module.ecs.security_group_ids["api"]
    worker = module.ecs.security_group_ids["worker"]
  }
}

module "ecr" {
  source = "../../modules/ecr"

  name                = local.name
  repositories        = ["api", "web"]
  kms_key_arn         = module.kms_data.key_arn
  pull_principal_arns = var.ecr_pull_principal_arns
}

module "observability" {
  source = "../../modules/observability"

  name                        = local.name
  environment                 = var.environment
  account_id                  = local.account_id
  logs_kms_key_arn            = module.kms_logs.key_arn
  log_retention_days          = var.log_retention_days
  alarm_emails                = var.alarm_emails
  alb_arn_suffix              = module.alb.alb_arn_suffix
  api_target_group_arn_suffix = module.alb.api_target_group_arn_suffix
  web_target_group_arn_suffix = module.alb.web_target_group_arn_suffix
  queue_name                  = module.queue.queue_name
  dlq_name                    = module.queue.dlq_name
  db_instance_id              = module.database.instance_id
  monthly_budget_usd          = var.monthly_budget_usd
}

# --- Edge: DNS, certificates, WAF, CloudFront, ALB ---------------------------

# Two origin secrets so one can be rotated without a gap: the ALB accepts both,
# CloudFront sends the active one. To rotate: bump the keeper of the inactive
# secret, apply, switch origin_secret_active to it, apply.
resource "random_password" "origin_secret" {
  for_each = toset(["a", "b"])

  length  = 48
  special = false

  keepers = {
    rotation = var.origin_secret_keepers[each.key]
  }
}

module "waf" {
  source = "../../modules/waf"
  providers = {
    aws = aws.us_east_1
  }

  name               = local.name
  upload_rate_limit  = var.waf_upload_rate_limit
  global_rate_limit  = var.waf_global_rate_limit
  logs_kms_key_arn   = module.kms_logs_us_east_1.key_arn
  log_retention_days = var.log_retention_days
}

module "dns" {
  source = "../../modules/dns"
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  zone_name                 = var.zone_name
  create_zone               = var.create_zone
  site_domain               = var.site_domain
  site_alternative_names    = var.site_alternative_names
  origin_domain             = local.origin_domain
  cloudfront_domain_name    = module.edge.domain_name
  cloudfront_hosted_zone_id = module.edge.hosted_zone_id
  alb_dns_name              = module.alb.alb_dns_name
  alb_zone_id               = module.alb.alb_zone_id
}

module "alb" {
  source = "../../modules/alb"

  name                  = local.name
  vpc_id                = module.network.vpc_id
  vpc_cidr_block        = module.network.vpc_cidr_block
  public_subnet_ids     = module.network.public_subnet_ids
  certificate_arn       = module.dns.origin_certificate_arn
  origin_verify_secrets = [for k in ["a", "b"] : random_password.origin_secret[k].result]
  logs_bucket_name      = module.storage.logs_bucket_name
  deletion_protection   = var.alb_deletion_protection
  web_health_check_path = var.web_health_check_path
}

module "edge" {
  source = "../../modules/edge"

  name                    = local.name
  aliases                 = concat([var.site_domain], var.site_alternative_names)
  certificate_arn         = module.dns.site_certificate_arn
  origin_domain_name      = local.origin_domain
  origin_verify_secret    = random_password.origin_secret[var.origin_secret_active].result
  web_acl_arn             = module.waf.web_acl_arn
  logs_bucket_domain_name = module.storage.logs_bucket_domain_name
  price_class             = var.cloudfront_price_class
}

# --- Compute -----------------------------------------------------------------

module "ecs" {
  source = "../../modules/ecs"

  name                        = local.name
  vpc_id                      = module.network.vpc_id
  vpc_cidr_block              = module.network.vpc_cidr_block
  private_subnet_ids          = module.network.private_subnet_ids
  alb_security_group_id       = module.alb.security_group_id
  alb_arn_suffix              = module.alb.alb_arn_suffix
  api_target_group_arn        = module.alb.api_target_group_arn
  api_target_group_arn_suffix = module.alb.api_target_group_arn_suffix
  web_target_group_arn        = module.alb.web_target_group_arn
  web_target_group_arn_suffix = module.alb.web_target_group_arn_suffix

  api_image           = local.api_image
  worker_image        = local.api_image
  web_image           = local.web_image
  ecr_repository_arns = values(module.ecr.repository_arns)
  worker_command      = var.worker_command

  api_environment    = local.backend_environment
  api_secrets        = local.backend_secrets
  worker_environment = local.backend_environment
  worker_secrets     = local.backend_secrets
  web_environment    = local.web_environment

  injected_secret_arns        = concat(values(local.app_secrets), [local.db_secret_arn])
  api_readable_secret_arns    = concat(values(local.app_secrets), [local.db_secret_arn])
  worker_readable_secret_arns = [local.db_secret_arn]

  log_group_names = module.observability.log_group_names
  log_group_arns  = module.observability.log_group_arns

  results_bucket_arn = module.storage.results_bucket_arn
  queue_arn          = module.queue.queue_arn
  queue_name         = module.queue.queue_name
  kms_key_arn        = module.kms_data.key_arn
  logs_kms_key_arn   = module.kms_logs.key_arn

  api_cpu          = var.api_cpu
  api_memory       = var.api_memory
  api_min_count    = var.api_min_count
  api_max_count    = var.api_max_count
  worker_cpu       = var.worker_cpu
  worker_memory    = var.worker_memory
  worker_min_count = var.worker_min_count
  worker_max_count = var.worker_max_count
  web_cpu          = var.web_cpu
  web_memory       = var.web_memory
  web_min_count    = var.web_min_count
  web_max_count    = var.web_max_count

  enable_execute_command = var.enable_execute_command
  scheduled_job_command  = var.scheduled_job_command

  restart_on_db_secret_rotation       = true
  db_secret_arn                       = local.db_secret_arn
  rotation_restart_log_retention_days = var.log_retention_days
}

# --- CI/CD -------------------------------------------------------------------

module "github_oidc" {
  source = "../../modules/github_oidc"

  name                        = local.name
  create_provider             = var.create_github_oidc_provider
  github_repository           = var.github_repository
  github_environment          = var.environment
  ecr_repository_arns         = values(module.ecr.repository_arns)
  source_ecr_repository_arns  = var.promotion_source_ecr_repository_arns
  ecs_cluster_name            = module.ecs.cluster_name
  ecs_cluster_arn             = module.ecs.cluster_arn
  ecs_service_arns            = values(module.ecs.service_arns)
  migration_task_family       = module.ecs.task_definition_families["api"]
  migration_log_group_arn     = module.observability.log_group_arns["api"]
  pass_role_arns              = concat([module.ecs.execution_role_arn], values(module.ecs.task_role_arns))
  cloudfront_distribution_arn = module.edge.distribution_arn
}

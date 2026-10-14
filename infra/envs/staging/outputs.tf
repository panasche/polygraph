output "site_url" {
  description = "Public URL of this environment."
  value       = local.public_base_url
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id."
  value       = module.edge.distribution_id
}

output "name_servers" {
  description = "Set these at the registrar if this env created the zone."
  value       = module.dns.name_servers
}

output "ecr_repository_urls" {
  description = "Image repositories."
  value       = module.ecr.repository_urls
}

output "ecr_repository_arns" {
  description = "Image repository ARNs (feed staging's into production's promotion_source_ecr_repository_arns)."
  value       = module.ecr.repository_arns
}

output "deploy_role_arn" {
  description = "Role GitHub Actions assumes in this environment."
  value       = module.github_oidc.deploy_role_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster."
  value       = module.ecs.cluster_name
}

output "ecs_service_names" {
  description = "ECS services."
  value       = module.ecs.service_names
}

output "secret_names" {
  description = "Secrets Manager names to fill with infra/scripts/set-secrets.sh."
  value       = module.secrets.secret_names
}

output "results_bucket" {
  description = "Results bucket."
  value       = module.storage.results_bucket_name
}

output "alarm_topic_arn" {
  description = "SNS topic for alarms."
  value       = module.observability.alarm_topic_arn
}

output "nat_public_ips" {
  description = "Outbound IPs of the tasks."
  value       = module.network.nat_public_ips
}

# Copy these into the GitHub Environment of the same name
# (infra/scripts/sync-github-env.sh does it with the gh CLI).
output "github_environment_variables" {
  description = "Variables the deploy workflow reads from the GitHub Environment."
  value = {
    AWS_REGION                 = var.region
    AWS_DEPLOY_ROLE_ARN        = module.github_oidc.deploy_role_arn
    ECR_API_REPOSITORY         = module.ecr.repository_urls["api"]
    ECR_WEB_REPOSITORY         = module.ecr.repository_urls["web"]
    ECS_CLUSTER                = module.ecs.cluster_name
    ECS_SERVICE_API            = module.ecs.service_names["api"]
    ECS_SERVICE_WORKER         = module.ecs.service_names["worker"]
    ECS_SERVICE_WEB            = module.ecs.service_names["web"]
    PUBLIC_BASE_URL            = local.public_base_url
    CLOUDFRONT_DISTRIBUTION_ID = module.edge.distribution_id
  }
}

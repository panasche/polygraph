output "deploy_role_arn" {
  description = "Role ARN for aws-actions/configure-aws-credentials (GitHub Environment variable AWS_DEPLOY_ROLE_ARN)."
  value       = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider ARN."
  value       = local.provider_arn
}

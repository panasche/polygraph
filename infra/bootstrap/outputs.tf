output "state_bucket" {
  description = "Put this in envs/*/backend.hcl as bucket."
  value       = aws_s3_bucket.state.id
}

output "state_kms_key_arn" {
  description = "KMS key encrypting the state bucket."
  value       = aws_kms_key.state.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN (empty if not created here)."
  value       = one(aws_iam_openid_connect_provider.github[*].arn)
}

output "name_servers" {
  description = "Set these as the domain's name servers at the registrar."
  value       = var.create_hosted_zone ? aws_route53_zone.apex[0].name_servers : []
}

output "zone_id" {
  description = "Route 53 zone id."
  value       = local.zone_id
}

output "name_servers" {
  description = "Name servers to set at the registrar (only when the zone is created here)."
  value       = var.create_zone ? aws_route53_zone.this[0].name_servers : []
}

output "site_certificate_arn" {
  description = "Validated us-east-1 certificate for CloudFront."
  value       = aws_acm_certificate_validation.site.certificate_arn
}

output "origin_certificate_arn" {
  description = "Validated regional certificate for the ALB."
  value       = aws_acm_certificate_validation.origin.certificate_arn
}

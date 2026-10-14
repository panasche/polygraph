output "web_acl_arn" {
  description = "Web ACL ARN (attach to CloudFront)."
  value       = aws_wafv2_web_acl.this.arn
}

output "web_acl_name" {
  description = "Web ACL name."
  value       = aws_wafv2_web_acl.this.name
}

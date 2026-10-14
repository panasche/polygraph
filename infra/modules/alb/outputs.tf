output "alb_arn" {
  description = "ALB ARN."
  value       = aws_lb.this.arn
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix for CloudWatch metrics."
  value       = aws_lb.this.arn_suffix
}

output "alb_dns_name" {
  description = "ALB DNS name."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone id (for alias records)."
  value       = aws_lb.this.zone_id
}

output "security_group_id" {
  description = "ALB security group."
  value       = aws_security_group.alb.id
}

output "api_target_group_arn" {
  description = "API target group ARN."
  value       = aws_lb_target_group.api.arn
}

output "api_target_group_arn_suffix" {
  description = "API target group ARN suffix (metrics, request-count scaling)."
  value       = aws_lb_target_group.api.arn_suffix
}

output "web_target_group_arn" {
  description = "Web target group ARN."
  value       = aws_lb_target_group.web.arn
}

output "web_target_group_arn_suffix" {
  description = "Web target group ARN suffix."
  value       = aws_lb_target_group.web.arn_suffix
}

output "https_listener_arn" {
  description = "HTTPS listener ARN."
  value       = aws_lb_listener.https.arn
}

variable "zone_name" {
  description = "Route 53 public zone, e.g. polygraphmcp.dev."
  type        = string
}

variable "create_zone" {
  description = "Create the zone here. Normally false: bootstrap creates the apex zone once per account."
  type        = bool
  default     = false
}

variable "site_domain" {
  description = "Public host name for this environment, e.g. polygraphmcp.dev or staging.polygraphmcp.dev."
  type        = string
}

variable "site_alternative_names" {
  description = "Extra names on the CloudFront certificate (e.g. www.polygraphmcp.dev)."
  type        = list(string)
  default     = []
}

variable "origin_domain" {
  description = "Host name that points at the ALB, e.g. origin.polygraphmcp.dev."
  type        = string
}

variable "caa_report_email" {
  description = "Where CAs send CAA violation reports (only used when create_zone is true)."
  type        = string
  default     = "security@polygraphmcp.dev"
}

variable "create_alias_records" {
  description = "Create the site and origin alias records."
  type        = bool
  default     = true
}

variable "cloudfront_domain_name" {
  description = "CloudFront distribution domain name."
  type        = string
}

variable "cloudfront_hosted_zone_id" {
  description = "CloudFront hosted zone id."
  type        = string
}

variable "alb_dns_name" {
  description = "ALB DNS name."
  type        = string
}

variable "alb_zone_id" {
  description = "ALB hosted zone id."
  type        = string
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

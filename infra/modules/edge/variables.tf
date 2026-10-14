variable "name" {
  description = "Name prefix, e.g. polygraph-staging."
  type        = string
}

variable "aliases" {
  description = "Public host names served by the distribution, e.g. [\"polygraphmcp.dev\"]."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate in us-east-1 covering the aliases."
  type        = string
}

variable "origin_domain_name" {
  description = "Host name that points at the ALB and matches its certificate, e.g. origin.polygraphmcp.dev."
  type        = string
}

variable "origin_verify_header_name" {
  description = "Secret header name the ALB checks."
  type        = string
  default     = "X-Origin-Verify"
}

variable "origin_verify_secret" {
  description = "Current secret header value sent to the origin."
  type        = string
  sensitive   = true
}

variable "web_acl_arn" {
  description = "WAF web ACL (CLOUDFRONT scope) ARN."
  type        = string
}

variable "logs_bucket_domain_name" {
  description = "Regional domain name of the access-logs bucket."
  type        = string
}

variable "price_class" {
  description = "CloudFront price class. PriceClass_200 includes India."
  type        = string
  default     = "PriceClass_200"
}

variable "web_default_ttl" {
  description = "Seconds to cache web responses that send no Cache-Control."
  type        = number
  default     = 60
}

variable "session_cookie_names" {
  description = "Cookies that make a web response user-specific (part of the cache key)."
  type        = list(string)
  default     = ["pg_session", "pg_csrf"]
}

variable "content_security_policy" {
  description = "Fallback CSP, used only when the origin sends none. The web app sets the real one."
  type        = string
  default     = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://avatars.githubusercontent.com; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://github.com; object-src 'none'"
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

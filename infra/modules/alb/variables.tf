variable "name" {
  description = "Name prefix, e.g. polygraph-staging (ALB names are cut to 32 chars)."
  type        = string
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC CIDR, used for egress to the task subnets."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB."
  type        = list(string)
}

variable "certificate_arn" {
  description = "Regional ACM certificate for the origin host name (e.g. origin.polygraphmcp.dev)."
  type        = string
}

variable "ssl_policy" {
  description = "Listener TLS policy."
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
}

variable "origin_verify_header_name" {
  description = "Header CloudFront adds to every origin request."
  type        = string
  default     = "X-Origin-Verify"
}

variable "origin_verify_secrets" {
  description = "Accepted values for the origin header. Keep two during a rotation, one otherwise."
  type        = list(string)
  sensitive   = true

  validation {
    condition     = length(var.origin_verify_secrets) >= 1 && length(var.origin_verify_secrets) <= 2
    error_message = "Provide one or two origin secrets."
  }
}

variable "api_port" {
  description = "API container port."
  type        = number
  default     = 8000
}

variable "web_port" {
  description = "Web container port."
  type        = number
  default     = 3000
}

variable "api_health_check_path" {
  description = "API target group health check path."
  type        = string
  default     = "/api/health"
}

variable "web_health_check_path" {
  description = "Web target group health check path."
  type        = string
  default     = "/.well-known/security.txt"
}

variable "logs_bucket_name" {
  description = "Bucket for ALB access logs (prefix alb/)."
  type        = string
}

variable "deletion_protection" {
  description = "Block ALB deletion."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

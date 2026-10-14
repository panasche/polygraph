variable "region" {
  description = "Region for the state bucket."
  type        = string
  default     = "ap-south-1"
}

variable "allowed_account_ids" {
  description = "Guard rail: refuse to run against any other account."
  type        = list(string)
}

variable "state_bucket_prefix" {
  description = "State bucket name prefix; the account id is appended."
  type        = string
  default     = "polygraph-tfstate"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub Actions OIDC provider. Set false if the account already has one."
  type        = bool
  default     = true
}

variable "create_hosted_zone" {
  description = "Create the Route 53 zone for zone_name."
  type        = bool
  default     = true
}

variable "zone_name" {
  description = "Apex domain. Var-driven because the name has a pending trademark check."
  type        = string
  default     = "polygraphmcp.dev"
}

variable "security_contact" {
  description = "Mailbox for CAA violation reports."
  type        = string
  default     = "security@polygraphmcp.dev"
}

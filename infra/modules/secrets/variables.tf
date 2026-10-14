variable "name" {
  description = "Secret name prefix, e.g. polygraph-staging. Secrets are <name>/<key>."
  type        = string
}

variable "secrets" {
  description = "Map of secret key (lower snake case of the env var) to description."
  type        = map(string)
  default = {
    github_app_id            = "GitHub App id (GITHUB_APP_ID)"
    github_app_client_id     = "GitHub App client id (GITHUB_APP_CLIENT_ID)"
    github_app_client_secret = "GitHub App client secret (GITHUB_APP_CLIENT_SECRET)"
    github_app_private_key   = "GitHub App private key, PEM (GITHUB_APP_PRIVATE_KEY)"
    session_secret           = "32+ random bytes that sign state cookies (SESSION_SECRET)"
    token_encryption_key     = "Fernet key that encrypts GitHub refresh tokens (TOKEN_ENCRYPTION_KEY)"
  }

  validation {
    condition     = alltrue([for k in keys(var.secrets) : can(regex("^[a-z0-9_]+$", k))])
    error_message = "Secret keys must be lower snake case."
  }
}

variable "kms_key_arn" {
  description = "KMS key that encrypts the secrets."
  type        = string
}

variable "recovery_window_in_days" {
  description = "Days a deleted secret can still be restored."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

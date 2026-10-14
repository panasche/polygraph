variable "name" {
  description = "Name prefix, e.g. polygraph-staging."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for server-side encryption of both queues."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a received message stays hidden. Must be longer than the worker's slowest job."
  type        = number
  default     = 300
}

variable "message_retention_seconds" {
  description = "How long an unprocessed message is kept on the main queue."
  type        = number
  default     = 345600 # 4 days
}

variable "max_receive_count" {
  description = "Failed receives before a message moves to the DLQ."
  type        = number
  default     = 5
}

variable "tags" {
  description = "Tags for all resources."
  type        = map(string)
  default     = {}
}

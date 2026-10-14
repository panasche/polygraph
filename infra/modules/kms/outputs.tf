output "key_arn" {
  description = "ARN of the key."
  value       = aws_kms_key.this.arn
}

output "key_id" {
  description = "Key id."
  value       = aws_kms_key.this.key_id
}

output "alias_arn" {
  description = "ARN of the alias."
  value       = aws_kms_alias.this.arn
}

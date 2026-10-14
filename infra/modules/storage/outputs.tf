output "results_bucket_name" {
  description = "Results bucket name (S3_BUCKET)."
  value       = aws_s3_bucket.results.id
}

output "results_bucket_arn" {
  description = "Results bucket ARN."
  value       = aws_s3_bucket.results.arn
}

output "logs_bucket_name" {
  description = "Access logs bucket name."
  value       = aws_s3_bucket.logs.id
}

output "logs_bucket_arn" {
  description = "Access logs bucket ARN."
  value       = aws_s3_bucket.logs.arn
}

output "logs_bucket_domain_name" {
  description = "Regional domain name of the logs bucket (for CloudFront logging)."
  value       = aws_s3_bucket.logs.bucket_regional_domain_name
}

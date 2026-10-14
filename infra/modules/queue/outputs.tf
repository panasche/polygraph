output "queue_url" {
  description = "Main queue URL (SQS_QUEUE_URL)."
  value       = aws_sqs_queue.this.id
}

output "queue_arn" {
  description = "Main queue ARN."
  value       = aws_sqs_queue.this.arn
}

output "queue_name" {
  description = "Main queue name."
  value       = aws_sqs_queue.this.name
}

output "dlq_url" {
  description = "Dead-letter queue URL."
  value       = aws_sqs_queue.dlq.id
}

output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "Dead-letter queue name."
  value       = aws_sqs_queue.dlq.name
}

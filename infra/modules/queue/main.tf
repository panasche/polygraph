# Upload queue plus dead-letter queue. Messages are {"report_id": "rep_..."}
# (CONTRACTS.md section 5). A message that fails max_receive_count times moves
# to the DLQ, which raises an alarm.

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name}-uploads-dlq"
  message_retention_seconds         = 1209600 # 14 days, the SQS maximum
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  tags = var.tags
}

resource "aws_sqs_queue" "this" {
  name                              = "${var.name}-uploads"
  visibility_timeout_seconds        = var.visibility_timeout_seconds
  message_retention_seconds         = var.message_retention_seconds
  receive_wait_time_seconds         = 20
  max_message_size                  = 4096
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = var.tags
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.this.arn]
  })
}

data "aws_iam_policy_document" "tls_only" {
  for_each = {
    main = aws_sqs_queue.this.arn
    dlq  = aws_sqs_queue.dlq.arn
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [each.value]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "main" {
  queue_url = aws_sqs_queue.this.id
  policy    = data.aws_iam_policy_document.tls_only["main"].json
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.tls_only["dlq"].json
}

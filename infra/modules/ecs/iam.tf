# IAM for the ECS services.
#
#   execution role - used by ECS itself: pull images, write logs, read the
#                    secrets that get injected as environment variables.
#   task roles     - used by the app code. One per service, least privilege:
#                    api    -> S3 put/get/delete, SQS send, KMS, secrets read
#                    worker -> S3 get/put/delete, SQS receive/delete, KMS
#                    web    -> nothing (only ECS Exec when it is switched on)

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:ecs:${local.region}:${local.account_id}:*"]
    }
  }
}

# --- Execution role ----------------------------------------------------------

resource "aws_iam_role" "execution" {
  name               = "${var.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for arn in values(var.log_group_arns) : "${arn}:*"]
  }

  statement {
    sid       = "InjectSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.injected_secret_arns
  }

  statement {
    sid       = "DecryptSecretsAndImages"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "ecs-execution"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# --- Shared statements -------------------------------------------------------

data "aws_iam_policy_document" "exec_command" {
  statement {
    sid = "EcsExec"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

# --- API task role -----------------------------------------------------------

resource "aws_iam_role" "api" {
  name               = "${var.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ResultsObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.results_bucket_arn}/raw/*", "${var.results_bucket_arn}/runs/*"]
  }

  statement {
    sid       = "ResultsList"
    actions   = ["s3:ListBucket"]
    resources = [var.results_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["raw/*", "runs/*"]
    }
  }

  statement {
    sid       = "EnqueueUploads"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "Kms"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "ReadSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = var.api_readable_secret_arns
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_role_policy" "api_exec" {
  count = var.enable_execute_command ? 1 : 0

  name   = "ecs-exec"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.exec_command.json
}

# --- Worker task role --------------------------------------------------------

resource "aws_iam_role" "worker" {
  name               = "${var.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ResultsObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.results_bucket_arn}/raw/*", "${var.results_bucket_arn}/runs/*"]
  }

  statement {
    sid       = "ResultsList"
    actions   = ["s3:ListBucket"]
    resources = [var.results_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["raw/*", "runs/*"]
    }
  }

  statement {
    sid = "ConsumeUploads"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }

  dynamic "statement" {
    for_each = length(var.worker_readable_secret_arns) > 0 ? [1] : []

    content {
      sid       = "ReadSecrets"
      actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      resources = var.worker_readable_secret_arns
    }
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_role_policy" "worker_exec" {
  count = var.enable_execute_command ? 1 : 0

  name   = "ecs-exec"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.exec_command.json
}

# --- Web task role (no AWS permissions) --------------------------------------

resource "aws_iam_role" "web" {
  name               = "${var.name}-web-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json

  tags = var.tags
}

resource "aws_iam_role_policy" "web_exec" {
  count = var.enable_execute_command ? 1 : 0

  name   = "ecs-exec"
  role   = aws_iam_role.web.id
  policy = data.aws_iam_policy_document.exec_command.json
}

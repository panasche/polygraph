# Restart api and worker after RDS rotates the master password.
#
# ECS injects the DB password into the container environment when a task
# starts. After a rotation, new connections from old tasks would fail. This
# small function listens for the Secrets Manager RotationSucceeded event and
# forces a fresh deployment of the services that talk to the database. The
# deployment is rolling (minimum healthy 100%), so there is no downtime.

locals {
  rotation_enabled = var.restart_on_db_secret_rotation
}

data "archive_file" "rotation_restart" {
  count = local.rotation_enabled ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/.build/rotation-restart.zip"

  source {
    filename = "index.py"
    content  = <<-PY
      import json
      import os

      import boto3

      ecs = boto3.client("ecs")


      def handler(event, _context):
          # Only react to rotations of our own secret.
          if os.environ["SECRET_ARN"] not in json.dumps(event):
              return {"restarted": []}
          restarted = []
          for service in os.environ["SERVICES"].split(","):
              ecs.update_service(
                  cluster=os.environ["CLUSTER"],
                  service=service,
                  forceNewDeployment=True,
              )
              restarted.append(service)
          print(json.dumps({"event": "db_secret_rotated", "restarted": restarted}))
          return {"restarted": restarted}
    PY
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "rotation_restart" {
  count = local.rotation_enabled ? 1 : 0

  name              = "/aws/lambda/${var.name}-db-rotation-restart"
  retention_in_days = var.rotation_restart_log_retention_days
  kms_key_id        = var.logs_kms_key_arn

  tags = var.tags
}

resource "aws_iam_role" "rotation_restart" {
  count = local.rotation_enabled ? 1 : 0

  name               = "${var.name}-db-rotation-restart"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "rotation_restart" {
  count = local.rotation_enabled ? 1 : 0

  statement {
    sid       = "RestartServices"
    actions   = ["ecs:UpdateService"]
    resources = [aws_ecs_service.api.id, aws_ecs_service.worker.id]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.rotation_restart[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "rotation_restart" {
  count = local.rotation_enabled ? 1 : 0

  name   = "restart-services"
  role   = aws_iam_role.rotation_restart[0].id
  policy = data.aws_iam_policy_document.rotation_restart[0].json
}

resource "aws_lambda_function" "rotation_restart" {
  count = local.rotation_enabled ? 1 : 0

  #checkov:skip=CKV_AWS_117:Only calls the public ECS API; no VPC access needed.
  #checkov:skip=CKV_AWS_116:Event-driven and idempotent; EventBridge retries on failure.
  #checkov:skip=CKV_AWS_50:X-Ray tracing is not useful for a one-call function.
  #checkov:skip=CKV_AWS_272:Code is defined inline in this module and reviewed with it.
  #checkov:skip=CKV_AWS_173:Environment holds only resource names, no secrets.
  function_name                  = "${var.name}-db-rotation-restart"
  description                    = "Force a new ECS deployment of api and worker after the DB secret rotates"
  role                           = aws_iam_role.rotation_restart[0].arn
  runtime                        = "python3.13"
  architectures                  = ["arm64"]
  handler                        = "index.handler"
  filename                       = data.archive_file.rotation_restart[0].output_path
  source_code_hash               = data.archive_file.rotation_restart[0].output_base64sha256
  timeout                        = 30
  memory_size                    = 128
  reserved_concurrent_executions = 1

  environment {
    variables = {
      CLUSTER    = aws_ecs_cluster.this.name
      SERVICES   = join(",", [aws_ecs_service.api.name, aws_ecs_service.worker.name])
      SECRET_ARN = var.db_secret_arn
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.rotation_restart[0].name
  }

  tags = var.tags
}

resource "aws_cloudwatch_event_rule" "db_secret_rotated" {
  count = local.rotation_enabled ? 1 : 0

  name        = "${var.name}-db-secret-rotated"
  description = "Secrets Manager finished rotating a secret"

  event_pattern = jsonencode({
    source        = ["aws.secretsmanager"]
    "detail-type" = ["AWS Service Event via CloudTrail"]
    detail = {
      eventName = ["RotationSucceeded"]
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "db_secret_rotated" {
  count = local.rotation_enabled ? 1 : 0

  rule = aws_cloudwatch_event_rule.db_secret_rotated[0].name
  arn  = aws_lambda_function.rotation_restart[0].arn
}

resource "aws_lambda_permission" "events" {
  count = local.rotation_enabled ? 1 : 0

  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rotation_restart[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.db_secret_rotated[0].arn
}

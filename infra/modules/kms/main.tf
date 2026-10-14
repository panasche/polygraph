# One customer managed KMS key with rotation on.
#
# IAM policies in the account decide who may use the key (the "root" statement
# below delegates to IAM). AWS services that act on their own behalf (CloudWatch
# Logs, CloudWatch alarms, Budgets) need a direct statement, which is what
# `logs_service` and `service_principals` add.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
}

data "aws_iam_policy_document" "key" {
  #checkov:skip=CKV_AWS_109:Key policies must name Resource "*" (the key itself); root delegates to IAM.
  #checkov:skip=CKV_AWS_111:Key policies must name Resource "*" (the key itself); root delegates to IAM.
  #checkov:skip=CKV_AWS_356:Key policies must name Resource "*" (the key itself).
  statement {
    sid       = "EnableIamPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  dynamic "statement" {
    for_each = var.logs_service ? [1] : []

    content {
      sid    = "AllowCloudWatchLogs"
      effect = "Allow"
      actions = [
        "kms:Encrypt*",
        "kms:Decrypt*",
        "kms:ReEncrypt*",
        "kms:GenerateDataKey*",
        "kms:Describe*",
      ]
      resources = ["*"]

      principals {
        type        = "Service"
        identifiers = ["logs.${local.region}.amazonaws.com"]
      }

      condition {
        test     = "ArnLike"
        variable = "kms:EncryptionContext:aws:logs:arn"
        values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"]
      }
    }
  }

  dynamic "statement" {
    for_each = length(var.service_principals) > 0 ? [1] : []

    content {
      sid       = "AllowServicePublishers"
      effect    = "Allow"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
      resources = ["*"]

      principals {
        type        = "Service"
        identifiers = var.service_principals
      }

      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account_id]
      }
    }
  }
}

resource "aws_kms_key" "this" {
  description             = var.description
  enable_key_rotation     = true
  rotation_period_in_days = var.rotation_period_in_days
  deletion_window_in_days = var.deletion_window_in_days
  multi_region            = false
  policy                  = data.aws_iam_policy_document.key.json

  tags = var.tags
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.alias}"
  target_key_id = aws_kms_key.this.key_id
}

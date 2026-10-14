# Two buckets:
#   results     - raw uploads and per-run files. SSE-KMS, versioned, TLS only.
#   access-logs - S3 server access logs, ALB logs and CloudFront logs.
#                 Log delivery only supports SSE-S3, so this bucket uses AES256.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_elb_service_account" "this" {}
data "aws_canonical_user_id" "current" {}

locals {
  account_id     = data.aws_caller_identity.current.account_id
  results_bucket = "${var.name}-results-${local.account_id}"
  logs_bucket    = "${var.name}-access-logs-${local.account_id}"

  # Canonical id of the awslogsdelivery account that writes CloudFront standard logs.
  cloudfront_log_delivery_canonical_id = "c4c1ede66af53448b93c283ce9448c4ba468c9432aa01d700d3878632f77d2d0"
}

# =============================================================================
# Access logs bucket
# =============================================================================

resource "aws_s3_bucket" "logs" {
  #checkov:skip=CKV_AWS_145:Log delivery (S3 server access logs, ALB, CloudFront) only supports SSE-S3.
  #checkov:skip=CKV_AWS_144:Cross-region replication is not needed for operational logs.
  #checkov:skip=CKV2_AWS_62:No event notifications needed on the log bucket.
  #checkov:skip=CKV_AWS_18:This is the access-log target itself.
  bucket        = local.logs_bucket
  force_destroy = var.force_destroy

  tags = merge(var.tags, { Name = local.logs_bucket, DataClass = "operational-logs" })
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket = aws_s3_bucket.logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CloudFront standard logging still writes through an ACL grant, so ACLs stay enabled here.
resource "aws_s3_bucket_ownership_controls" "logs" {
  #checkov:skip=CKV2_AWS_65:CloudFront standard logging writes through an ACL grant, so ACLs stay on for this log bucket only.
  bucket = aws_s3_bucket.logs.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "logs" {
  bucket = aws_s3_bucket.logs.id

  access_control_policy {
    owner {
      id = data.aws_canonical_user_id.current.id
    }

    grant {
      grantee {
        id   = data.aws_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }

    grant {
      grantee {
        id   = local.cloudfront_log_delivery_canonical_id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }
  }

  depends_on = [aws_s3_bucket_ownership_controls.logs]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.access_logs_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "logs" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.logs.arn,
      "${aws_s3_bucket.logs.arn}/*",
    ]

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

  statement {
    sid       = "S3ServerAccessLogs"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs.arn}/s3/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  # ap-south-1 is an older region, so ALB logs come from the regional ELB account.
  statement {
    sid       = "AlbAccessLogs"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs.arn}/alb/AWSLogs/${local.account_id}/*"]

    principals {
      type        = "AWS"
      identifiers = [data.aws_elb_service_account.this.arn]
    }
  }

  statement {
    sid       = "AlbAccessLogsDeliveryService"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs.arn}/alb/AWSLogs/${local.account_id}/*"]

    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.logs.json

  depends_on = [aws_s3_bucket_public_access_block.logs]
}

# =============================================================================
# Results bucket
# =============================================================================

resource "aws_s3_bucket" "results" {
  #checkov:skip=CKV_AWS_144:Data must stay in ap-south-1; cross-region replication would move it out.
  #checkov:skip=CKV2_AWS_62:The API enqueues work itself; no S3 event notifications are used.
  bucket        = local.results_bucket
  force_destroy = var.force_destroy

  tags = merge(var.tags, { Name = local.results_bucket, DataClass = "customer-results" })
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "results" {
  bucket = aws_s3_bucket.results.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_logging" "results" {
  bucket = aws_s3_bucket.results.id

  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3/${local.results_bucket}/"

  target_object_key_format {
    partitioned_prefix {
      partition_date_source = "EventTime"
    }
  }
}

# Retention follows CONTRACTS.md section 5: run files 90 days, raw uploads 2 years.
# Deleted or replaced objects are kept as noncurrent versions only for a short
# safety window, so a user delete really removes the data soon after.
resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "expire-run-files"
    status = "Enabled"

    filter {
      prefix = "runs/"
    }

    expiration {
      days = var.runs_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_retention_days
    }
  }

  rule {
    id     = "expire-raw-uploads"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = var.raw_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_retention_days
    }
  }

  rule {
    id     = "cleanup"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }

    expiration {
      expired_object_delete_marker = true
    }

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_retention_days
    }
  }
}

data "aws_iam_policy_document" "results" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.results.arn,
      "${aws_s3_bucket.results.arn}/*",
    ]

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

  statement {
    sid       = "DenyOldTls"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["${aws_s3_bucket.results.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "NumericLessThan"
      variable = "s3:TlsVersion"
      values   = ["1.2"]
    }
  }

}

resource "aws_s3_bucket_policy" "results" {
  bucket = aws_s3_bucket.results.id
  policy = data.aws_iam_policy_document.results.json

  depends_on = [aws_s3_bucket_public_access_block.results]
}

# One-time, per-account setup. Run this first, with local state.
#
# Creates:
#   - the S3 bucket that holds Terraform state for staging and production
#     (versioned, KMS-encrypted, TLS-only; locking uses S3 lock files)
#   - the GitHub Actions OIDC provider (one per account)
#   - optionally, the Route 53 public zone for the apex domain
#
# Its own state stays local (terraform.tfstate in this folder, gitignored).
# Keep a copy somewhere safe, or migrate it into the bucket afterwards
# (see infra/README.md).

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = {
      Project   = "polygraph"
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  state_bucket = "${var.state_bucket_prefix}-${data.aws_caller_identity.current.account_id}"
}

# --- State bucket ------------------------------------------------------------

data "aws_iam_policy_document" "state_key" {
  #checkov:skip=CKV_AWS_109:Key policies must name Resource "*" (the key itself); root delegates to IAM.
  #checkov:skip=CKV_AWS_111:Key policies must name Resource "*" (the key itself); root delegates to IAM.
  #checkov:skip=CKV_AWS_356:Key policies must name Resource "*" (the key itself).
  statement {
    sid       = "EnableIamPolicies"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

resource "aws_kms_key" "state" {
  description             = "Terraform state for Polygraph"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.state_key.json
}

resource "aws_kms_alias" "state" {
  name          = "alias/polygraph-tfstate"
  target_key_id = aws_kms_key.state.key_id
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_144:State stays in the home region on purpose.
  #checkov:skip=CKV_AWS_18:Access to state is audited with CloudTrail; a separate log bucket is not created at bootstrap.
  #checkov:skip=CKV2_AWS_62:No event notifications needed for state.
  bucket = local.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 20
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "state" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
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
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# --- GitHub Actions OIDC provider (one per account) --------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# --- Route 53 zone for the apex domain ---------------------------------------

resource "aws_route53_zone" "apex" {
  #checkov:skip=CKV2_AWS_38:DNSSEC needs a registrar DS record; enable it after launch (see infra/README.md).
  #checkov:skip=CKV2_AWS_39:Public zone query logs would only collect resolver IPs; not needed.
  count = var.create_hosted_zone ? 1 : 0

  name    = var.zone_name
  comment = "Polygraph for MCP"

  lifecycle {
    prevent_destroy = true
  }
}

# Only Amazon may issue certificates for this domain.
resource "aws_route53_record" "caa" {
  count = var.create_hosted_zone ? 1 : 0

  zone_id = aws_route53_zone.apex[0].zone_id
  name    = var.zone_name
  type    = "CAA"
  ttl     = 3600
  records = [
    "0 issue \"amazon.com\"",
    "0 issuewild \";\"",
    "0 iodef \"mailto:${var.security_contact}\"",
  ]
}

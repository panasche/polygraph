# One private repository per image. Tags are immutable (git SHA tags), images
# are scanned on push, and old images are cleaned up by a lifecycle policy.

resource "aws_ecr_repository" "this" {
  for_each = toset(var.repositories)

  name                 = "${var.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  tags = merge(var.tags, { Image = each.value })
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the most recent tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.keep_images
        }
        action = { type = "expire" }
      },
    ]
  })
}

# Optional read access for another principal, e.g. the production deploy role
# copying an already-tested image from staging into production.
data "aws_iam_policy_document" "pull" {
  count = length(var.pull_principal_arns) > 0 ? 1 : 0

  statement {
    sid    = "AllowPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:DescribeImages",
    ]

    principals {
      type        = "AWS"
      identifiers = var.pull_principal_arns
    }
  }
}

resource "aws_ecr_repository_policy" "pull" {
  for_each = { for k, r in aws_ecr_repository.this : k => r.name if length(var.pull_principal_arns) > 0 }

  repository = each.value
  policy     = data.aws_iam_policy_document.pull[0].json
}

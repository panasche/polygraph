provider "aws" {
  region              = var.region
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = local.tags
  }
}

# CloudFront certificates and CLOUDFRONT-scoped WAF must live in us-east-1.
# Nothing that stores user data is created with this provider.
provider "aws" {
  alias               = "us_east_1"
  region              = "us-east-1"
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = local.tags
  }
}

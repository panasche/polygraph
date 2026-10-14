# DNS and TLS certificates. Everything is driven by variables so the domain
# can change (the product name still has a pending trademark check).
#
#   site_domain    e.g. polygraphmcp.dev          -> CloudFront (cert in us-east-1)
#   origin_domain  e.g. origin.polygraphmcp.dev   -> ALB        (cert in ap-south-1)

resource "aws_route53_zone" "this" {
  #checkov:skip=CKV2_AWS_38:DNSSEC needs a registrar DS record; enable it after launch (see infra/README.md).
  #checkov:skip=CKV2_AWS_39:Public zone query logs would only collect resolver IPs; not needed.
  count = var.create_zone ? 1 : 0

  name    = var.zone_name
  comment = "Polygraph for MCP"

  tags = var.tags
}

data "aws_route53_zone" "this" {
  count = var.create_zone ? 0 : 1

  name         = var.zone_name
  private_zone = false
}

locals {
  zone_id = var.create_zone ? aws_route53_zone.this[0].zone_id : data.aws_route53_zone.this[0].zone_id
}

# Only Amazon may issue certificates for the zone.
resource "aws_route53_record" "caa" {
  count = var.create_zone ? 1 : 0

  zone_id = local.zone_id
  name    = var.zone_name
  type    = "CAA"
  ttl     = 3600
  records = [
    "0 issue \"amazon.com\"",
    "0 issuewild \";\"",
    "0 iodef \"mailto:${var.caa_report_email}\"",
  ]
}

# --- Certificates ------------------------------------------------------------

resource "aws_acm_certificate" "site" {
  provider = aws.us_east_1

  domain_name               = var.site_domain
  subject_alternative_names = var.site_alternative_names
  validation_method         = "DNS"
  key_algorithm             = "EC_prime256v1"

  options {
    certificate_transparency_logging_preference = "ENABLED"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate" "origin" {
  domain_name       = var.origin_domain
  validation_method = "DNS"
  key_algorithm     = "EC_prime256v1"

  options {
    certificate_transparency_logging_preference = "ENABLED"
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

locals {
  validation_records = merge(
    {
      for dvo in aws_acm_certificate.site.domain_validation_options : dvo.domain_name => {
        name   = dvo.resource_record_name
        type   = dvo.resource_record_type
        record = dvo.resource_record_value
      }
    },
    {
      for dvo in aws_acm_certificate.origin.domain_validation_options : dvo.domain_name => {
        name   = dvo.resource_record_name
        type   = dvo.resource_record_type
        record = dvo.resource_record_value
      }
    },
  )
}

resource "aws_route53_record" "validation" {
  for_each = local.validation_records

  zone_id         = local.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  provider = aws.us_east_1

  certificate_arn = aws_acm_certificate.site.arn
  validation_record_fqdns = [
    for dvo in aws_acm_certificate.site.domain_validation_options : aws_route53_record.validation[dvo.domain_name].fqdn
  ]
}

resource "aws_acm_certificate_validation" "origin" {
  certificate_arn = aws_acm_certificate.origin.arn
  validation_record_fqdns = [
    for dvo in aws_acm_certificate.origin.domain_validation_options : aws_route53_record.validation[dvo.domain_name].fqdn
  ]
}

# --- Alias records -----------------------------------------------------------

resource "aws_route53_record" "site" {
  for_each = var.create_alias_records ? toset(["A", "AAAA"]) : toset([])

  zone_id = local.zone_id
  name    = var.site_domain
  type    = each.value

  alias {
    name                   = var.cloudfront_domain_name
    zone_id                = var.cloudfront_hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "origin" {
  count = var.create_alias_records ? 1 : 0

  zone_id = local.zone_id
  name    = var.origin_domain
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}

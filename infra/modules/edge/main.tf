# CloudFront in front of the ALB.
#
# Behaviours (first match wins, most specific first):
#   /api/badge/*               cached 300 s (badges are public SVGs)
#   /api/*                     never cached; all headers, cookies and query strings go to the API
#   /_next/static/*            cached 1 year (content-hashed file names)
#   /.well-known/security.txt  goes to web, short cache
#   default                    web, short cache that respects the origin's Cache-Control

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

locals {
  origin_id = "alb"

  # App Router request headers that change the response body (RSC payloads vs HTML).
  next_router_headers = [
    "RSC",
    "Next-Router-State-Tree",
    "Next-Router-Prefetch",
    "Next-Router-Segment-Prefetch",
    "Next-Url",
  ]
}

# --- Cache policies ----------------------------------------------------------

resource "aws_cloudfront_cache_policy" "badge" {
  name        = "${var.name}-badge"
  comment     = "Public badge SVGs, 300 s"
  min_ttl     = 0
  default_ttl = 300
  max_ttl     = 300

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "whitelist"

      query_strings {
        items = ["branch"]
      }
    }
  }
}

resource "aws_cloudfront_cache_policy" "static_immutable" {
  name        = "${var.name}-next-static"
  comment     = "Content-hashed Next.js assets, 1 year"
  min_ttl     = 31536000
  default_ttl = 31536000
  max_ttl     = 31536000

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

# Pages: short default TTL, honours Cache-Control: private/no-store from Next.js.
# The session cookie is part of the key so a signed-in page is never served to
# someone else, even if the origin forgets to mark it private.
resource "aws_cloudfront_cache_policy" "web" {
  name        = "${var.name}-web"
  comment     = "Web pages, short cache, session-aware"
  min_ttl     = 0
  default_ttl = var.web_default_ttl
  max_ttl     = 3600

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "whitelist"

      cookies {
        items = var.session_cookie_names
      }
    }

    headers_config {
      header_behavior = "whitelist"

      headers {
        items = local.next_router_headers
      }
    }

    query_strings_config {
      query_string_behavior = "all"
    }
  }
}

# --- Security response headers -----------------------------------------------

resource "aws_cloudfront_response_headers_policy" "security" {
  name    = "${var.name}-security-headers"
  comment = "HSTS, CSP fallback, frame deny, nosniff, referrer"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }

    # Placeholder CSP. override = false so the web app's own CSP header
    # (set in cloud/web/next.config.ts) wins when it is present.
    content_security_policy {
      content_security_policy = var.content_security_policy
      override                = false
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    content_type_options {
      override = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
      override = false
    }
  }

  remove_headers_config {
    items {
      header = "Server"
    }

    items {
      header = "X-Powered-By"
    }
  }
}

# --- Distribution ------------------------------------------------------------

resource "aws_cloudfront_distribution" "this" {
  #checkov:skip=CKV_AWS_305:The web app serves "/" itself; a default root object would bypass it.
  #checkov:skip=CKV_AWS_310:A single ALB origin; there is no second origin to fail over to.
  #checkov:skip=CKV_AWS_374:No geo restriction: the free service is open to everyone.
  #checkov:skip=CKV2_AWS_47:The attached WAF includes AWSManagedRulesKnownBadInputsRuleSet (Log4j).
  enabled         = true
  is_ipv6_enabled = true
  http_version    = "http2and3"
  comment         = "${var.name} web + API"
  aliases         = var.aliases
  price_class     = var.price_class
  web_acl_id      = var.web_acl_arn

  origin {
    origin_id   = local.origin_id
    domain_name = var.origin_domain_name

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "https-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 30
    }

    custom_header {
      name  = var.origin_verify_header_name
      value = var.origin_verify_secret
    }
  }

  ordered_cache_behavior {
    path_pattern               = "/api/badge/*"
    target_origin_id           = local.origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.badge.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = local.origin_id
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  ordered_cache_behavior {
    path_pattern               = "/_next/static/*"
    target_origin_id           = local.origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.static_immutable.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  ordered_cache_behavior {
    path_pattern               = "/.well-known/security.txt"
    target_origin_id           = local.origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.web.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  default_cache_behavior {
    target_origin_id           = local.origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.web.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  logging_config {
    bucket          = var.logs_bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  tags = var.tags
}

# Application secrets. Terraform creates the empty containers only; the values
# are written out-of-band (infra/scripts/set-secrets.sh) so they never land in
# Terraform state or in CI logs.

resource "aws_secretsmanager_secret" "this" {
  for_each = var.secrets

  #checkov:skip=CKV2_AWS_57:These are externally issued (GitHub App) or app keys rotated by the runbook, not by a Lambda.
  name                    = "${var.name}/${each.key}"
  description             = each.value
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days

  tags = merge(var.tags, { EnvVar = upper(each.key) })
}

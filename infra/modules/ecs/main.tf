# ECS Fargate cluster with three services, all ARM64 (Graviton):
#   api    - FastAPI on :8000, behind the ALB, reachable by web as http://api:8000
#   worker - queue consumer, no inbound traffic
#   web    - Next.js on :3000, behind the ALB
#
# Terraform owns the task definitions; CI registers new revisions with a new
# image (copying everything else from the latest revision) and updates the
# services. That is why the services ignore task_definition changes.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition

  container_linux = {
    capabilities       = { add = [], drop = ["ALL"] }
    initProcessEnabled = true
  }

  tmp_mount = { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }

  # Entry point for the API image (api, worker, migrations, prune). ECS injects
  # DB_USERNAME / DB_PASSWORD from the RDS-managed secret; this builds
  # DATABASE_URL from them (URL-encoded) and then execs the real command, so the
  # app only ever sees DATABASE_URL. Overriding "command" (migrations, the daily
  # job) keeps this entry point.
  database_url_shim   = <<-PY
    import os, sys, urllib.parse as u
    e = os.environ
    if not e.get("DATABASE_URL") and e.get("DB_HOST"):
        user = u.quote(e.pop("DB_USERNAME"), safe="")
        pw = u.quote(e.pop("DB_PASSWORD"), safe="")
        e["DATABASE_URL"] = "postgresql+psycopg://%s:%s@%s:%s/%s?sslmode=%s" % (
            user, pw, e["DB_HOST"], e.get("DB_PORT", "5432"),
            e.get("DB_NAME", "polygraph"), e.get("DB_SSLMODE", "require"))
    os.execvp(sys.argv[1], sys.argv[1:])
  PY
  backend_entry_point = var.database_url_shim ? ["python", "-c", local.database_url_shim] : null

  log_options = {
    for svc, group in var.log_group_names : svc => {
      "awslogs-group"         = group
      "awslogs-region"        = local.region
      "awslogs-stream-prefix" = svc
      "mode"                  = "non-blocking"
      "max-buffer-size"       = "25m"
    }
  }

  # ECS rejects null fields, so optional keys are merged in only when set.
  api_container = merge(
    {
      name                   = "api"
      image                  = var.api_image
      essential              = true
      readonlyRootFilesystem = var.readonly_root_filesystem
      portMappings = [{
        name          = "http"
        containerPort = var.api_port
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      environment      = [for k in sort(keys(var.api_environment)) : { name = k, value = var.api_environment[k] }]
      secrets          = [for k in sort(keys(var.api_secrets)) : { name = k, valueFrom = var.api_secrets[k] }]
      linuxParameters  = local.container_linux
      mountPoints      = [local.tmp_mount]
      stopTimeout      = 30
      logConfiguration = { logDriver = "awslogs", options = local.log_options["api"] }
    },
    var.container_user == null ? {} : { user = var.container_user },
    var.api_command == null ? {} : { command = var.api_command },
    local.backend_entry_point == null ? {} : { entryPoint = local.backend_entry_point },
  )

  worker_container = merge(
    {
      name                   = "worker"
      image                  = var.worker_image
      essential              = true
      readonlyRootFilesystem = var.readonly_root_filesystem
      command                = var.worker_command
      environment            = [for k in sort(keys(var.worker_environment)) : { name = k, value = var.worker_environment[k] }]
      secrets                = [for k in sort(keys(var.worker_secrets)) : { name = k, valueFrom = var.worker_secrets[k] }]
      linuxParameters        = local.container_linux
      mountPoints            = [local.tmp_mount]
      stopTimeout            = 120
      logConfiguration       = { logDriver = "awslogs", options = local.log_options["worker"] }
    },
    var.container_user == null ? {} : { user = var.container_user },
    local.backend_entry_point == null ? {} : { entryPoint = local.backend_entry_point },
  )

  web_container = merge(
    {
      name                   = "web"
      image                  = var.web_image
      essential              = true
      readonlyRootFilesystem = var.readonly_root_filesystem
      portMappings = [{
        name          = "http"
        containerPort = var.web_port
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      environment     = [for k in sort(keys(var.web_environment)) : { name = k, value = var.web_environment[k] }]
      linuxParameters = local.container_linux
      mountPoints = [
        local.tmp_mount,
        { sourceVolume = "next-cache", containerPath = var.web_cache_path, readOnly = false },
      ]
      stopTimeout      = 30
      logConfiguration = { logDriver = "awslogs", options = local.log_options["web"] }
    },
    var.container_user == null ? {} : { user = var.container_user },
  )

  services = {
    api    = { min = var.api_min_count, max = var.api_max_count }
    worker = { min = var.worker_min_count, max = var.worker_max_count }
    web    = { min = var.web_min_count, max = var.web_max_count }
  }
}

# --- Cluster -----------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  #checkov:skip=CKV_AWS_224:ECS Exec is disabled in production; staging sessions use the default (TLS) channel.
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enhanced"
  }

  configuration {
    execute_command_configuration {
      logging = "DEFAULT"
    }
  }

  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }
}

# Service Connect namespace: web reaches the API at http://api:8000.
resource "aws_service_discovery_http_namespace" "this" {
  name        = var.name
  description = "Service Connect namespace for ${var.name}"

  tags = var.tags
}

# --- Security groups ---------------------------------------------------------

resource "aws_security_group" "service" {
  for_each = toset(["api", "worker", "web"])

  name        = "${var.name}-${each.key}"
  description = "${each.key} tasks"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-${each.key}" })
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.service["api"].id
  description                  = "API from the ALB"
  ip_protocol                  = "tcp"
  from_port                    = var.api_port
  to_port                      = var.api_port
  referenced_security_group_id = var.alb_security_group_id
}

resource "aws_vpc_security_group_ingress_rule" "api_from_web" {
  security_group_id            = aws_security_group.service["api"].id
  description                  = "API from web (Service Connect)"
  ip_protocol                  = "tcp"
  from_port                    = var.api_port
  to_port                      = var.api_port
  referenced_security_group_id = aws_security_group.service["web"].id
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.service["web"].id
  description                  = "Web from the ALB"
  ip_protocol                  = "tcp"
  from_port                    = var.web_port
  to_port                      = var.web_port
  referenced_security_group_id = var.alb_security_group_id
}

resource "aws_vpc_security_group_egress_rule" "https_out" {
  for_each = aws_security_group.service

  security_group_id = each.value.id
  description       = "HTTPS to AWS endpoints and GitHub"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "postgres_out" {
  for_each = toset(["api", "worker"])

  security_group_id = aws_security_group.service[each.key].id
  description       = "PostgreSQL to the data subnets"
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432
  cidr_ipv4         = var.vpc_cidr_block
}

resource "aws_vpc_security_group_egress_rule" "web_to_api" {
  security_group_id = aws_security_group.service["web"].id
  description       = "Web to API (Service Connect)"
  ip_protocol       = "tcp"
  from_port         = var.api_port
  to_port           = var.api_port
  cidr_ipv4         = var.vpc_cidr_block
}

# --- Task definitions --------------------------------------------------------

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([local.api_container])

  tags = var.tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([local.worker_container])

  tags = var.tags
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.web.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  volume {
    name = "tmp"
  }

  volume {
    name = "next-cache"
  }

  container_definitions = jsonencode([local.web_container])

  tags = var.tags
}

# --- Services ----------------------------------------------------------------

resource "aws_ecs_service" "api" {
  name                              = "${var.name}-api"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.api_min_count
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  enable_execute_command            = var.enable_execute_command
  enable_ecs_managed_tags           = true
  propagate_tags                    = "SERVICE"
  health_check_grace_period_seconds = 30
  availability_zone_rebalancing     = "ENABLED"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service["api"].id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.api_target_group_arn
    container_name   = "api"
    container_port   = var.api_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    service {
      port_name      = "http"
      discovery_name = "api"

      client_alias {
        port     = var.api_port
        dns_name = "api"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_names["service-connect"]
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

resource "aws_ecs_service" "worker" {
  name                          = "${var.name}-worker"
  cluster                       = aws_ecs_cluster.this.id
  task_definition               = aws_ecs_task_definition.worker.arn
  desired_count                 = var.worker_min_count
  launch_type                   = "FARGATE"
  platform_version              = "LATEST"
  enable_execute_command        = var.enable_execute_command
  enable_ecs_managed_tags       = true
  propagate_tags                = "SERVICE"
  availability_zone_rebalancing = "ENABLED"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service["worker"].id]
    assign_public_ip = false
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

resource "aws_ecs_service" "web" {
  name                              = "${var.name}-web"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.web.arn
  desired_count                     = var.web_min_count
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  enable_execute_command            = var.enable_execute_command
  enable_ecs_managed_tags           = true
  propagate_tags                    = "SERVICE"
  health_check_grace_period_seconds = 30
  availability_zone_rebalancing     = "ENABLED"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service["web"].id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.web_target_group_arn
    container_name   = "web"
    container_port   = var.web_port
  }

  # Client-only Service Connect: lets web resolve "api".
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    log_configuration {
      log_driver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_names["service-connect"]
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = "web"
      }
    }
  }

  tags = var.tags

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  # Make sure the API's Service Connect endpoint exists before web starts.
  depends_on = [aws_ecs_service.api]
}

# --- Autoscaling -------------------------------------------------------------

resource "aws_appautoscaling_target" "this" {
  for_each = local.services

  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${var.name}-${each.key}"
  min_capacity       = each.value.min
  max_capacity       = each.value.max

  depends_on = [aws_ecs_service.api, aws_ecs_service.worker, aws_ecs_service.web]
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = toset(["api", "web"])

  name               = "${var.name}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[each.key].service_namespace
  scalable_dimension = aws_appautoscaling_target.this[each.key].scalable_dimension
  resource_id        = aws_appautoscaling_target.this[each.key].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = var.cpu_target_percent
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_policy" "requests" {
  for_each = {
    api = var.api_target_group_arn_suffix
    web = var.web_target_group_arn_suffix
  }

  name               = "${var.name}-${each.key}-requests"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[each.key].service_namespace
  scalable_dimension = aws_appautoscaling_target.this[each.key].scalable_dimension
  resource_id        = aws_appautoscaling_target.this[each.key].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = var.requests_per_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${var.alb_arn_suffix}/${each.value}"
    }
  }
}

# Worker: keep the backlog per running task near the target.
resource "aws_appautoscaling_policy" "worker_backlog" {
  name               = "${var.name}-worker-backlog"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this["worker"].service_namespace
  scalable_dimension = aws_appautoscaling_target.this["worker"].scalable_dimension
  resource_id        = aws_appautoscaling_target.this["worker"].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = var.worker_backlog_per_task
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    customized_metric_specification {
      metrics {
        id          = "visible"
        return_data = false

        metric_stat {
          stat = "Sum"

          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"

            dimensions {
              name  = "QueueName"
              value = var.queue_name
            }
          }
        }
      }

      metrics {
        id          = "tasks"
        return_data = false

        metric_stat {
          stat = "Average"

          metric {
            namespace   = "ECS/ContainerInsights"
            metric_name = "RunningTaskCount"

            dimensions {
              name  = "ClusterName"
              value = aws_ecs_cluster.this.name
            }

            dimensions {
              name  = "ServiceName"
              value = aws_ecs_service.worker.name
            }
          }
        }
      }

      metrics {
        id          = "backlog"
        label       = "Messages per running worker task"
        expression  = "IF(tasks > 0, visible / tasks, visible)"
        return_data = true
      }
    }
  }
}

# --- Optional scheduled job (daily prune) ------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count = length(var.scheduled_job_command) > 0 ? 1 : 0

  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "scheduler" {
  count = length(var.scheduled_job_command) > 0 ? 1 : 0

  statement {
    actions   = ["ecs:RunTask"]
    resources = ["arn:${local.partition}:ecs:${local.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.worker.family}:*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.worker.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler" {
  count = length(var.scheduled_job_command) > 0 ? 1 : 0

  name   = "run-scheduled-job"
  role   = aws_iam_role.scheduler[0].id
  policy = data.aws_iam_policy_document.scheduler[0].json
}

resource "aws_scheduler_schedule" "job" {
  #checkov:skip=CKV_AWS_297:The schedule input is only a command line, no data.
  count = length(var.scheduled_job_command) > 0 ? 1 : 0

  name                         = "${var.name}-daily-job"
  description                  = "Daily maintenance job (retention prune) on the worker image"
  schedule_expression          = var.scheduled_job_expression
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 30
  }

  target {
    # Runs the worker family's latest ACTIVE revision, so it follows deploys.
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler[0].arn

    ecs_parameters {
      task_definition_arn = "arn:${local.partition}:ecs:${local.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.worker.family}"
      launch_type         = "FARGATE"
      platform_version    = "LATEST"
      task_count          = 1

      network_configuration {
        subnets          = var.private_subnet_ids
        security_groups  = [aws_security_group.service["worker"].id]
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "worker"
        command = var.scheduled_job_command
      }]
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}

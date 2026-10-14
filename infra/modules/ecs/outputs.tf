output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "service_names" {
  description = "Service names keyed api, worker, web."
  value = {
    api    = aws_ecs_service.api.name
    worker = aws_ecs_service.worker.name
    web    = aws_ecs_service.web.name
  }
}

output "service_arns" {
  description = "Service ARNs keyed api, worker, web."
  value = {
    api    = aws_ecs_service.api.id
    worker = aws_ecs_service.worker.id
    web    = aws_ecs_service.web.id
  }
}

output "task_definition_families" {
  description = "Task definition families keyed api, worker, web."
  value = {
    api    = aws_ecs_task_definition.api.family
    worker = aws_ecs_task_definition.worker.family
    web    = aws_ecs_task_definition.web.family
  }
}

output "security_group_ids" {
  description = "Task security groups keyed api, worker, web."
  value       = { for k, sg in aws_security_group.service : k => sg.id }
}

output "execution_role_arn" {
  description = "Task execution role."
  value       = aws_iam_role.execution.arn
}

output "task_role_arns" {
  description = "Task roles keyed api, worker, web."
  value = {
    api    = aws_iam_role.api.arn
    worker = aws_iam_role.worker.arn
    web    = aws_iam_role.web.arn
  }
}

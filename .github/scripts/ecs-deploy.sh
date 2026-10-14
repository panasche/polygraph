#!/usr/bin/env bash
# Roll a new image out to one Polygraph Cloud environment on ECS.
#
#   1. Register new revisions of the api, worker and web task definitions.
#      Everything except the image is copied from the latest revision, which
#      Terraform owns, so env vars and sizes always come from Terraform.
#   2. Run database migrations as a one-off task on the new API revision and
#      stop if they fail.
#   3. Update the services and wait until each new deployment COMPLETES.
#      The ECS circuit breaker rolls back a failed deployment; this script then
#      reports the failure instead of pretending it worked.
#
# Required env: AWS_REGION ECS_CLUSTER ECS_SERVICE_API ECS_SERVICE_WORKER
#               ECS_SERVICE_WEB API_IMAGE WEB_IMAGE
# Optional env: MIGRATION_COMMAND (JSON array), DEPLOY_TIMEOUT_SECONDS

set -euo pipefail

: "${AWS_REGION:?}" "${ECS_CLUSTER:?}" "${ECS_SERVICE_API:?}" "${ECS_SERVICE_WORKER:?}"
: "${ECS_SERVICE_WEB:?}" "${API_IMAGE:?}" "${WEB_IMAGE:?}"
MIGRATION_COMMAND="${MIGRATION_COMMAND:-[\"python\",\"-m\",\"polygraph_cloud.cli\",\"migrate\"]}"
DEPLOY_TIMEOUT_SECONDS="${DEPLOY_TIMEOUT_SECONDS:-1200}"

export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

log() { printf '[deploy] %s\n' "$*" >&2; }

# Register a new revision of the family behind a service, with a new image for one container.
register_revision() {
  local service="$1" container="$2" image="$3"
  local current family td new_arn

  current="$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$service" \
    --query 'services[0].taskDefinition' --output text)"
  family="$(aws ecs describe-task-definition --task-definition "$current" \
    --query 'taskDefinition.family' --output text)"

  # Latest ACTIVE revision of the family = what Terraform last registered.
  td="$(aws ecs describe-task-definition --task-definition "$family" --include TAGS --output json)"

  new_arn="$(
    jq --arg c "$container" --arg img "$image" '
      .tags as $tags
      | .taskDefinition
      | .containerDefinitions |= map(if .name == $c then .image = $img else . end)
      | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
            .registeredAt, .registeredBy, .deregisteredAt)
      | if ($tags | length) > 0 then .tags = $tags else . end
    ' <<<"$td" >"${RUNNER_TEMP:-/tmp}/td-${container}.json"
    aws ecs register-task-definition \
      --cli-input-json "file://${RUNNER_TEMP:-/tmp}/td-${container}.json" \
      --query 'taskDefinition.taskDefinitionArn' --output text
  )"
  log "registered $new_arn"
  printf '%s' "$new_arn"
}

run_migrations() {
  local td="$1" netcfg task_arn exit_code reason task_id

  netcfg="$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE_API" \
    --query 'services[0].networkConfiguration' --output json)"

  log "running migrations: $MIGRATION_COMMAND"
  task_arn="$(aws ecs run-task \
    --cluster "$ECS_CLUSTER" \
    --task-definition "$td" \
    --launch-type FARGATE \
    --count 1 \
    --started-by "github-deploy-migrate" \
    --network-configuration "$netcfg" \
    --overrides "$(jq -nc --argjson cmd "$MIGRATION_COMMAND" '{containerOverrides: [{name: "api", command: $cmd}]}')" \
    --query 'tasks[0].taskArn' --output text)"

  if [[ -z "$task_arn" || "$task_arn" == "None" ]]; then
    log "could not start the migration task"
    return 1
  fi
  log "migration task: $task_arn"

  # `aws ecs wait tasks-stopped` gives up after 10 minutes; loop for longer migrations.
  local deadline=$((SECONDS + 1800)) status
  while :; do
    status="$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task_arn" \
      --query 'tasks[0].lastStatus' --output text)"
    [[ "$status" == "STOPPED" ]] && break
    if ((SECONDS > deadline)); then
      log "migration task did not finish in time; stopping it"
      aws ecs stop-task --cluster "$ECS_CLUSTER" --task "$task_arn" --reason "deploy timeout" >/dev/null
      return 1
    fi
    sleep 10
  done

  # shellcheck disable=SC2016 # backticks are JMESPath literals, not shell
  exit_code="$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task_arn" \
    --query 'tasks[0].containers[?name==`api`].exitCode | [0]' --output text)"
  reason="$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task_arn" \
    --query 'tasks[0].stoppedReason' --output text)"

  # Print the migration output. It holds ids and schema names only, never user data.
  task_id="${task_arn##*/}"
  local group
  group="$(jq -r '.containerDefinitions[] | select(.name=="api") | .logConfiguration.options["awslogs-group"]' \
    "${RUNNER_TEMP:-/tmp}/td-api.json")"
  aws logs get-log-events --log-group-name "$group" --log-stream-name "api/api/${task_id}" \
    --start-from-head --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | tail -200 || true

  if [[ "$exit_code" != "0" ]]; then
    log "migrations failed (exit ${exit_code}, ${reason})"
    return 1
  fi
  log "migrations done"
}

update_service() {
  local service="$1" td="$2"
  aws ecs update-service --cluster "$ECS_CLUSTER" --service "$service" \
    --task-definition "$td" --query 'service.serviceName' --output text >/dev/null
  log "updating $service -> ${td##*/}"
}

# Wait until the PRIMARY deployment of a service is COMPLETED (or FAILED).
wait_for_service() {
  local service="$1" td="$2" deadline=$((SECONDS + DEPLOY_TIMEOUT_SECONDS)) state primary_td
  while :; do
    # shellcheck disable=SC2016 # backticks are JMESPath literals, not shell
    read -r state primary_td < <(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$service" \
      --query 'services[0].deployments[?status==`PRIMARY`] | [0].[rolloutState, taskDefinition]' --output text)
    if [[ "$primary_td" != "$td" ]]; then
      log "$service: circuit breaker rolled back to ${primary_td##*/}"
      return 1
    fi
    case "$state" in
      COMPLETED) log "$service: deployment completed"; return 0 ;;
      FAILED) log "$service: deployment failed"; return 1 ;;
    esac
    if ((SECONDS > deadline)); then
      log "$service: still ${state} after ${DEPLOY_TIMEOUT_SECONDS}s"
      return 1
    fi
    sleep 15
  done
}

api_td="$(register_revision "$ECS_SERVICE_API" api "$API_IMAGE")"
worker_td="$(register_revision "$ECS_SERVICE_WORKER" worker "$API_IMAGE")"
web_td="$(register_revision "$ECS_SERVICE_WEB" web "$WEB_IMAGE")"

run_migrations "$api_td"

update_service "$ECS_SERVICE_API" "$api_td"
update_service "$ECS_SERVICE_WORKER" "$worker_td"
update_service "$ECS_SERVICE_WEB" "$web_td"

failed=0
wait_for_service "$ECS_SERVICE_API" "$api_td" || failed=1
wait_for_service "$ECS_SERVICE_WORKER" "$worker_td" || failed=1
wait_for_service "$ECS_SERVICE_WEB" "$web_td" || failed=1

if ((failed)); then
  log "deployment failed; ECS rolled back the failing service(s)"
  exit 1
fi
log "all services deployed"

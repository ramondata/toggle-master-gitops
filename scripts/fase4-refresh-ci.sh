#!/usr/bin/env bash
# Atualiza os cinco Secrets a partir da sessao AWS atual, sem colar chaves no chat.
set -euo pipefail
set +x
umask 077
for command in aws gh python3; do
  command -v "$command" >/dev/null || { printf 'Instale o comando %s antes de executar.\n' "$command"; exit 2; }
done
region="${TARGET_REGION:-us-east-1}"
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
aws sts get-caller-identity --no-cli-pager >/dev/null
aws eks describe-cluster --name "${TARGET_CLUSTER:-toggle-cluster}" --region "$region" --query cluster.status --output text >/dev/null
if ! gh auth status >/dev/null 2>&1; then
  gh auth login --hostname github.com --git-protocol https --web
fi
login="$(gh api user --jq .login)"
[[ "$login" == ramondata ]] || { echo 'Use a conta GitHub ramondata para atualizar estes repositorios.'; exit 2; }
aws configure export-credentials --format process > "$work_dir/credentials.json"
python3 - "$work_dir" <<'PY'
import datetime
import json
from pathlib import Path
import sys
root=Path(sys.argv[1])
data=json.loads((root/'credentials.json').read_text())
keys={'aws_access_key_id':'AccessKeyId','aws_secret_access_key':'SecretAccessKey','aws_session_token':'SessionToken'}
for source in keys.values():
    value=data.get(source,'')
    if not value or any(c.isspace() for c in value):
        raise SystemExit('Sessao temporaria incompleta; renove o lab.')
if data.get('Expiration'):
    expiry=datetime.datetime.fromisoformat(data['Expiration'].replace('Z','+00:00'))
    if (expiry-datetime.datetime.now(datetime.timezone.utc)).total_seconds()<1200:
        raise SystemExit('Renove a sessao: faltam menos de 20 minutos para expirar.')
(root/'academy.ini').write_text('[default]\n'+''.join(k+'='+data[v]+'\n' for k,v in keys.items()))
PY
for service in auth-service flag-service targeting-service evaluation-service analytics-service; do
  aws ecr describe-repositories --region "$region" --repository-names "toggle-master/$service" >/dev/null
  gh secret set AWS_ACADEMY_CREDENTIALS --repo "ramondata/$service" < "$work_dir/academy.ini"
done
run_services=()
run_ids=()
for service in auth-service flag-service targeting-service evaluation-service analytics-service; do
  # Rerun do push existente funciona mesmo quando workflow_dispatch nao esta na branch default.
  run_id="$(gh run list --repo "ramondata/$service" --branch aws-cloud-k8s --event push --limit 1 --json databaseId --jq '.[0].databaseId // empty')"
  [[ -n "$run_id" ]] || { printf 'Nenhuma execucao push encontrada para %s.\n' "$service"; exit 2; }
  run_services+=("$service")
  run_ids+=("$run_id")
  status="$(gh run view "$run_id" --repo "ramondata/$service" --json status --jq .status)"
  conclusion="$(gh run view "$run_id" --repo "ramondata/$service" --json conclusion --jq .conclusion)"
  if [[ "$status" != completed ]]; then
    printf '%s ainda esta executando; reexecute este script apos a conclusao se a publicacao falhar.\n' "$service"
  elif [[ "${FORCE_REPUBLISH:-false}" == true ]]; then
    gh run rerun "$run_id" --repo "ramondata/$service"
    printf 'Publicacao completa reacionada: https://github.com/ramondata/%s/actions/runs/%s\n' "$service" "$run_id"
  elif [[ "$conclusion" == success ]]; then
    printf 'Ultima publicacao de %s ja terminou com sucesso.\n' "$service"
  else
    gh run rerun "$run_id" --repo "ramondata/$service" --failed
    printf 'Esteira reacionada: https://github.com/ramondata/%s/actions/runs/%s\n' "$service" "$run_id"
  fi
done
if [[ "${WAIT_FOR_CI:-false}" == true ]]; then
  for index in "${!run_ids[@]}"; do
    gh run watch "${run_ids[$index]}" --repo "ramondata/${run_services[$index]}" --exit-status --interval 10
  done
fi
printf 'Credenciais atualizadas. Acompanhe as esteiras: build, scan, push ECR e update GitOps.\n'

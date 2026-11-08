#!/usr/bin/env bash
# Bootstrap repetivel no EKS criado pelo Terraform do AWS Academy.
set -euo pipefail
set +x
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export TARGET_CLUSTER="${TARGET_CLUSTER:-toggle-cluster}"
export TARGET_REGION="${TARGET_REGION:-us-east-1}"
export AWS_PAGER=""
export PATH="$HOME/.local/bin:$PATH"
REF="${GITOPS_REF:-master}"
ARGO_VERSION="${ARGOCD_VERSION:-v3.5.2}"
WORK="$(mktemp -d)"
held_nodes=()
cleanup() {
  for node in "${held_nodes[@]}"; do kubectl uncordon "$node" || true; done
  rm -rf -- "$WORK"
}
trap cleanup EXIT
mode="${1:-help}"
services=(auth-service flag-service targeting-service evaluation-service analytics-service)

usage() {
  cat <<'TEXT'
Uso: bash academy/lab.sh COMANDO
  up             Configura EKS, Argo CD, HPA, aplicacoes e observabilidade.
  refresh        Renova credenciais AWS nos pods; nao reinstala a stack.
  publish        Atualiza credenciais no GitHub e reexecuta CI dos 5 servicos.
  status         Mostra contexto, Applications, pods e enderecos.
  passwords      Mostra as senhas somente neste terminal.
  verify         Testa o fluxo real AWS, metricas e logs (sem injetar falha).
  integrations   Troca as chaves New Relic/PagerDuty/Discord com entrada oculta.
  databases      Corrige URLs RDS com entrada oculta; preserva chaves de servico.
  demo start|logs|fail|recover|status
Padrao: toggle-cluster / us-east-1. Execute no CloudShell com o lab ativo.
TEXT
}

if [[ "$mode" == help || "$mode" == --help || "$mode" == -h ]]; then usage; exit; fi
case "$mode" in up|refresh|publish|status|passwords|verify|integrations|databases|demo) ;; *) usage >&2; exit 2 ;; esac
for dependency in aws kubectl jq python3 curl openssl git; do
  command -v "$dependency" >/dev/null || { printf 'Comando ausente: %s. Use o AWS CloudShell.\n' "$dependency" >&2; exit 2; }
done
[[ "$REF" =~ ^[a-zA-Z0-9._/-]+$ && "$ARGO_VERSION" =~ ^v[0-9]+[.][0-9]+[.][0-9]+$ ]] || { echo 'Versao/ref invalida.' >&2; exit 2; }

connect() {
  aws sts get-caller-identity --region "$TARGET_REGION" --output json > "$WORK/identity.json"
  ACCOUNT="$(jq -er .Account "$WORK/identity.json")"
  [[ "$ACCOUNT" =~ ^[0-9]{12}$ ]] || exit 2
  # O arquivo do kind e o contexto padrao do usuario nao sao alterados.
  state="${ACADEMY_STATE_DIR:-$HOME/.toggle-academy/$ACCOUNT/$TARGET_CLUSTER}"
  mkdir -p "$state"
  chmod 700 "$state"
  export KUBECONFIG="$state/kubeconfig"
  aws eks describe-cluster --name "$TARGET_CLUSTER" --region "$TARGET_REGION" --output json > "$WORK/cluster.json"
  if [[ "$(jq -r .cluster.status "$WORK/cluster.json")" != ACTIVE ]]; then
    echo 'O EKS precisa estar ACTIVE. Crie/recupere a infraestrutura pelo Terraform antes do bootstrap.' >&2; exit 2
  fi
  aws eks update-kubeconfig --region "$TARGET_REGION" --name "$TARGET_CLUSTER" --kubeconfig "$KUBECONFIG" --alias "academy-$TARGET_CLUSTER" >/dev/null
  expected="$(jq -er .cluster.endpoint "$WORK/cluster.json")"
  actual="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
  [[ "$actual" == "$expected" && ( "$actual" == https://*.eks.amazonaws.com || "$actual" == https://*.api.aws ) ]] || { echo 'Contexto diferente do EKS esperado.' >&2; exit 2; }
  kubectl --request-timeout=20s get nodes >/dev/null
  printf 'Conta %s | EKS %s | regiao %s\n' "$ACCOUNT" "$TARGET_CLUSTER" "$TARGET_REGION"
}

diagnose() {
  printf '\nBootstrap interrompido; pode repetir o comando apos corrigir a causa.\n' >&2
  kubectl --request-timeout=10s -n argocd get applications >&2 || true
  kubectl --request-timeout=10s get pods -A -o wide >&2 || true
  kubectl --request-timeout=10s get events -A --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -15 >&2 || true
}

wait_app() {
  local name="$1" remaining refreshed=false previous_reconcile
  local expected=()
  if [[ "$name" == platform || "$name" == *-service ]]; then
    expected=(--config-revision "$CONFIG_REVISION" --aws-revision "$AWS_REVISION")
  fi
  for ((remaining=0;remaining<150;remaining++)); do
    if kubectl -n argocd get application "$name" -o json > "$WORK/app.json" 2>/dev/null; then
      if [[ "$refreshed" == false ]]; then
        previous_reconcile="$(jq -r '.status.reconciledAt // ""' "$WORK/app.json")"
        kubectl -n argocd annotate application "$name" argocd.argoproj.io/refresh=hard --overwrite >/dev/null
        refreshed=true
      elif [[ "$(jq -r '.status.reconciledAt // ""' "$WORK/app.json")" != "$previous_reconcile" ]] &&
           python3 "$ROOT/academy/apps.py" ready "${expected[@]}" < "$WORK/app.json"; then return; fi
    fi
    if (( remaining % 6 == 0 )); then
      printf 'Aguardando Argo CD: %s\n' "$name"
      kubectl -n argocd get application "$name" --ignore-not-found || true
    fi
    sleep 5
  done
  printf 'Tempo limite: Application %s.\n' "$name" >&2
  return 1
}

ensure_capacity() {
  local count group desired max min update phase ready
  count="${ACADEMY_NODE_COUNT:-3}"
  [[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo 'ACADEMY_NODE_COUNT deve ser positivo.' >&2; return 2; }
  aws eks list-nodegroups --cluster-name "$TARGET_CLUSTER" --region "$TARGET_REGION" --output json > "$WORK/groups.json"
  group="${TARGET_NODEGROUP:-}"
  if [[ -z "$group" ]]; then
    [[ "$(jq '.nodegroups | length' "$WORK/groups.json")" == 1 ]] || { echo 'Defina TARGET_NODEGROUP: o cluster precisa ter um grupo de nos alvo.' >&2; return 2; }
    group="$(jq -r '.nodegroups[0]' "$WORK/groups.json")"
  fi
  aws eks describe-nodegroup --cluster-name "$TARGET_CLUSTER" --nodegroup-name "$group" --region "$TARGET_REGION" --output json > "$WORK/group.json"
  desired="$(jq -r .nodegroup.scalingConfig.desiredSize "$WORK/group.json")"
  max="$(jq -r .nodegroup.scalingConfig.maxSize "$WORK/group.json")"
  min="$(jq -r .nodegroup.scalingConfig.minSize "$WORK/group.json")"
  if (( desired < count )); then
    (( max >= count )) || max="$count"
    printf 'Capacidade: aumentando %s de %s para %s nos (consome creditos Academy).\n' "$group" "$desired" "$count"
    update="$(aws eks update-nodegroup-config --cluster-name "$TARGET_CLUSTER" --nodegroup-name "$group" \
      --region "$TARGET_REGION" --scaling-config "minSize=$min,maxSize=$max,desiredSize=$count" --query update.id --output text)"
    for ((i=0;i<120;i++)); do
      phase="$(aws eks describe-update --name "$TARGET_CLUSTER" --nodegroup-name "$group" --update-id "$update" --region "$TARGET_REGION" --query update.status --output text)"
      [[ "$phase" != Failed && "$phase" != Cancelled ]] || { echo 'Falha ao aumentar o node group.' >&2; return 1; }
      [[ "$phase" != Successful ]] || break
      if (( i % 6 == 0 )); then printf 'Aguardando o node group: %s\n' "$phase"; fi
      sleep 5
    done
    [[ "$phase" == Successful ]] || return 1
  fi
  for ((i=0;i<120;i++)); do
    kubectl get nodes -o json > "$WORK/nodes.json"
    ready="$(jq --arg group "$group" '[.items[] | select(.metadata.labels["eks.amazonaws.com/nodegroup"] == $group) |
      select(.spec.unschedulable != true) | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))] | length' "$WORK/nodes.json")"
    if (( ready >= count )); then break; fi
    if (( i % 6 == 0 )); then printf 'Nos Ready com agendamento liberado: %s/%s\n' "$ready" "$count"; fi
    sleep 5
  done
  (( ready >= count )) || { echo 'Nos insuficientes. Confira cordon, taints e eventos com o comando status.' >&2; return 1; }
  kubectl get nodes -o wide
}

ensure_argo() {
  for namespace in argocd toggle-master observability; do
    kubectl create namespace "$namespace" --dry-run=client -o json | kubectl apply -f -
  done
  if ! kubectl -n argocd get deployment argocd-server >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 10 --max-time 120 "https://raw.githubusercontent.com/argoproj/argo-cd/$ARGO_VERSION/manifests/install.yaml" -o "$WORK/argo.yaml"
    kubectl -n argocd apply --server-side --field-manager=academy-bootstrap -f "$WORK/argo.yaml"
  fi
  kubectl wait --for=condition=Established crd/applications.argoproj.io crd/applicationsets.argoproj.io --timeout=120s
  # TLS nativo do Argo, acessivel pelo DNS do seu Load Balancer.
  kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{"data":{"server.insecure":"false"}}'
  kubectl -n argocd patch service argocd-server --type merge -p '{"metadata":{"annotations":{"service.beta.kubernetes.io/aws-load-balancer-scheme":"internet-facing"}},"spec":{"type":"LoadBalancer"}}'
  kubectl -n argocd rollout restart deployment/argocd-server
  for deployment in argocd-server argocd-repo-server argocd-applicationset-controller; do
    kubectl -n argocd rollout status "deployment/$deployment" --timeout=300s
  done
  kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
}

reserve_agent_slots() {
  local move namespace deployment node strategy old_pod remaining
  for ((attempt=0;attempt<15;attempt++)); do
    kubectl get nodes -o json > "$WORK/nodes.json"
    kubectl get pods -A -o json > "$WORK/pods.json"
    kubectl get replicasets -A -o json > "$WORK/rs.json"
    move="$(python3 "$ROOT/academy/capacity.py" "$WORK/nodes.json" "$WORK/pods.json" "$WORK/rs.json")"
    if [[ "$move" == null ]]; then return; fi
    namespace="$(jq -r .namespace <<< "$move")"
    deployment="$(jq -r .deployment <<< "$move")"
    strategy="$(kubectl -n "$namespace" get deployment "$deployment" -o jsonpath='{.spec.strategy.type}')"
    [[ "$strategy" == RollingUpdate ]] || { echo 'Redistribuicao exige RollingUpdate; nenhum pod foi excluido.' >&2; return 1; }
    while IFS= read -r node; do
      held_nodes+=("$node")
      kubectl cordon "$node"
    done < <(jq -r '.hold[]' <<< "$move")
    printf 'Liberando vaga para agentes: redistribuindo %s/%s por rollout.\n' "$namespace" "$deployment"
    kubectl -n "$namespace" rollout restart "deployment/$deployment"
    kubectl -n "$namespace" rollout status "deployment/$deployment" --timeout=300s
    # Available nao implica que o pod antigo ja liberou a vaga no scheduler.
    old_pod="$(jq -r .pod <<< "$move")"
    for ((remaining=0;remaining<60;remaining++)); do
      if [[ -z "$(kubectl -n "$namespace" get pod "$old_pod" --ignore-not-found -o name)" ]]; then break; fi
      sleep 2
    done
    (( remaining < 60 )) || { echo 'O pod anterior ainda nao liberou a vaga; confira status.' >&2; return 1; }
    for node in "${held_nodes[@]}"; do kubectl uncordon "$node"; done
    held_nodes=()
  done
  echo 'Limite de redistribuicoes atingido; confira status e a capacidade do node group.' >&2; return 1
}

ensure_metrics_server() {
  local version status
  if ! kubectl -n kube-system get deployment metrics-server >/dev/null 2>&1; then
    version="$(aws eks describe-addon-versions --addon-name metrics-server --kubernetes-version "$(jq -r .cluster.version "$WORK/cluster.json")" \
      --region "$TARGET_REGION" --output json | jq -er '[.addons[].addonVersions[] | select(any(.compatibilities[]; .defaultVersion == true))][0].addonVersion')"
    aws eks create-addon --cluster-name "$TARGET_CLUSTER" --addon-name metrics-server --addon-version "$version" --region "$TARGET_REGION" >/dev/null
    for ((i=0;i<120;i++)); do
      status="$(aws eks describe-addon --cluster-name "$TARGET_CLUSTER" --addon-name metrics-server --region "$TARGET_REGION" --query addon.status --output text)"
      [[ "$status" != CREATE_FAILED && "$status" != DEGRADED ]] || { echo 'Metrics Server addon nao ficou saudavel.' >&2; return 1; }
      [[ "$status" != ACTIVE ]] || break
      if (( i % 6 == 0 )); then printf 'Aguardando Metrics Server: %s\n' "$status"; fi
      sleep 5
    done
    [[ "$status" == ACTIVE ]] || return 1
  fi
  kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
}

check_images() {
  local service tag
  for service in "${services[@]}"; do
    curl -fsSL --connect-timeout 10 --max-time 60 "https://raw.githubusercontent.com/ramondata/toggle-master-gitops/$REF/apps/$service/values.yaml" -o "$WORK/$service.yaml"
    tag="$(python3 - "$WORK/$service.yaml" <<'PY'
from pathlib import Path
import re, sys
body = Path(sys.argv[1]).read_text().split('image:', 1)[1]
match = re.search(r'^  tag:\s*[\'\"]?([a-zA-Z0-9_.-]+)', body, re.M)
if not match:
    raise SystemExit('Tag de imagem ausente no chart.')
print(match[1])
PY
)"
    if ! aws ecr describe-images --region "$TARGET_REGION" --repository-name "toggle-master/$service" --image-ids "imageTag=$tag" >/dev/null 2>&1; then
      printf 'Imagem ausente no ECR atual: %s:%s. Execute bash academy/lab.sh publish; depois git pull --ff-only e up.\n' "$service" "$tag" >&2
      return 1
    fi
    if [[ "$service" == flag-service ]]; then FLAG_IMAGE="$ACCOUNT.dkr.ecr.$TARGET_REGION.amazonaws.com/toggle-master/$service:$tag"; fi
  done
}

check_ownership() {
  local name source
  if ! kubectl get crd applications.argoproj.io >/dev/null 2>&1; then return; fi
  for name in platform "${services[@]}" observability-metrics observability-telemetry; do
    source="$(kubectl -n argocd get application "$name" --ignore-not-found -o jsonpath='{.spec.source.repoURL}')"
    if [[ -n "$source" && "$source" != https://github.com/ramondata/toggle-master-gitops.git ]]; then
      printf 'Application %s pertence a outra origem; bootstrap interrompido.\n' "$name" >&2; return 1
    fi
  done
  source="$(kubectl -n argocd get applicationset toggle-master-services --ignore-not-found -o jsonpath='{.spec.template.spec.source.repoURL}')"
  [[ -z "$source" || "$source" == https://github.com/ramondata/toggle-master-gitops.git ]] || { echo 'ApplicationSet pertence a outra origem.' >&2; return 1; }
}

apply_services() {
  CONFIG_REVISION="$(kubectl -n toggle-master get secret academy-databases -o jsonpath='{.metadata.resourceVersion}')"
  AWS_REVISION="$(kubectl -n toggle-master get secret academy-runtime -o jsonpath='{.metadata.resourceVersion}')"
  python3 "$ROOT/academy/apps.py" services --ref "$REF" --account "$ACCOUNT" --region "$TARGET_REGION" \
    --config-revision "$CONFIG_REVISION" --aws-revision "$AWS_REVISION" | kubectl apply -f -
}

wait_lb() {
  local namespace="$1" service="$2" host
  for ((i=0;i<120;i++)); do
    host="$(kubectl -n "$namespace" get service "$service" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
    if [[ -n "$host" ]]; then printf '%s' "$host"; return; fi
    if (( i % 6 == 0 )); then printf 'Aguardando Load Balancer de %s...\n' "$service" >&2; fi
    sleep 5
  done
  printf 'Load Balancer de %s nao recebeu DNS. Confira tags das subnets e eventos do Service.\n' "$service" >&2
  return 1
}

tls_secret() {
  local host="$1"
  if kubectl -n observability get secret grafana-tls -o jsonpath='{.data.tls\.crt}' > "$WORK/current.b64" 2>/dev/null; then
    openssl base64 -d -A -in "$WORK/current.b64" -out "$WORK/current.crt"
    if openssl verify -CAfile "$WORK/current.crt" -verify_hostname "$host" "$WORK/current.crt" >/dev/null 2>&1 &&
       openssl x509 -checkend 86400 -noout -in "$WORK/current.crt" >/dev/null 2>&1; then return; fi
  fi
  [[ "$host" =~ ^[a-zA-Z0-9.-]+$ ]] || return 2
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 30 -keyout "$WORK/tls.key" -out "$WORK/tls.crt" \
    -subj /CN=grafana-lab -addext "subjectAltName=DNS:$host" >/dev/null 2>&1
  kubectl -n observability create secret tls grafana-tls --cert="$WORK/tls.crt" --key="$WORK/tls.key" --dry-run=client -o json |
    kubectl apply --server-side --field-manager=fase4-bootstrap -f -
}

init_db() {
  kubectl -n toggle-master delete job academy-db-init --ignore-not-found=true --wait=true
  python3 "$ROOT/academy/runtime.py" init-manifest "$FLAG_IMAGE" | kubectl apply -f -
  for ((i=0;i<90;i++)); do
    kubectl -n toggle-master get job academy-db-init -o json > "$WORK/job.json"
    if jq -e 'any(.status.conditions[]?; .type == "Complete" and .status == "True")' "$WORK/job.json" >/dev/null; then
      kubectl -n toggle-master logs job/academy-db-init; return
    fi
    if jq -e 'any(.status.conditions[]?; .type == "Failed" and .status == "True")' "$WORK/job.json" >/dev/null; then
      kubectl -n toggle-master logs job/academy-db-init || true; return 1
    fi
    if (( i % 6 == 0 )); then echo 'Aguardando inicializacao dos schemas RDS...'; fi
    sleep 3
  done
  echo 'Tempo limite na inicializacao RDS. Confira senha e security groups EKS -> RDS:5432.' >&2; return 1
}

addresses() {
  for pair in argocd/argocd-server observability/grafana; do
    host="$(kubectl -n "${pair%/*}" get service "${pair#*/}" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
    printf '%s: https://%s\n' "$pair" "${host:-aguardando-DNS}"
  done
}

connect
case "$mode" in
  up)
    trap diagnose ERR
    check_ownership
    check_images
    ensure_capacity
    ensure_argo
    ensure_metrics_server
    python3 "$ROOT/academy/runtime.py" configure
    python3 "$ROOT/academy/runtime.py" runtime
    python3 "$ROOT/academy/runtime.py" integrations
    # Reduz as replicas anteriores e prepara os bancos antes de reservar
    # uma vaga por no para cada DaemonSet novo.
    apply_services
    wait_app platform
    init_db
    for service in "${services[@]}"; do wait_app "$service"; kubectl -n toggle-master rollout status "deployment/$service" --timeout=300s; done
    reserve_agent_slots
    host="$(kubectl -n observability get service grafana --ignore-not-found -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
    host="${host:-grafana-lab.invalid}"
    tls_secret "$host"
    python3 "$ROOT/academy/apps.py" metrics --ref "$REF" --host "$host" | kubectl apply -f -
    wait_app observability-metrics
    host="$(wait_lb observability grafana)"
    tls_secret "$host"
    python3 "$ROOT/academy/apps.py" metrics --ref "$REF" --host "$host" | kubectl apply -f -
    wait_app observability-metrics
    python3 "$ROOT/academy/apps.py" telemetry --ref "$REF" --cluster "$TARGET_CLUSTER" --new-relic-region "${NEW_RELIC_REGION:-US}" | kubectl apply -f -
    wait_app observability-telemetry
    # Carrega a configuracao OTel tambem em pods de uma instalacao antiga.
    kubectl -n toggle-master rollout restart deployment/auth-service deployment/flag-service deployment/targeting-service deployment/evaluation-service deployment/analytics-service
    for service in "${services[@]}"; do wait_app "$service"; kubectl -n toggle-master rollout status "deployment/$service" --timeout=300s; done
    # Atualiza os processos que leem Secrets por env, inclusive uma instalacao anterior.
    kubectl -n observability rollout restart deployment/grafana deployment/otel-gateway deployment/self-healer
    for service in grafana otel-gateway self-healer; do kubectl -n observability rollout status "deployment/$service" --timeout=300s; done
    wait_lb argocd argocd-server >/dev/null
    kubectl -n observability get secret grafana-tls -o jsonpath='{.data.tls\.crt}' | openssl base64 -d -A > "$WORK/check.crt"
    curl -fsS --retry 12 --retry-all-errors --retry-delay 5 --max-time 10 --cacert "$WORK/check.crt" "https://$host/api/health" |
      jq -e '.database == "ok"' >/dev/null
    addresses
    echo 'Instalacao EKS concluida. Execute passwords e verify; depois valide traces e notificacoes reais.'
    ;;
  refresh)
    check_ownership
    python3 "$ROOT/academy/runtime.py" runtime
    apply_services
    for service in evaluation-service analytics-service; do wait_app "$service"; kubectl -n toggle-master rollout status "deployment/$service" --timeout=300s; done
    echo 'Sessao dos pods renovada. Para renovar tambem o CI, use publish.'
    ;;
  publish)
    if ! command -v gh >/dev/null; then bash "$ROOT/academy/install-gh.sh"; fi
    WAIT_FOR_CI=true FORCE_REPUBLISH=true bash "$ROOT/scripts/fase4-refresh-ci.sh"
    echo 'CI concluido. Execute git pull --ff-only antes de up para usar os charts atualizados.'
    ;;
  status)
    kubectl -n argocd get applications
    kubectl get pods -A -o wide
    addresses
    ;;
  passwords)
    python3 - <<'PY'
import base64, json, subprocess
for ns, name, key, label in [('argocd','argocd-initial-admin-secret','password','Argo CD'), ('observability','grafana-admin','admin-password','Grafana')]:
    p = subprocess.run(['kubectl','-n',ns,'get','secret',name,'--ignore-not-found','-o','json'],capture_output=True,text=True,check=True)
    if p.stdout.strip():
        value = base64.b64decode(json.loads(p.stdout)['data'][key]).decode()
        print(label + ' | usuario: admin | senha: ' + value)
    else:
        print(label + ': Secret inicial ausente; use a senha que ja foi definida.')
PY
    ;;
  integrations)
    ROTATE_INTEGRATIONS=true python3 "$ROOT/academy/runtime.py" integrations
    python3 "$ROOT/academy/apps.py" telemetry --ref "$REF" --cluster "$TARGET_CLUSTER" --new-relic-region "${NEW_RELIC_REGION:-US}" | kubectl apply -f -
    wait_app observability-telemetry
    kubectl -n observability rollout restart deployment/grafana deployment/otel-gateway deployment/self-healer
    for service in grafana otel-gateway self-healer; do kubectl -n observability rollout status "deployment/$service" --timeout=300s; done
    ;;
  databases)
    check_ownership
    python3 "$ROOT/academy/runtime.py" databases
    apply_services
    check_images
    init_db
    for service in "${services[@]}"; do wait_app "$service"; done
    ;;
  verify)
    bash "$ROOT/scripts/fase4-preflight.sh"
    python3 "$ROOT/scripts/fase4-smoke.py"
    bash "$ROOT/scripts/fase4-verify.sh"
    ;;
  demo) bash "$ROOT/scripts/fase4-demo.sh" "${2:-status}" ;;
esac

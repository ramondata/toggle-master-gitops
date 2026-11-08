# Fase 4 no AWS Academy / EKS

Este e o caminho de entrega. O bootstrap usa AWS real: EKS, ECR, RDS,
ElastiCache, SQS e DynamoDB. Os manifests locais nao sao usados.

O Terraform no repositorio `ramondata/terraform_tc_postech_fiap`, ambiente
`env/dev`, continua responsavel pela infraestrutura AWS. Este script configura
o Kubernetes **depois** que o EKS e os servicos de dados existem; nao executa
`terraform apply`, nao recria VPC/bancos e nao transfere os dados locais.

## Primeira execucao

Inicie o lab e abra o **AWS CloudShell** em `us-east-1`. Use uma sessao com
permissao de administracao do seu EKS. Nao coloque credenciais no Git.
CloudShell ja oferece AWS CLI, kubectl, Git, Python, jq, curl e OpenSSL.
Nao precisa instalar Helm, Argo CLI, Docker ou kind no CloudShell.

```bash
git clone https://github.com/ramondata/toggle-master-gitops.git
cd toggle-master-gitops
```

Se ja tem o checkout, entre nele e execute `git pull --ff-only`.

1. Publique as versoes instrumentadas dos cinco servicos pelo CI existente.
   O comando solicita login GitHub de `ramondata`, atualiza o Secret
   `AWS_ACADEMY_CREDENTIALS`, reexecuta as publicacoes e aguarda o resultado.
   Instala GitHub CLI (`gh`) no diretorio do usuario se estiver ausente,
   usando binario oficial com versao e checksum fixos.
   A deploy key `GITOPS_DEPLOY_KEY` dos servicos precisa continuar cadastrada.

   ```bash
   bash academy/lab.sh publish
   git pull --ff-only
   ```

2. Configure o cluster e instale a stack:

   ```bash
   bash academy/lab.sh up
   ```

   Na primeira vez, o script solicita a senha master dos RDS (ou URLs privadas
   se a descoberta nao for suficiente) e as chaves de New Relic, PagerDuty e
   Discord. A entrada fica oculta. Nas proximas execucoes esses valores sao
   reaproveitados dos Secrets. Para New Relic europeu, use
   `NEW_RELIC_REGION=EU bash academy/lab.sh up`.

3. Consulte acesso e valide o funcionamento:

   ```bash
   bash academy/lab.sh status
   bash academy/lab.sh passwords
   bash academy/lab.sh verify
   ```

O comando `up` imprime as URLs HTTPS dos Load Balancers de Argo CD e Grafana.
Usuario `admin` em ambos. Os certificados sao autoassinados para o laboratorio;
o navegador pede aceitacao do certificado. As senhas sao exibidas somente
pelo comando `passwords`, no seu terminal.

`verify` usa a API key do Secret, cria flag/regra temporarias, envia uma avaliacao
real e procura **o evento exclusivo desse teste** no DynamoDB. A flag/regra
sao removidas ao final; o evento permanece como evidencia. Tambem verifica
metricas dos cinco servicos e logs. Nao injeta falha automaticamente.

## O que `up` configura

- Confirma conta, regiao e endpoint EKS; usa kubeconfig separado em
  `$HOME/.toggle-academy/<conta>/<cluster>/kubeconfig`.
- Garante pelo menos **3 nos** no managed node group, sem reduzir grupos maiores.
  Isso consome creditos e evita o limite anterior de 17 pods/no. O script nao
  remove taints nem desbloqueia nos em manutencao. Mais de um node group exige
  `TARGET_NODEGROUP=<nome>`.
- Instala Argo CD `v3.5.2` se estiver ausente. Reaproveita instalacoes existentes
  e prepara o `argocd-server` com TLS e Service LoadBalancer.
- Instala Metrics Server como add-on EKS compativel, somente se estiver ausente.
- Reserva as vagas dos DaemonSets de metricas/logs. Se um no antigo estiver
  lotado, usa cordon temporario e rollout de Deployments conhecidos do projeto
  para mover a replica a outro no. Restaura apenas os cordons que ele mesmo
  fez, inclusive em erro; nao apaga pods de outros projetos nem usa drain.
- Prepara `academy-databases`, `academy-runtime`, `fase4-demo`,
  `fase4-integrations` e `grafana-admin` no cluster.
- Registra Applications no Argo CD para Prometheus/Grafana, Loki/Collectors,
  alertas/self-healer e os cinco microsservicos. Cada servico comeca com uma
  replica; os dois HPAs podem chegar a duas replicas cada.
- Cria databases/tabelas ausentes no RDS e registra o hash da API key usada
  pelos servicos. Nao apaga os dados existentes. Os SQLs correspondem aos
  schemas dos servicos; nao sao um framework de migracoes.
- Publica Grafana com TLS nativo e seu proprio LoadBalancer. Prometheus,
  Loki e o webhook de recuperacao continuam internos.
- Aguarda o Argo observar a configuracao atual, os rollouts e o health do Grafana.

O bootstrap nao precisa de um novo Ingress Controller. `platform` fica com
`serviceIngressEnabled=false`; os testes usam os Services internos. O NGINX
antigo, se existir, nao e removido pelo script. Seu Load Balancer antigo pode
continuar consumindo creditos ate ser desativado apos conferir seus usos.

Os charts e a configuracao continuam no Git; o Argo aplica o estado declarado.
Os overrides de conta/regiao/Secrets sao gerados pelo bootstrap na ApplicationSet.
Depois de bootstrap, use este comando para atualiza-la, em vez de reaplicar
`argocd/applicationset.yaml`, que pertence ao fluxo anterior.

## Cada nova sessao Academy

Depois de iniciar/renovar a sessao AWS no CloudShell:

```bash
cd toggle-master-gitops
git pull --ff-only
bash academy/lab.sh refresh
```

`refresh` exporta a sessao atual da AWS CLI, atualiza `academy-runtime` e provoca
rollout de evaluation/analytics. A chave temporaria nos pods expira junto com a
sessao: iniciar o lab novamente nao a renova dentro dos containers sozinho.
O comando preserva as conexoes RDS/Redis, a fila e a tabela.

Para voltar a publicar imagens, use `bash academy/lab.sh publish` para atualizar
tambem as credenciais do GitHub. Nenhum arquivo de credenciais entra no checkout.

## Recursos e parametros

O padrao e `TARGET_CLUSTER=toggle-cluster`, `TARGET_REGION=us-east-1`,
`GITOPS_REF=master` e `ACADEMY_NODE_COUNT=3`.
`ACADEMY_NODE_COUNT` e um minimo de nos, nao uma reducao automatica de custo.
O bootstrap pode aumentar desired/max no EKS; um futuro `terraform apply` pode
reaplicar o tamanho declarado no Terraform. Revise o plano se voltar a aplicar IaC.

Conexoes existentes sao reaproveitadas. Para um lab novo, a descoberta reconhece:

- RDS `auth_db`, `flag_db`/`flags_db`, `target_db`/`targeting_db` na VPC do EKS.
  No layout Terraform com RDS auth/target, cria `flags_db` no RDS de auth.
- SQS `toggle-master-sqs` ou `feature-evaluation-events` se apenas uma existir.
- DynamoDB `dev-dynamodb` ou `user-events` se apenas uma existir; chave HASH `event_id`.
- ElastiCache replication group `dev-redis`.

Se houver ambiguidade, informe os nomes (nao sao credenciais):

```bash
SQS_QUEUE_NAME=toggle-master-sqs \
AWS_DYNAMODB_TABLE=dev-dynamodb \
REDIS_REPLICATION_GROUP=dev-redis \
bash academy/lab.sh up
```

Tambem aceita `AWS_SQS_URL` e `REDIS_URL`. URLs privadas com senha sao solicitadas
por `bash academy/lab.sh databases`, com entrada oculta. Esse comando preserva
a API key e o master-key existentes, reexecuta o Job de schemas e aguarda Argo.

Security groups/rotas precisam permitir EKS -> RDS:5432, Redis:6379 e saida HTTPS
para AWS, GitHub, registries e integracoes. O script nao amplia regras de rede
automaticamente. Em falhas, imprime pods/eventos sem exibir Secrets.

## Integracoes e demonstracao

Contas externas: New Relic **INGEST license key** (US/EU), PagerDuty Service
com **Events API v2 routing key** e Discord com webhook de canal de teste.

Para trocar chaves, use `bash academy/lab.sh integrations` (ou
`NEW_RELIC_REGION=EU bash academy/lab.sh integrations`).

```bash
bash academy/lab.sh demo start
bash academy/lab.sh demo logs
# Em outro terminal, no mesmo checkout/sessao CloudShell:
bash academy/lab.sh demo fail
```

O ultimo comando provoca erro de negocio mantendo `/health` OK. Acompanhe o
alerta Grafana, incidente PagerDuty, mensagem Discord e restart automatico.
`demo recover` desativa a falha manualmente em emergencia.
Para repetir a carga, aguarde o Job anterior terminar e remova apenas
`fase4-demo` com kubectl usando o kubeconfig deste lab.

O roteiro de evidencia continua em [04-operacao-final.md](../docs/fase4/04-operacao-final.md).
O teste CI verifica scripts, render e Grafana TLS em Kubernetes descartavel;
nao comprova acesso EKS, saldo, permissoes Academy, ingestao New Relic ou entrega
real de notificacoes. Esses itens exigem a execucao no seu lab.

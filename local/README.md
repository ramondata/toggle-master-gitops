# Ambiente de desenvolvimento historico

Para a entrega da Fase 4 use o [bootstrap AWS Academy/EKS](../academy/README.md).
Este laboratorio local nao atende a exigencia de cloud gerenciada do trabalho.

# Fase 4 local: Floci + Kubernetes

Este ambiente nao usa a conta AWS nem consome creditos Academy. Usa Kubernetes
real em Docker (kind), com Floci emulando SQS e DynamoDB. PostgreSQL e Redis rodam
como containers. Os cinco servicos e a observabilidade continuam no Kubernetes,
reconciliados pelo Argo CD. Nao representa EKS gerenciado em nuvem.

## Preparar o Mac

1. Abra o Docker Desktop. Em Settings > Resources, reserve pelo menos 8 GiB de RAM
   (12 GiB se disponiveis), 4 CPUs e espaco para imagens/volumes. Essa e uma
   estimativa inicial; observe o consumo. O script verifica a memoria do Docker.
2. Instale as ferramentas (Homebrew precisa estar instalado):

```bash
brew install kind kubectl helm python
```

3. Baixe o repositorio em uma pasta nova para nao alterar seu checkout AWS:

```bash
git clone https://github.com/ramondata/toggle-master-gitops.git toggle-master-local
cd toggle-master-local
bash local/lab.sh up
```

O bootstrap constroi as cinco imagens no proprio Mac e importa no kind. As revisoes
estao fixadas em `sources.lock` e nos valores do chart. Nao usa ECR nem roda o
workflow de publicacao AWS. A primeira execucao precisa baixar varias imagens.

O kubeconfig e separado em `~/.toggle-floci-lab/kubeconfig`. Todos os comandos do
script usam explicitamente `kind-toggle-floci`. Seus contextos EKS sao preservados.
Nao aplique o ApplicationSet AWS neste cluster local.

## Acessar

- Grafana: http://localhost:3000
- Argo CD: http://localhost:8080
- Usuario: `admin`. Mostre as senhas somente no terminal, fora da gravacao:

```bash
bash local/lab.sh passwords
bash local/lab.sh status
```

As portas sao ligadas somente ao loopback do computador. O HTTP e as credenciais
locais publicas do PostgreSQL/API sao exclusivos desse laboratorio. Nao exponha
essa configuracao na internet. SQS, DynamoDB, PostgreSQL e Redis ficam internos.

## O que cada comando faz

`up`: cria um cluster dedicado, instala Argo CD, prepara PostgreSQL/Redis/Floci,
cria fila e tabela, constroi imagens e registra tres Applications. As Applications
apontam para a revisao Git do checkout; para atualizar, puxe a nova revisao e rode
`up` novamente. O Argo reconcilia essa revisao fixada.

`demo start`: cria uma flag/regra exclusivas e gera uma requisicao por segundo
por oito minutos. Ao terminar, remove essas duas fixtures. Os eventos no DynamoDB
permanecem para evidencia. Se interromper o Job, pode haver fixtures remanescentes.

`demo fail`: ativa erro HTTP 500 apenas em `/evaluate`. Os health checks seguem
saudaveis. A recuperacao deve vir do alerta do Grafana e do self-healer, nao do
liveness probe. `demo recover` desativa manualmente a falha se precisar.

```bash
bash local/lab.sh demo start
bash local/lab.sh demo logs
# Em outro terminal, na mesma pasta:
bash local/lab.sh demo fail
bash local/lab.sh demo status
```

O teste completo `bash local/verify.sh` gera carga e injeta uma falha local.
Verifica HTTP 200, eventos no DynamoDB, restart automatico e retorno ao HTTP 200.
Execute sem outro Job `fase4-demo` em andamento. Ele termina em oito minutos e
some uma hora depois; para repetir antes, remova apenas o Job concluido.
O self-healer possui cooldown de dez minutos entre restarts.

## Integracoes externas (obrigatorias na entrega)

A base inicia com integracoes externas desativadas explicitamente. Metricas,
logs, dashboard, alerta e restart funcionam localmente. Traces vao ao exporter
`debug` do Collector, sem armazenamento consultavel ou Service Map comercial.
Nenhuma notificacao real e enviada nesse modo; ele NAO conclui esses requisitos.

Crie as integracoes New Relic (INGEST license key), PagerDuty (Events API v2) e
Discord (webhook de um canal), depois execute:

```bash
bash local/lab.sh integrations
# Se a conta New Relic for europeia:
# NEW_RELIC_REGION=EU bash local/lab.sh integrations
```

Os valores sao digitados ocultos no terminal e salvos em Kubernetes Secrets, nunca
no Git. Nao envie as chaves no chat. Confira depois um trace distribuido, Service
Map, incidente no PagerDuty e mensagem no Discord. Exigem internet e contas
externas; planos/limites dessas plataformas sao independentes da AWS.

## Persistencia e limites

PostgreSQL e Floci usam volumes locais persistentes no kind. Parar/reiniciar o
Docker preserva os volumes; excluir o cluster pode apagar os dados. Redis e cache
sao descartaveis. Prometheus, Loki e Grafana mantem as escolhas de laboratorio:
dados temporarios, configuracoes/dashboards recuperados do Git. Salve prints e
video antes de recriar o cluster.

Nao ha autoscaling de maquinas, Load Balancer AWS, RDS gerenciado ou Terraform
AWS real nesse modo. Preserve o repositorio Terraform e as evidencias anteriores
do EKS; descreva a substituicao no PDF.

## Validacao e entrega

A esteira `Validate local Floci lab` constroi imagens e executa o cluster de teste.
Nao usa credenciais externas e nao comprova New Relic/PagerDuty/Discord. A
arquitetura ARM64 do Mac ainda deve ser verificada na sua maquina, mesmo quando
a esteira AMD64 passar. Configuracao pronta nao significa implantacao no seu Mac.

Depois de validar, grave dashboard, logs, trace e Service Map; injete uma falha e
mostre alerta, incidente/chat, restart automatico e recuperacao. Finalize o PDF
com integrantes, links, arquitetura, justificativas e evidencias. O video tem
limite de 25 minutos conforme o enunciado.


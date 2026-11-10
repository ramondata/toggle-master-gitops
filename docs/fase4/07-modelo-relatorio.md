# ToggleMaster — Tech Challenge Fase 4

**Rascunho para preencher apos a demonstracao. Nao e evidencia de execucao.**

## Identificacao

| Nome completo | RM | Username GitHub |
|---|---|---|
| [PREENCHER] | [PREENCHER] | [PREENCHER] |

Turma/grupo: [PREENCHER]. Data e horario da demonstracao: [PREENCHER].

## Links

- Video (ate 25 minutos, acesso habilitado): [PREENCHER]
- Terraform: https://github.com/ramondata/terraform_tc_postech_fiap
- GitOps: https://github.com/ramondata/toggle-master-gitops
- Apps: https://github.com/ramondata/auth-service,
  https://github.com/ramondata/flag-service,
  https://github.com/ramondata/targeting-service,
  https://github.com/ramondata/evaluation-service,
  https://github.com/ramondata/analytics-service (branch aws-cloud-k8s).

Substituir os links caso a demonstracao use forks de outro integrante.

## Arquitetura e justificativas

Infraestrutura AWS provisionada por Terraform, cinco microsservicos
conteinerizados com CI/CD e DevSecOps e implantacao no EKS pelo Argo CD.
Os charts de observabilidade ficam no repositorio GitOps.

O OpenTelemetry Collector centraliza recebimento OTLP, processamento em lote
e encaminhamento de metricas, logs e traces. Prometheus armazena metricas,
Loki centraliza logs e Grafana apresenta o dashboard e avalia os alertas.
Um Collector por no coleta logs stdout/stderr dos containers; o gateway recebe
esses logs e a telemetria instrumentada dos cinco servicos.

Escolhemos New Relic para o APM comercial e a visualizacao de traces e do
Service Map com ingestao OTLP. Escolhemos PagerDuty para receber eventos de
incidente pela Events API v2. Discord fornece o canal de ChatOps. Estas sao as
alternativas selecionadas dentre as opcoes permitidas; nao foi necessario
implantar Datadog e OpsGenie adicionalmente.

A regra EvaluationHighErrorRate detecta taxa HTTP 5xx acima de 5%, usa janela
de 2min e pendencia de 45s. Um webhook autenticado chama o runbook que envia
notificacoes e solicita restart do evaluation-service. RBAC limita o PATCH a
esse Deployment; cooldown de 10min reduz repeticoes. A recuperacao e
comprovada pelo rollout e retorno HTTP 200, alem da solicitacao do restart.

## Evidencias reais

1. **Dashboard Grafana:** [INSERIR PRINT 1]. Legenda com data, cluster e metricas
   exibidas. Trecho do video: [MINUTO].
2. **Trace distribuido no New Relic:** [INSERIR PRINT 2]. Trace ID: [PREENCHER].
   Servicos visiveis: [PREENCHER APOS CONFERIR]. Service Map no video: [MINUTO].
3. **Notificacao no Discord:** [INSERIR PRINT 3]. Incidente PagerDuty correlato:
   [ID/LINK SEM CREDENCIAIS]. Alerta Firing no video: [MINUTO].
4. **Automacao de self-healing:** [INSERIR PRINT 4]. Mostrar eventos do runbook,
   horario do rollout e evidencia de retorno HTTP 200. Video: [MINUTO].

## Resultado da validacao

| Criterio | Resultado observado / evidencia |
|---|---|
| EKS, Terraform, CI/CD com DevSecOps e Argo | [PREENCHER] |
| Cinco servicos e fluxo SQS/DynamoDB | [PREENCHER] |
| Metricas, logs e roteamento pelo OTel | [PREENCHER] |
| Trace e Service Map dos cinco servicos | [PREENCHER] |
| Alerta, incidente e mensagem recebida | [PREENCHER] |
| Restart automatico e recuperacao | [PREENCHER] |

Aceite HTTP das APIs externas nao e, sozinho, comprovacao de incidente
aberto/mensagem recebida. Registrar esses resultados apos conferencia nas telas.

## Limites do laboratorio

Configuracao para demonstracao: poucas replicas, certificados autoassinados e
armazenamento temporario de Prometheus/Loki/Grafana. As sessoes Academy expiram
e exigem renovacao das credenciais dos pods. Esses limites nao dispensam
comprovar todos os requisitos do enunciado na execucao apresentada.

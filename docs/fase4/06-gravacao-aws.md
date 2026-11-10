# Fase 4: preparar, comprovar e gravar no AWS Academy

Este roteiro usa EKS real. Os testes de CI nao substituem a demonstracao na AWS.
O projeto so esta pronto para entrega quando as verificacoes automaticas E as
confirmacoes visuais abaixo tiverem sido realizadas. Nao preencher evidencias
com resultados de exemplo.

## Antes de gravar

No CloudShell, com a infraestrutura Terraform existente e o lab ativo, entre
no checkout GitOps e execute:

```bash
git pull --ff-only
bash academy/lab.sh publish
git pull --ff-only
bash academy/lab.sh up
bash academy/lab.sh finish
```

`publish` e necessario se as imagens instrumentadas ainda nao foram publicadas
na conta atual. `up` instala/configura a stack e pede as chaves privadas no
terminal. Se o ambiente ja esta instalado e apenas a sessao mudou, use
`bash academy/lab.sh refresh` antes de `finish`.

`finish` faz o teste funcional real, verifica metricas/logs e cria carga. Depois
injeta falha, procura HTTP 5xx, webhook Firing, aceite HTTP das APIs PagerDuty e
Discord, restart automatico, rollout e HTTP 200 novo. Salva `summary.json`,
`self-healing.jsonl` e `load.jsonl` em diretorio privado fora do checkout,
imprimindo o caminho. Em falha tenta desativar a injecao manualmente e retorna
erro: essa recuperacao de emergencia NAO comprova self-healing.

Uma resposta 202 do PagerDuty ou 204 do Discord significa aceite pela API.
Confirme tambem o incidente e a mensagem nas telas. O script nao acessa a API
de leitura do New Relic e nao comprova Service Map nem traces no APM.

Antes de ligar a camera:

1. `bash academy/lab.sh status`: obtenha URLs HTTPS de Argo CD e Grafana.
2. `bash academy/lab.sh passwords`: consulte senhas e faca login antes de gravar.
3. Abra as abas GitHub, AWS EKS, Argo CD, Grafana, New Relic, PagerDuty e Discord.
4. No New Relic, selecione a conta correta, APM & Services e periodo recente
   (30 minutos). Confirme os cinco servicos e o mapa de dependencias. Copie um
   `trace_id` do teste e encontre o trace completo. A primeira avaliacao de uma
   flag nova evita o cache; a propagacao assincrona ate analytics pode demorar.
5. Confirme no PagerDuty o incidente do teste e no Discord a mensagem recebida.
6. Aguarde normalizar o alerta e pelo menos 10 minutos desde o ultimo restart
   automatico antes de repetir a falha na gravacao (cooldown do runbook).
7. Se `fase4-demo` ainda estiver ativo, aguarde a carga encerrar. Nao interrompa
   a limpeza das fixtures. Antes da nova carga, remova apenas o Job finalizado:

   ```bash
   account="$(aws sts get-caller-identity --query Account --output text)"
   export KUBECONFIG="${ACADEMY_STATE_DIR:-$HOME/.toggle-academy/$account/${TARGET_CLUSTER:-toggle-cluster}}/kubeconfig"
   kubectl -n toggle-master delete job fase4-demo --cascade=foreground --wait=true
   ```

Se algum desses itens falhar, corrija antes de gravar. Nao gravar o comando
`passwords`, chaves comerciais nem conteudo de Secrets.

## Roteiro de video: alvo de 15 a 20 minutos, limite de 25

| Ordem | Acessar | Mostrar e falar |
|---|---|---|
| 1 — 2min | GitHub e console AWS > EKS > toggle-cluster | Terraform da infraestrutura; cinco repos de apps; pipeline de CI/CD aprovada com etapas DevSecOps; GitOps com os charts. Mostrar o cluster real ACTIVE e os nodes. |
| 2 — 1min | URL do Argo CD | As seis Applications de plataforma/servicos e as duas de observabilidade Synced/Healthy. Explicar que o Argo aplica os manifests do Git. |
| 3 — preparar | CloudShell | `bash academy/lab.sh status`: mostrar pods prontos. Usar o teste previo finish para conferir dados recentes. Deixar dois terminais preparados para a carga e a falha. |
| 4 — 2min | Grafana > Dashboards > ToggleMaster - Fase 4 | CPU, memoria, pods, requisicoes por servico e painel de logs com dados recentes. Salvar print 1. |
| 5 — 1min | Grafana > Explore > Loki | Consultar `{service_namespace="toggle-master"}`; ativar Live. Para logs fora dos apps, consultar `{k8s_namespace_name="kube-system"}`. Explicar coleta de stdout/stderr pelo agente em cada no. |
| 6 — 1 a 2min | CloudShell e New Relic > APM & Services | Agora executar `bash academy/lab.sh demo start` e, no outro terminal, `bash academy/lab.sh demo logs`. Mostrar HTTP 200, copiar o primeiro trace_id e abri-lo no APM. Mostrar o Service Map dos cinco servicos e o processamento assincrono no analytics. Salvar print 2. A carga dura 8min: seguir direto para a falha. |
| 7 — 3 a 5min | CloudShell + Grafana Alerting + PagerDuty + Discord | Executar `bash academy/lab.sh demo fail`. Mostrar 5xx na carga; regra EvaluationHighErrorRate passando a Firing; incidente aberto e mensagem no Discord. Salvar print 3. |
| 8 — 1min | CloudShell | `bash academy/lab.sh healer-logs`: mostrar alert_received, notification_sent e restart_requested. `bash academy/lab.sh status`: mostrar pod novo e pronto. Logs da carga devem voltar a 200. Salvar print 4. Nao executar restart manual. |
| 9 — 1min | Grafana e PagerDuty | Mostrar normalizacao do alerta e, quando processada, resolucao do incidente. Fechar com as escolhas tecnicas. |

Se a carga terminar antes da prova do incidente, espere a limpeza e prepare
outra carga. Em emergencia, `bash academy/lab.sh demo recover` desativa a falha,
mas esse comando nao deve ser apresentado como recuperacao automatica.

Fala curta sobre a arquitetura:

> Os cinco servicos enviam telemetria padronizada ao OpenTelemetry Collector.
> As metricas vao para o Prometheus, os logs para o Loki e os traces para o
> New Relic. O Grafana consulta metricas e logs. Quando a taxa de erros do
> evaluation passa de 5% durante a janela configurada, o Grafana chama um
> webhook autenticado. Ele envia eventos ao PagerDuty e ao Discord e solicita
> restart somente do evaluation. O cooldown de 10min reduz restarts repetidos.

## O que fornecer para montar o PDF final

- Nome completo, RM e username GitHub de cada integrante.
- Turma, nome do grupo e data da demonstracao.
- Links dos repositorios usados, incluindo Terraform, GitOps e os cinco apps.
- Link do video, com acesso liberado para o avaliador.
- Quatro prints reais obrigatorios: dashboard Grafana; trace distribuido no
  New Relic; mensagem de incidente no Discord; log/execucao do self-healer.
- Conta/regiao New Relic e nome do servico PagerDuty, sem chaves ou senhas.
- Opcional: prints adicionais do Service Map, incidente PagerDuty e EKS/Argo.
- Saida de `finish`/resumo das verificacoes para conferir datas e resultados.

Use o [modelo do relatorio](07-modelo-relatorio.md). Preencha resultados somente
apos observar as telas. Os quatro prints nao substituem o video completo.

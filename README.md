# toggle-master-gitops

## Fase 4 — instalacao no AWS Academy

O caminho de entrega e **AWS real / EKS**. No CloudShell, apos criar/recuperar
a infraestrutura pelo Terraform e publicar as imagens:

```bash
git pull --ff-only
bash academy/lab.sh up
bash academy/lab.sh passwords
bash academy/lab.sh verify
```

Para renovar a sessao dos pods sem reinstalar: `bash academy/lab.sh refresh`.
O [guia AWS Academy](academy/README.md) inclui publicacao, configuracao inicial,
credenciais, capacidade dos nos, URLs e demonstracao.
O laboratorio em `local/` e apenas uma referencia de desenvolvimento e nao e o
ambiente de entrega da fase 4.

GitOps do ToggleMaster. O ApplicationSet em `argocd/applicationset.yaml`
acompanha `master` e inclui platform, auth-service, flag-service,
targeting-service, evaluation-service e analytics-service.

## Imagens publicadas pelo CI

As esteiras publicam as imagens no ECR e atualizam `image.repository` e
`image.tag` em `apps/<serviço>/values.yaml`. Todos os Deployments, incluindo
analytics-service, usam esses campos e `image.pullPolicy`.

Os valores versionados devem corresponder a imagens já publicadas. A validação
do chart usa um endereço fictício e uma tag de teste somente durante a renderização.

Os workflows de publicação utilizam `AWS_ACADEMY_CREDENTIALS` e
`GITOPS_DEPLOY_KEY`, configurados nos repositórios dos microsserviços.
Atualizações concorrentes no GitOps devem passar por rebase e repetição do push.

## Configuração de execução

Os templates referenciam os Secrets `academy-databases` e `academy-runtime`.
Eles precisam existir no namespace de destino antes de os serviços iniciarem.
Os valores privados de conexão e autenticação são fornecidos no cluster.

Os parâmetros Helm `academyConfigRevision` e `academyAwsRevision` permitem
provocar rollout quando a configuração externa muda. O ApplicationSet preserva
as réplicas controladas pelos HPAs de evaluation-service e analytics-service.

## Validação dos charts

O workflow `validate-charts.yml` executa `helm lint` e `helm template`
para os cinco charts de microsserviços. Cada job fornece um repositório e uma
tag de teste e verifica se essa imagem aparece no manifesto renderizado.

Os testes validam os arquivos sem acessar o cluster ou publicar imagens.

# toggle-master-gitops

GitOps existente do ToggleMaster. O ApplicationSet em `argocd/applicationset.yaml`
acompanha a branch `master` e inclui platform, auth-service, flag-service,
targeting-service, evaluation-service e analytics-service.

## Imagens publicadas pelo CI

Os workflows dos microsserviços autenticam no ECR com o secret
`AWS_ACADEMY_CREDENTIALS` configurado em cada repositório. Depois do push da
imagem, usam o secret `GITOPS_DEPLOY_KEY` para atualizar os campos
`image.repository` e `image.tag` de `apps/<serviço>/values.yaml` neste repositório.

A imagem e a política de pull dos Deployments usam esses valores, incluindo
analytics-service. Os valores iniciais de analytics refletem a referência que
já estava no seu Deployment; o primeiro pipeline aprovado os substituirá pelo
endereço real do ECR Academy e pela tag publicada.

Os pipelines fazem rebase e repetem até três tentativas de push para acomodar
atualizações concorrentes de serviços diferentes, sem force push. Uma falha de
merge permanece visível no job e não é sobrescrita à força.

## Validação dos charts

O workflow `validate-charts.yml` executa `helm lint` e renderiza os cinco charts
de microsserviços com um repositório e tag de teste. A validação confirma que
a imagem fornecida em `values.yaml` é usada no manifesto renderizado.
Essa validação trabalha somente com arquivos; o ArgoCD e o acesso ao cluster
são configurados separadamente.

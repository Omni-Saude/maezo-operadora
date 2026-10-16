// Material staff/human NA AWS (DL-0113; docs/runbooks/material-staff-na-aws.md; task-staff-material.tf).
//
// INERTE: tudo abaixo esta comentado, entao `staff_material = null` (default) e o `terraform plan` nao
// muda nada. Ligar e' um PR proprio (passo 1 do runbook), em duas etapas:
//   1a. descomentar SEM `image_digest`  -> so a infraestrutura (bucket de estado, CMK propria, role, log group);
//   1b. depois do build da imagem de operacao com `deploy/ops/staff-install.Dockerfile` (CodeBuild
//       `maezo-operadora-dev-imagem`), acrescentar `image_digest` -> nasce a task definition `staff-material`.
// Os ARNs sao os dos segredos VIVOS (nomes com sufixo): so leitura (a role nunca grava segredo).
//
// staff_material = {
//   // image_digest = "sha256:<64 hex da imagem de operacao NOVA, a que contem `tools.staff_ops material`>"
//   native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-XoACan"
//   staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-R8JeAL"
//   job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-glnOol"
//   rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-R8q4LK"
//   assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EJgSnC"
//   syn_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/syn-fixture-wt3sOm"
// }

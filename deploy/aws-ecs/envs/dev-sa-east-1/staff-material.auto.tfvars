// Material staff/human NA AWS (DL-0113; docs/runbooks/material-staff-na-aws.md; task-staff-material.tf).
//
// ATIVADO (1a e 1b, 16/10/2026): a infraestrutura (bucket de estado, CMK propria, role) e, com `image_digest`, o log group
// e a task definition `staff-material` (padrao `MATERIAL_MODO=verificar`, dry-run). Ligar e' um PR proprio (passo 1 do runbook), em duas etapas:
//   1a. descomentar SEM `image_digest`  -> so a infraestrutura (bucket de estado, CMK propria, role, log group);
//   1b. depois do build da imagem de operacao com `deploy/ops/staff-install.Dockerfile` (CodeBuild
//       `maezo-operadora-dev-imagem`), acrescentar `image_digest` -> nasce a task definition `staff-material`.
// Os ARNs sao os dos segredos VIVOS (nomes com sufixo): so leitura (a role nunca grava segredo).
//
staff_material = {
  image_digest          = "sha256:b5c4dbf186b542fdbddf225bb0b797fa0e15f43faf1588dd0f4ce7c3a262e0ef" // imagem de operacao 075eed3a-staff-ops (base: app c29a88ea)
  native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-XoACan"
  staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-R8JeAL"
  job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-glnOol"
  rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-R8q4LK"
  assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EJgSnC"
  syn_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/syn-fixture-wt3sOm"
}

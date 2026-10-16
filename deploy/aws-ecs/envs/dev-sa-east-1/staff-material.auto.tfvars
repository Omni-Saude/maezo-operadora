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
  image_digest          = "sha256:53d76e8c2626d671a7dec3a2dc04a119b1b897ad88c0134e41803cbc8e51cb37" // imagem de operacao 30af653b-staff-ops (base: app c29a88ea)
  native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-XoACan"
  staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-R8JeAL"
  job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-glnOol"
  rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-R8q4LK"
  assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EJgSnC"
  syn_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/syn-fixture-wt3sOm"
}

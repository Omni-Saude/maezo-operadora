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
  image_digest          = "sha256:71262cad9dfcf8671758cc90d37b4c92d66ba2f749e6a0a3c6fe1a15035d6201" // imagem de operacao 99a4d94a-staff-ops (base: app 021251a9; com --retirar-versoes, #1182; o catalogo do reembolso v2 exige os formularios BPMN_FORMDATA do app novo). Era 182e11b7-staff-material (bb19425c)
  native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-XoACan"
  staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-R8JeAL"
  job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-glnOol"
  rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-R8q4LK"
  assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EJgSnC"
  syn_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/syn-fixture-wt3sOm"
}

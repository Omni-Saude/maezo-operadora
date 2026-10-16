// Estado VIVO do dev (conta 203312548462, sa-east-1) medido em 14/10/2026.
//
// POR QUE ESTE ARQUIVO EXISTE: o ultimo `apply` do dev foi feito com um arquivo de variaveis FORA do
// repositorio (`atestacao-phi-viva.tfvars`) que nunca foi commitado e se perdeu. Sem ele, um `terraform
// plan` a partir do git dava `20 to add, 9 to change, 54 to destroy`: destruia o plano staff inteiro
// (inclusive o job T1.5 `staff-job`, que atualiza a fila do portal a cada 4 min, e o alarme dele), o
// NLB do engine nativo e o SG dele, e criava a rotina de sincronizacao do egresso Cognito, que nao
// existe na conta. Este arquivo REGISTRA o que estava ligado para que o plan a partir do git reproduza
// a AWS. Valores recuperados das task definitions vivas e do state (somente leitura), conferidos contra
// a conta.
//
// Nada aqui e' segredo: toggles, ARNs, ids de versao de segredo e digests de imagem. Os segredos em si
// vivem no Secrets Manager (CMK `engine-native`/`portal-staff`).
//
// O QUE FICA FORA (por deploy, na linha de comando): `image_tag`, `webhook_receiver_image_tag`,
// `canal_teste_image_tag`, `agents_image_digest`, `worker_image_digest`, `diagnostics_image_digest` e
// `engine_image_digest`. Ver `docs/runbooks/aws-ecs.md`.
//
// PARA MUDAR: um PR que edita ESTE arquivo, seguido de `terraform apply` pelo time. NUNCA `-var` nem
// outro arquivo avulso para estes valores: o proximo apply da main desfaria sem aviso.

// Toggle da frente "zona PHI" da Helena (antes passado por `-var` a cada deploy).
helena_zona_phi = true

// Zona PHI (Bedrock sa-east-1, Mistral). `phi_vendor_dpa_ref` e' a ATESTACAO do instrumento (sem default
// de proposito, variables.tf): vazia, a Helena cai na zona geral (`bedrock`, sem modelo, PHI_ZONE_REQUIRED
// =false). O texto abaixo e' o que as task definitions vivas carregam em MAEZO_PHI_VENDOR_DPA_REF, atestado
// no apply de 20/08/2026 por autorizacao do dono do produto. `phi_zone_provider = "bedrock_br"` e' o que
// helena/marina/rafael/webhook-receiver/agent-resume/simulador rodam hoje (o default seria `phi_zone_mock`).
phi_vendor_dpa_ref = "AWS-SERVICE-TERMS-BEDROCK-sa-east-1 (Contrato AWS vigente da AMH; atestado no apply de 20/08/2026 por autorizacao do dono do produto)"
phi_zone_provider  = "bedrock_br"

// Sincronizacao automatica do egresso Cognito (portal-egresso-cognito-sync.tf): NAO esta aplicada na
// conta (nao ha a Lambda nem o schedule). O default `enabled = true` criaria Lambda, role, schedule e
// alarme no proximo apply. Fica DESLIGADA ate' o time decidir aplica-la (PR que troca para `true`); ate'
// la vale a lista /32 de `portal.auto.tfvars`.
portal_cognito_egress_sync = {
  enabled = false
}

// ---------------------------------------------------------------------------
// Onda 3: task avulsa `staff-install` (instalacao no Aurora). Mantem viva a task, a role e o log group.
// ---------------------------------------------------------------------------
staff_install = {
  image_digest = "sha256:a20b2597f8d1d0dbdccb9ac6322b5a6e4f1c2fe8f4e87499a37f5c8e3ed8a7d2" // imagem de operacao 6bd559ec-staff-ops
  login_secret_arns = {
    maezo_native_case_issuer    = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/maezo_native_case_issuer-ahUBVX"
    maezo_native_issuer_witness = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/maezo_native_issuer_witness-Ef9Cw8"
    maezo_native_schema_owner   = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/maezo_native_schema_owner-WJD9Bd"
    portal_read_source_amh      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/portal_read_source_amh-mH9uEP"
    portal_staff_lock_amh       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-session-lock-dsn-mA0Uwe"
    portal_staff_witness_amh    = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-native-witness-dsn-TFfRhL"
  }
}

// ---------------------------------------------------------------------------
// Ondas 4-8: tasks de operacao do plano staff. `job_schedule_enabled = true` mantem o schedule
// `maezo-operadora-dev-staff-job` (rate(4 minutes)), o alarme `staff-job-2-falhas-seguidas`, o filtro
// de metrica e o ledger S3: e' o refresher da fila de casos do portal. Sem isto a fila do portal para.
// ---------------------------------------------------------------------------
staff_ops = {
  // 16/10/2026: imagem de operacao 182e11b7-staff-material (base: app 8819f3fb). O job T1.5 valida o catalogo no modelo
  // `ReadCatalogArtifact` da imagem; a base antiga (c29a88ea) nao conhece o formulario `cancel_execucao` (#788) e recusa
  // o catalogo do SP-OP-CANCEL-001 v0.2.0 (`ReadRefusalError read_dependency_unavailable`). Era 1ded30e7-staff-ops (e92a4e5e).
  // Digest UNICO das 4 tasks staff-ops (rows/syn/assignment/job).
  // Mesmas camadas-base que 6bd559ec-staff-ops (a20b2597); entre as duas so mudou deploy/sql/portal-human-plane-grants.sql
  // (#558: INSERT do outbox na cadeia de auditoria); tools/staff_ops, staff_install, staff_materials e dev_syn_fixture identicos.
  image_digest = "sha256:bb19425cda659598c3947f6ecfc7997826b2c91796418edc1e020274c9ddf384"

  rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-R8q4LK"
  syn_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/syn-fixture-wt3sOm"
  job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-glnOol"
  job_secret_version_id = "ece58f11-9f81-4034-95e2-504307b24a60"

  // Pins publicos do pacote humano (os mesmos de `portal.human` em `portal.auto.tfvars`).
  human_material_version_id = "amh-dev-human-16d34f91f2a37ec3f0512adb53"
  human_manifest_sha256     = "992858ec56e69996a42c941fde42f3aee91aa055bb1f9f6694e62e061b961449"

  job_schedule_enabled = true

  task_source_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/portal_task_source_amh-3ZLsg8"
  human_outbox_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/portal_human_outbox_amh-OZDjME"
  human_source_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/portal_human_source_amh-NPmNkv"
  assignment_admin_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-admin-CfM0vr"
  assignment_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EJgSnC"
}

// ---------------------------------------------------------------------------
// Onda 4: engine nativo (NLB interno 443, SG, Cloud Map, init `engine-native-materialize` e sidecar
// `staff-case-issuer` no cibseven). Sem isto o plan destroi o NLB que o portal staff usa.
// ---------------------------------------------------------------------------
engine_native = {
  native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-XoACan"
  native_secret_version_id = "25c14f6b-400e-4175-9399-3eea268ebd41"
  kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/49a1f9f2-9f92-44ad-9b5a-d5de269fee73" // alias/maezo-operadora-dev-engine-native
  app_image_digest         = "sha256:c29a88eafcf568b64b4ea455c954aa499964bc07e8d65088754dacce2c463d14"     // init e sidecar (imagem do app 6bd559ec)
  staff_case_issuer        = true
  assignment_trust         = true
}

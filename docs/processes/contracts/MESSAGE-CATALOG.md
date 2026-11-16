# Catálogo de mensagens e erros BPMN (versionado)

**Status:** DRAFT (v1.0.0) — inventário do que os `.bpmn` declaram hoje; nenhum `.bpmn` foi alterado para
produzi-lo. Não é contrato de processo: é o índice transversal das mensagens e erros dos contratos.
**Catálogo:** `message-catalog.v1` · **Épico:** E-SF-13 / GTM-E-BPMN-00 (pré-requisito comum de
E-BPMN-02/05/06 e E-61) · **Data:** 16/11/2026

## Para que serve

Toda `<bpmn:message name>` e todo `errorCode` de `<bpmn:error>` em `spec/processes/bpmn/*.bpmn` aparece
aqui **uma vez por processo**, com dono, uso no diagrama, correlação e versão. O teste
`tests/unit/gateway/test_esf13_message_catalog.py` relê os `.bpmn` a cada build e reprova:

- mensagem ou `errorCode` presente no `.bpmn` e ausente daqui (ou o contrário);
- id BPMN divergente entre o diagrama e a linha;
- mensagem fora da convenção `msg.<dom>.<evento>` (minúsculas, `_` dentro do segmento);
- `errorCode` fora da convenção `ERR_<MAIUSCULAS>`;
- qualquer `<bpmn:signal>` ou `<bpmn:escalation>` sem decisão registrada na seção "Sinais e escalações".

## Convenções

- **Mensagem:** `msg.<dom>.<evento>`. `<dom>` é o domínio do processo (`auth`, `cancel`, `nip`, …);
  `<evento>` é o fato que chegou, no passado ou como substantivo (`docs_received`, `notification_ack`).
- **Erro:** `ERR_<DOMINIO>_<CAUSA>`. `ERR_EVENT_PUBLISH_FAILED` é o único código compartilhado entre
  processos (falha de publicação do aviso; cada processo o declara e o trata no próprio diagrama).
- **Dono:** o processo que declara a mensagem/erro, e o contrato dele em `docs/processes/contracts/`.
  O status entre parênteses é o do cabeçalho do contrato (nenhum está promovido).
- **Correlação:** como o contrato diz que a mensagem encontra a instância. "GAP" = o contrato não diz.
- **Versão:** versão da linha no catálogo. Mudar nome, correlação ou dono de uma linha = versão nova da
  linha, registrada aqui, nunca edição silenciosa.

## Mensagens (`<bpmn:message>`)

| Mensagem | Processo | Id BPMN | Uso no BPMN | Correlação | Dono (contrato, status) | Versão |
|---|---|---|---|---|---|---|
| `msg.adequacao.rede_atualizada` | SP-OP-ADEQUACAO-001 | `Msg_AdequacaoRedeAtualizada` | boundaryEvent `BME_RedeAtualizada`; intermediateCatchEvent `ICE_AguardarInfo` | business key (`SP-OP-ADEQUACAO-001.md:224`) | SP-OP-ADEQUACAO-001 (DRAFT (v0.1.0)) | 1 |
| `msg.anssubmit.ack_received` | SP-OP-ANS-SUBMIT-001 | `Msg_AnsAckReceived` | intermediateCatchEvent `ICE_AckRecebido` | `protocolo_ans` (`SP-OP-ANS-SUBMIT-001.md:123`) | SP-OP-ANS-SUBMIT-001 (DRAFT (v0.1.0)) | 1 |
| `msg.auth.docs_received` | SP-OP-AUTH-001 | `Msg_AuthDocsRecebidos` | intermediateCatchEvent `ICE_DocsRecebidos` | business key (`SP-OP-AUTH-001.md:454`) | SP-OP-AUTH-001 (DRAFT (v0.1.0)) | 1 |
| `msg.auth.start` | SP-OP-AUTH-001 | `Msg_AuthIntakeStart` | startEvent `Start_IntakePortal` | start por mensagem — cria instancia (intake do portal, `auth.start`; `SP-OP-AUTH-001.md:207`); chave de negocio do start nao declarada no contrato (GAP) | SP-OP-AUTH-001 (DRAFT (v0.1.0)) | 1 |
| `msg.cancel.notification_ack` | SP-OP-CANCEL-001 | `Msg_CancelNotificationAck` | intermediateCatchEvent `ICE_NotificacaoAck` | business key (`SP-OP-CANCEL-001.md:147`) | SP-OP-CANCEL-001 (DRAFT (v0.2.0)) | 1 |
| `msg.cancel.info_received` | SP-OP-CANCEL-001 | `Msg_CancelInfoReceived` | boundaryEvent `BME_InfoRecebida`; intermediateCatchEvent `ICE_AguardarInfo` | business key (`SP-OP-CANCEL-001.md:148`) | SP-OP-CANCEL-001 (DRAFT (v0.2.0)) | 1 |
| `msg.contas.linhas_atualizadas` | SP-OP-CONTAS-001 | `Msg_ContasLinhasAtualizadas` | boundaryEvent `BME_LinhasAtualizadas` | business key (`SP-OP-CONTAS-001.md:161`) | SP-OP-CONTAS-001 (DRAFT (v0.1.0)) | 1 |
| `msg.cred.notification_ack` | SP-OP-CRED-001 | `Msg_CredNotificationAck` | intermediateCatchEvent `ICE_NotificacaoAck` | business key (`SP-OP-CRED-001.md:128`) | SP-OP-CRED-001 (DRAFT (v0.1.0)) | 1 |
| `msg.cred.info_received` | SP-OP-CRED-001 | `Msg_CredInfoReceived` | intermediateCatchEvent `ICE_AguardarInfoDoc`; boundaryEvent `BME_InfoDescred`; intermediateCatchEvent `ICE_AguardarInfoAdicionalDescred`; boundaryEvent `BME_InfoCred`; intermediateCatchEvent `ICE_AguardarInfoAdicionalCred` | business key (`SP-OP-CRED-001.md:129`) | SP-OP-CRED-001 (DRAFT (v0.1.0)) | 1 |
| `msg.escalation.withdrawn` | SP-OP-ESCALATION-001 | `Msg_EscalationWithdrawn` | startEvent `Start_EncerramentoAPedido` | business key `ESC-{tenant_id}-{conversation_id}` (`SP-OP-ESCALATION-001.md:221,376`) | SP-OP-ESCALATION-001 (FINAL (v1.1.0) · **Emenda:** DL-0118/ADR-0064, 17/10/2026) | 1 |
| `msg.fraude.evidencia_anexada` | SP-OP-FRAUDE-001 | `Msg_FraudeEvidenciaAnexada` | boundaryEvent `BME_EvidenciaAnexada` | business key (`SP-OP-FRAUDE-001.md:146`) | SP-OP-FRAUDE-001 (DRAFT (v0.1.0)) | 1 |
| `msg.fraude.diligencia_concluida` | SP-OP-FRAUDE-001 | `Msg_FraudeDiligenciaConcluida` | intermediateCatchEvent `ICE_DiligenciaConcluida` | business key (`SP-OP-FRAUDE-001.md:147`) | SP-OP-FRAUDE-001 (DRAFT (v0.1.0)) | 1 |
| `msg.inadimplencia.pagamento_recebido` | SP-OP-INADIMPLENCIA-001 | `Msg_InadPagamentoRecebido` | intermediateCatchEvent `ICE_PagamentoAntesNotificacao`; intermediateCatchEvent `ICE_PagamentoRecebido` | business key (`SP-OP-INADIMPLENCIA-001.md:195`) | SP-OP-INADIMPLENCIA-001 (DRAFT (v0.2.0)) | 1 |
| `msg.inadimplencia.notificacao_ack` | SP-OP-INADIMPLENCIA-001 | `Msg_InadNotificacaoAck` | intermediateCatchEvent `ICE_NotificacaoAck50`; intermediateCatchEvent `ICE_NotificacaoAck` | business key (`SP-OP-INADIMPLENCIA-001.md:196`) | SP-OP-INADIMPLENCIA-001 (DRAFT (v0.2.0)) | 1 |
| `msg.inadimplencia.info_received` | SP-OP-INADIMPLENCIA-001 | `Msg_InadInfoRecebida` | boundaryEvent `BME_InfoRecebida`; intermediateCatchEvent `ICE_AguardarInfo` | business key (`SP-OP-INADIMPLENCIA-001.md:197`) | SP-OP-INADIMPLENCIA-001 (DRAFT (v0.2.0)) | 1 |
| `msg.lgpd.proof_received` | SP-OP-LGPD-DSR-001 | `Msg_LgpdProvaRecebida` | intermediateCatchEvent `ICE_ProvaRecebida` | nao declarada no contrato (`SP-OP-LGPD-DSR-001.md:83`) — GAP | SP-OP-LGPD-DSR-001 (DRAFT (v0.1.0)) | 1 |
| `msg.nip.instruct` | SP-OP-NIP-001 | `Msg_NipInstruct` | startEvent `Start_NipInstruct` | start por delegacao A2A `nip.instruct`; correlaciona `NIP-{tenant}-{numero_nip_ans}` (`SP-OP-NIP-001.md:150`) | SP-OP-NIP-001 (DRAFT (v0.1.0)) | 1 |
| `msg.nip.info_recebida` | SP-OP-NIP-001 | `Msg_NipInfoRecebida` | intermediateCatchEvent `ICE_InfoRecebida` | nao declarada no contrato (`SP-OP-NIP-001.md:151`) — GAP | SP-OP-NIP-001 (DRAFT (v0.1.0)) | 1 |
| `msg.pagto.dados_corrigidos` | SP-OP-PAGTO-001 | `Msg_PagtoDadosCorrigidos` | intermediateCatchEvent `ICE_AguardarDadosCorrigidos` | business key (`SP-OP-PAGTO-001.md:246`) | SP-OP-PAGTO-001 (DRAFT (v0.1.0)) | 1 |
| `msg.programa.consent_revoked` | SP-OP-PROGRAMA-001 | `Msg_ProgramaConsentRevoked` | boundaryEvent `BME_RevogacaoConsentimento` | business key (`SP-OP-PROGRAMA-001.md:159`) | SP-OP-PROGRAMA-001 (DRAFT (v0.1.0)) | 1 |
| `msg.programa.info_received` | SP-OP-PROGRAMA-001 | `Msg_ProgramaInfoReceived` | boundaryEvent `BME_InfoRecebida`; intermediateCatchEvent `ICE_AguardarInfo` | business key (`SP-OP-PROGRAMA-001.md:160`) | SP-OP-PROGRAMA-001 (DRAFT (v0.1.0)) | 1 |
| `msg.recurso.docs_received` | SP-OP-RECURSO-001 | `Msg_RecursoDocsRecebidos` | intermediateCatchEvent `ICE_DocsRecebidos` | business key (`SP-OP-RECURSO-001.md:152`) | SP-OP-RECURSO-001 (DRAFT (v0.1.0)) | 1 |
| `msg.reembolso.docs_received` | SP-OP-REEMBOLSO-001 | `Msg_ReembolsoDocsRecebidos` | intermediateCatchEvent `ICE_DocsRecebidos` | business key (`SP-OP-REEMBOLSO-001.md:129`) | SP-OP-REEMBOLSO-001 (DRAFT (v0.2.0)) | 1 |

## Erros (`<bpmn:error errorCode>`)

Correlação não se aplica a erro BPMN: o código é lançado por um worker (`handleBpmnError`) ou por um
evento de fim e capturado no próprio processo. A coluna "Uso no BPMN" diz onde; "declarado, sem evento que
o referencie" é um código que o diagrama declara e nenhum evento captura nem lança (achado de higiene,
registrado e não corrigido aqui: nenhum `.bpmn` é editado por este épico).

| errorCode | Processo | Id BPMN | Uso no BPMN | Dono (contrato, status) | Versão |
|---|---|---|---|---|---|
| `ERR_ADEQUACAO_CELULA_INVALIDA` | SP-OP-ADEQUACAO-001 | `Error_AdequacaoCelulaInvalida` | declarado, sem evento que o referencie | SP-OP-ADEQUACAO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_FALLBACK_COMMITMENT_NOT_HUMAN` | SP-OP-ADEQUACAO-001 | `Error_FallbackCommitmentNotHuman` | declarado, sem evento que o referencie | SP-OP-ADEQUACAO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_ANS_SUBMIT_NOT_HUMAN` | SP-OP-ANS-SUBMIT-001 | `Error_AnsSubmitNotHuman` | declarado, sem evento que o referencie | SP-OP-ANS-SUBMIT-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_ANS_DATASET_INCOMPLETO` | SP-OP-ANS-SUBMIT-001 | `Error_AnsDatasetIncompleto` | boundary `BE_AssembleDatasetIncompleto` em `ST_AssembleDataset` (topic `regulatorio.anssubmit.assemble`) | SP-OP-ANS-SUBMIT-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_ANS_PROTOCOLO_NACK` | SP-OP-ANS-SUBMIT-001 | `Error_AnsProtocoloNack` | boundary `BE_SubmitNack` em `ST_SubmeterEnvio` (topic `regulatorio.anssubmit.submit`) | SP-OP-ANS-SUBMIT-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_ANS_RETRY_ESGOTADO` | SP-OP-ANS-SUBMIT-001 | `Error_AnsRetryEsgotado` | endEvent `End_RetryEsgotado`; boundary `BE_RetryEsgotado` em `SUB_RetryEnvio` | SP-OP-ANS-SUBMIT-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_AUTH_INVALID_GUIA` | SP-OP-AUTH-001 | `Error_AuthGuiaInvalida` | declarado, sem evento que o referencie | SP-OP-AUTH-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_AUTH_DENIAL_INCOMPLETE` | SP-OP-AUTH-001 | `Error_AuthDenialIncompleta` | boundary `BE_NegativaIncompleta` em `ST_EnviarNegativaFormal` (topic `operadora.auth.send_denial_notice`) | SP-OP-AUTH-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_AUTH_DECISION_INVALID` | SP-OP-AUTH-001 | `Error_AuthDecisaoInvalida` | endEvent `End_ErrDecisaoInvalida` | SP-OP-AUTH-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_CANCEL_INVALID_CONTRATO` | SP-OP-CANCEL-001 | `Error_CancelContratoInvalido` | declarado, sem evento que o referencie | SP-OP-CANCEL-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_CANCELLATION_NOT_HUMAN` | SP-OP-CANCEL-001 | `Error_CancellationNotHuman` | declarado, sem evento que o referencie | SP-OP-CANCEL-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_CANCEL_MANTER_NOT_HUMAN` | SP-OP-CANCEL-001 | `Error_CancelManterNotHuman` | boundary `BE_ManterNaoConfirmado` em `ST_ConfirmMaintainedDecision` (topic `operadora.cancel.confirm_maintained_decision`) | SP-OP-CANCEL-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_EVENT_PUBLISH_FAILED` | SP-OP-CANCEL-001 | `Error_CancelAvisoFalhou` | boundary `BE_AvisoCanceladoFalhou` em `ST_AvisarCancelado` (topic `operadora.events.publish`); boundary `BE_AvisoInfoFalhou` em `ST_AvisarInfoSolicitada` (topic `operadora.events.publish`); boundary `BE_AvisoMantidoFalhou` em `ST_AvisarPedidoMantido` (topic `operadora.events.publish`) | SP-OP-CANCEL-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_CONTAS_LOTE_INVALIDO` | SP-OP-CONTAS-001 | `Error_ContasLoteInvalido` | declarado, sem evento que o referencie | SP-OP-CONTAS-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_CONTAS_GLOSA_NOT_HUMAN` | SP-OP-CONTAS-001 | `Error_ContasGlosaNotHuman` | declarado, sem evento que o referencie | SP-OP-CONTAS-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_CONTAS_DECISAO_INVALIDA` | SP-OP-CONTAS-001 | `Error_ContasDecisaoInvalida` | endEvent `End_ErrContasDecisaoInvalida` | SP-OP-CONTAS-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_CRED_INVALID_PRESTADOR` | SP-OP-CRED-001 | `Error_CredPrestadorInvalido` | boundary `BE_PrestadorInvalido` em `ST_VerifyCredentials` (topic `operadora.cred.verify_credentials`) | SP-OP-CRED-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_DECRED_NOT_HUMAN` | SP-OP-CRED-001 | `Error_DecredNotHuman` | boundary `BE_DecredNaoHumano` em `ST_RegisterDescredenciamento` (topic `operadora.cred.register_descredenciamento`) | SP-OP-CRED-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_CRED_DENIAL_NOT_HUMAN` | SP-OP-CRED-001 | `Error_CredDenialNotHuman` | boundary `BE_CredDenialNaoHumano` em `ST_RegisterCredDenial` (topic `operadora.cred.register_cred_denial`) | SP-OP-CRED-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_ESC_NOTIFY_FAILED` | SP-OP-ESCALATION-001 | `Error_EscNotifyFailed` | boundary `BE_FalhaNotificacao` em `ST_NotificarTime` (topic `operadora.escalation.notify_team`); boundary `BE_NotifFallbackFailed` em `ST_NotificarFallback` (topic `operadora.escalation.notify_supervisor`); boundary `BE_NotifSupervisorFailed` em `ST_NotificarSupervisor` (topic `operadora.escalation.notify_supervisor`) | SP-OP-ESCALATION-001 (FINAL (v1.1.0) · **Emenda:** DL-0118/ADR-0064, 17/10/2026) | 1 |
| `ERR_EVENT_PUBLISH_FAILED` | SP-OP-ESCALATION-001 | `Error_EscPublishFailed` | boundary `BE_PubReqFailed` em `ST_PublishRequested` (topic `operadora.events.publish`); boundary `BE_PubAckBreachFailed` em `ST_PublishAckBreach` (topic `operadora.events.publish`); boundary `BE_PubResolBreachFailed` em `ST_PublishResolBreach` (topic `operadora.events.publish`); boundary `BE_PubResolvedFailed` em `ST_PublishResolved` (topic `operadora.events.publish`); boundary `BE_PubProcessCompletedFailed` em `ST_PublishProcessCompleted` (topic `operadora.events.publish`); boundary `BE_PubWithdrawnFailed` em `ST_PublishWithdrawn` (topic `operadora.events.publish`) | SP-OP-ESCALATION-001 (FINAL (v1.1.0) · **Emenda:** DL-0118/ADR-0064, 17/10/2026) | 1 |
| `ERR_FRAUD_ACCUSATION_NOT_HUMAN` | SP-OP-FRAUDE-001 | `Error_FraudAccusationNotHuman` | declarado, sem evento que o referencie | SP-OP-FRAUDE-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_CUSTODY_NOT_SEALED` | SP-OP-FRAUDE-001 | `Error_CustodyNotSealed` | endEvent `End_CustodiaNaoSelada` | SP-OP-FRAUDE-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_PHI_IN_CUSTODY` | SP-OP-FRAUDE-001 | `Error_PhiInCustody` | declarado, sem evento que o referencie | SP-OP-FRAUDE-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_FRAUDE_CASO_INVALIDO` | SP-OP-FRAUDE-001 | `Error_FraudeCasoInvalido` | declarado, sem evento que o referencie | SP-OP-FRAUDE-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_INAD_INVALID_CONTRATO` | SP-OP-INADIMPLENCIA-001 | `Error_InadContratoInvalido` | declarado, sem evento que o referencie | SP-OP-INADIMPLENCIA-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` | SP-OP-INADIMPLENCIA-001 | `Error_ContractSuspensionNotHuman` | boundary `BE_SuspensaoNaoHumano` em `ST_RegisterSuspension` (topic `operadora.inadimplencia.register_contract_suspension`) | SP-OP-INADIMPLENCIA-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_DSR_IDENTITY_UNVERIFIED` | SP-OP-LGPD-DSR-001 | `Error_LgpdIdentidade` | boundary `BE_IdentidadeInverificavel` em `ST_VerificarIdentidade` (topic `operadora.lgpd.verify_identity`) | SP-OP-LGPD-DSR-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_DSR_ERASURE_FAILED` | SP-OP-LGPD-DSR-001 | `Error_LgpdErasureFalhou` | declarado, sem evento que o referencie | SP-OP-LGPD-DSR-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_DSR_ERASURE_NOT_HUMAN` | SP-OP-LGPD-DSR-001 | `Error_LgpdErasureNaoHumana` | declarado, sem evento que o referencie | SP-OP-LGPD-DSR-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_DSR_DECISION_INVALID` | SP-OP-LGPD-DSR-001 | `Error_LgpdDecisaoInvalida` | endEvent `End_ErrDecisaoInvalida` | SP-OP-LGPD-DSR-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_DSR_FUNDAMENTACAO_AUSENTE` | SP-OP-LGPD-DSR-001 | `Error_LgpdFundamentacaoAusente` | endEvent `End_ErrFundamentacaoAusente` | SP-OP-LGPD-DSR-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_NIP_PROTOCOLO_INVALIDO` | SP-OP-NIP-001 | `Error_NipProtocoloInvalido` | boundary `BE_NipProtocoloInvalidoManter` em `ST_HandoffAnsManter` (topic `operadora.nip.handoff_ans_submit`); boundary `BE_NipProtocoloInvalidoConceder` em `ST_HandoffAnsConceder` (topic `operadora.nip.handoff_ans_submit`); boundary `BE_NipProtocoloInvalidoNaoAssist` em `ST_HandoffAnsNaoAssistencial` (topic `operadora.nip.handoff_ans_submit`) | SP-OP-NIP-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_NIP_NEGATIVA_NOT_HUMAN` | SP-OP-NIP-001 | `Error_NipNegativaNotHuman` | declarado, sem evento que o referencie | SP-OP-NIP-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_PAYMENT_RELEASE_NOT_HUMAN` | SP-OP-PAGTO-001 | `Error_PaymentReleaseNotHuman` | declarado, sem evento que o referencie | SP-OP-PAGTO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_PAGTO_ORDEM_INVALIDA` | SP-OP-PAGTO-001 | `Error_PagtoOrdemInvalida` | boundary `BE_PagtoOrdemInvalida` em `ST_ValidatePaymentData` (topic `operadora.pagto.validate_payment_data`) | SP-OP-PAGTO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_PROGRAMA_NO_CONSENT` | SP-OP-PROGRAMA-001 | `Error_ProgramaNoConsent` | boundary `BE_SemConsentimento` em `ST_CheckConsent` (topic `operadora.programa.check_consent`) | SP-OP-PROGRAMA-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_PROGRAM_DISCHARGE_NOT_HUMAN` | SP-OP-PROGRAMA-001 | `Error_ProgramDischargeNotHuman` | declarado, sem evento que o referencie | SP-OP-PROGRAMA-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_PROGRAMA_INSTANCIA_INVALIDA` | SP-OP-PROGRAMA-001 | `Error_ProgramaInstanciaInvalida` | declarado, sem evento que o referencie | SP-OP-PROGRAMA-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_RECURSO_INVALID_GLOSA` | SP-OP-RECURSO-001 | `Error_RecursoGlosaInvalida` | boundary `BE_GlosaInvalidaValidacao` em `ST_ValidarRecurso` (topic `operadora.recurso.validate_recurso`); boundary `BE_GlosaInvalidaDocs` em `ST_SolicitarDocumentos` (topic `operadora.recurso.request_documents`); boundary `BE_GlosaInvalidaDossie` em `ST_PrepararDossie` (topic `operadora.recurso.analyze_request`) | SP-OP-RECURSO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN` | SP-OP-RECURSO-001 | `Error_RecursoIndeferimentoNotHuman` | declarado, sem evento que o referencie | SP-OP-RECURSO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_RECURSO_DECISAO_INVALIDA` | SP-OP-RECURSO-001 | `Error_RecursoDecisaoInvalida` | endEvent `End_ErrRecursoDecisaoInvalida` | SP-OP-RECURSO-001 (DRAFT (v0.1.0)) | 1 |
| `ERR_REEMBOLSO_INVALID_PROTOCOLO` | SP-OP-REEMBOLSO-001 | `Error_ReembolsoProtocoloInvalido` | boundary `BE_ReembolsoProtocoloInvalido` em `ST_ValidarSolicitacao` (topic `operadora.reembolso.check_coverage`) | SP-OP-REEMBOLSO-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` | SP-OP-REEMBOLSO-001 | `Error_ReembolsoDenialNotHuman` | declarado, sem evento que o referencie | SP-OP-REEMBOLSO-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_EVENT_PUBLISH_FAILED` | SP-OP-REEMBOLSO-001 | `Error_ReembolsoAvisoFalhou` | boundary `BE_AvisoAprovadoFalhou` em `ST_AvisarAprovado` (topic `operadora.events.publish`); boundary `BE_AvisoParcialFalhou` em `ST_AvisarParcial` (topic `operadora.events.publish`); boundary `BE_AvisoPagoFalhou` em `ST_AvisarPago` (topic `operadora.events.publish`); boundary `BE_AvisoNegadoFalhou` em `ST_AvisarNegado` (topic `operadora.events.publish`); boundary `BE_AvisoInfoFalhou` em `ST_AvisarInfo` (topic `operadora.events.publish`) | SP-OP-REEMBOLSO-001 (DRAFT (v0.2.0)) | 1 |
| `ERR_SUPP_SUBJECT_UNRESOLVED` | SP-OP-SUPP-001 | `Error_SuppSujeitoIrresolvivel` | boundary `BE_SujeitoIrresolvivel` em `ST_VerificarSujeito` (topic `vendor.suppression.verify_subject`) | SP-OP-SUPP-001 (DRAFT (v1.0.0)) | 1 |
| `ERR_SUPP_ROUTING_UNAVAILABLE` | SP-OP-SUPP-001 | `Error_SuppRoteamentoIndisponivel` | boundary `BE_RoteamentoIndisponivel` em `ST_AvaliarRoteamento` (topic `vendor.suppression.route`) | SP-OP-SUPP-001 (DRAFT (v1.0.0)) | 1 |

## Sinais e escalações

Nenhum `<bpmn:signal>` e nenhum `<bpmn:escalation>` existe hoje em `spec/processes/bpmn/`. Regra: **zero
sinais**, salvo decisão registrada nesta seção (com a referência da decisão). Sinal é broadcast sem
correlação; mensagem com correlação é o padrão.

| Sinal/escalação | Processo | Decisão registrada |
|---|---|---|

## O que este catálogo não faz

- Não cria mensagem nem erro: só inventaria o que os `.bpmn` declaram. As mensagens de NOTIF-001
  (E-SF-13r, aguardando ratificação) entram aqui quando o `.bpmn` existir.
- Não promove contrato nenhum (todos seguem com o status do cabeçalho).
- Não fixa produtor de cada mensagem: onde o contrato nomeia (ex.: o Lucas em `msg.reembolso.docs_received`),
  isso está no contrato; onde não nomeia, é GAP do contrato, não deste catálogo.

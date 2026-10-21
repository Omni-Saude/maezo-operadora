# Runbook — revogação de consentimento pendente no lago da AMH (DL-0083)

**Quando chega aqui.** Um caso do SP-OP-ESCALATION-001 (categoria `solicitacao_humano`) com o resumo fixo
"Revogacao de consentimento recebida por WhatsApp e ainda NAO registrada no lago da AMH ... (motivo
revogacao_consentimento_pendente)". O beneficiário escreveu REVOGAR, o Maezo **já** aplicou a revogação (os
agentes não rodam e nenhum dado dele é usado), mas a gravação `revoked` no `consent-record` da AMH falhou
nas 5 tentativas automáticas (`regravacoes_falhas = 5`, log de alarme
`acesso_consentimento_regravacao_esgotada`). Enquanto isso, o beneficiário recebe o texto fixo "Recebemos seu
pedido de revogação e ele está sendo processado...".

**O que NÃO fazer.** Não apague a linha de `conversa_acesso_beneficiario` e não mude `revogado_em`: a
revogação tem de entrar no lago com o instante ORIGINAL do pedido (a AMH #212 aceita `decided_at` no
passado, e a chave de idempotência é derivada dele — repetir é seguro).

## Passos

1. **Confirme que o `consent-record` da AMH voltou** (painel/health do interop da AMH, ou o fim dos logs
   `acesso_consentimento_nao_gravado` com `decisao=revoked`). Se ainda está fora, acione a AMH e pare aqui:
   o beneficiário segue protegido (nenhum agente roda).
2. **Libere a regravação automática** no schema do tenant, com o `conversation_id` do caso (é o keyed
   `wa:{tenant}:hk1_...` da chave de negócio; nunca um telefone):

   ```sql
   UPDATE conversa_acesso_beneficiario
      SET regravacoes_falhas = 0
    WHERE tenant = '<tenant>' AND conversation_id = '<conversation_id>'
      AND revogacao_pendente;
   ```

3. **Peça ao beneficiário que mande qualquer mensagem** (ou aguarde a próxima). Nesse turno o Maezo regrava
   `revoked` com o `revogado_em` original. Se der certo, `revogacao_pendente` vira `false`, a referência é
   descartada e a conversa volta ao normal (um novo ACEITO recomeça o fluxo). Se falhar de novo, o contador
   volta a subir e, no teto, um novo alarme e um novo caso aparecem.
4. **Confira e feche o caso**: `SELECT revogacao_pendente, regravacoes_falhas FROM conversa_acesso_beneficiario
   WHERE tenant = ... AND conversation_id = ...` deve mostrar `false, 0`.

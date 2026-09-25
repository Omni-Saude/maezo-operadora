# ADR-0061: Custódia do telefone do beneficiário para retomada

**Status:** Proposto
**Data:** 2026-09-25
**Área:** Segurança | Dados | LGPD

> **Condição de habilitação.** Este ADR não autoriza ligar a custódia em NENHUM ambiente. O DPO
> precisa registrar ciência (e a base legal e o prazo de retenção abaixo) antes de qualquer
> ambiente receber `RECIPIENT_VAULT_KMS_KEY_ARN` / `recipient_vault_enabled = true`. Até lá o
> código existe, os testes passam, e a infraestrutura continua com `terraform plan` sem mudança.

## Contexto

GAP-XHITL-4: quando o humano devolve um caso de SP-OP-ESCALATION-001 (`devolvido_agente`), a
Helena precisa retomar a conversa no WhatsApp com a instrução do humano. A Cloud API do WhatsApp
exige o **número** do destinatário. A v2 guarda só o `phone_hash` keyed (ADR-0035), irreversível
por desenho, e não existia cofre reversível de telefone (ADR-0006; `dispatch.py`, "PHI custody
note"). Sem o número, a retomada não pode ser entregue.

Alternativas consideradas: (a) guardar o número em claro no checkpoint da conversa — rejeitada,
quebra o invariante de que o estado da Helena nunca carrega telefone; (b) consultar o cadastro
do beneficiário — hoje não há ponte de identidade entre a conversa e o cadastro; (c)
identificador de usuário da Meta — dependente de produto externo, não disponível. Decisão do dono
(25/09/2026): **cofre cifrado na Zona PHI**.

## Decisão

1. **Tabela** `beneficiario_contato_retomada` (migração `0016_recipient_custody.py`), no schema do
   tenant, uma linha por `(tenant, conversation_id)`, com **upsert** a cada mensagem recebida.
2. **Envelope encryption.** O número é cifrado com AES-256-GCM sob uma chave de dados aleatória
   por linha; a chave de dados é embrulhada pelo **AWS KMS** (`kms:Encrypt`). O AAD do GCM e o
   encryption context do KMS são `(tenant, conversation_id, finalidade)` — um blob copiado para
   outra conversa não abre, e o CloudTrail registra para qual conversa cada decifragem ocorreu.
   Código: `src/maezo/gateway/recipient_custody.py`.
3. **Separação de papéis por IAM.** O receptor do webhook tem **só `kms:Encrypt`**; o serviço
   `agent-resume` tem **só `kms:Decrypt`** (roles dedicadas em
   `deploy/aws-ecs/envs/dev-sa-east-1/recipient-vault.tf`, condicionadas ao encryption context).
   Ninguém mais decifra.
4. **Retenção.** `expires_at` = última mensagem + **30 dias** (proposta; configurável por
   `RECIPIENT_VAULT_TTL_DAYS`). A leitura ignora linha vencida; `purge_expired` apaga.
5. **Janela da Meta.** A mesma linha guarda `last_inbound_at`. A retomada só envia se a última
   mensagem do beneficiário tem **menos de 24h**; fora disso nada é enviado, o desfecho
   `retomada_fora_da_janela` é contado e a equipe é avisada por `escalation.notify_team`.
6. **Nunca em claro em log.** Nenhum log, `repr` ou mensagem de erro da custódia carrega o
   número (teste `tests/unit/gateway/test_recipient_custody.py`).

## Consequências

**Positivas:** a retomada pós-humano passa a ser entregável; o dado fica cifrado em repouso com
chave gerenciada, com trilha por conversa no KMS e prazo de vida curto.

**Negativas (aceitas):** passa a existir um dado de contato **reversível** do titular, novo
tratamento sob a LGPD — exige base legal, registro no plano de eliminação (camada
`custodia_contato_retomada`, `PENDENTE`) e ciência do DPO; o direito de eliminação precisa de
uma ponte da referência DSR até `conversation_id`, que ainda não existe (`PONTE_AUSENTE`).

## Supersedes

— (complementa ADR-0006 e ADR-0035; não os altera).

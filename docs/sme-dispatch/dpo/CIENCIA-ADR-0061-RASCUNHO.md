# Registro de ciência do DPO — ADR-0061 (custódia do telefone para a retomada)

> **rascunho — pendente de designação e assinatura do encarregado (LGPD art. 41)**
>
> **recomendação — pendente de assinatura de DPO**
>
> Este texto foi preparado pela engenharia para o DPO **confirmar ou alterar**. Ninguém além do DPO
> preenche os campos de assinatura. Enquanto estiverem em branco, **não há ciência registrada** e o
> ADR-0061 continua `Proposto` (`docs/adr/0061-custodia-do-telefone-do-beneficiario-para-retomada.md`).
> Decisão correspondente na planilha `DPO_Decisoes_Escalonamento_2026-10-20.xlsx`: linhas (a), (b),
> (c) e (h).

---

## 1. Escopo da ciência

| Item | Texto proposto | Confirmar / alterar |
|---|---|---|
| Ambiente | **Somente `dev-sa-east-1`.** Não vale para homologação nem produção; cada um exige registro próprio. | |
| Titulares | **Somente** colaboradores e diretoria que aceitaram participar do teste interno (celulares do time). Nenhum beneficiário real. | |
| Dado | Número de telefone do WhatsApp de quem escreveu para a Helena. Dado pessoal comum (não sensível, art. 5º II), guardado na Zona PHI porque fica associado a uma conversa de saúde. | |
| Tratamento | Guardar o número cifrado e usá-lo **uma vez por devolução** para entregar ao titular a resposta do atendente humano (`devolvido_agente` de SP-OP-ESCALATION-001). | |
| Início do tratamento em dev | 27/09/2026 (exceção da diretoria registrada no ADR-0061; `deploy/aws-ecs/envs/dev-sa-east-1/retomada.auto.tfvars`). Esta ciência é **posterior** ao início e o registra. | |

## 2. Finalidade

Única: **retomada pós-humano** — devolver ao beneficiário, no mesmo canal, a orientação que o
atendente humano escreveu ao concluir o escalonamento. A finalidade está gravada no próprio
mecanismo: o contexto de cifragem do KMS e o AAD do AES-GCM carregam
`finalidade = maezo-retomada-pos-humano` (`src/maezo/gateway/recipient_custody.py::FINALIDADE`), e as
permissões de IAM só autorizam cifrar/decifrar com esse contexto
(`deploy/aws-ecs/envs/dev-sa-east-1/recipient-vault.tf`).

**Fora da finalidade (proibido sem novo registro):** qualquer outro uso do número, inclusive o
lembrete proativo de cobrança. Observação da engenharia: o código do lembrete
(`src/maezo/platform/lembretes/composicao.py::montar`) monta o mesmo cofre e lê o telefone com o
mesmo contexto de finalidade; em dev ele está **desligado** (`var.lembrete_cobranca` nula). Ligá-lo
exige registro próprio (planilha, linha h).

## 3. Base legal

| Situação | Base proposta | Por quê |
|---|---|---|
| Dev — teste interno com celulares do time | **Legítimo interesse** (LGPD art. 7º IX), com ciência informada dos participantes | Teste do próprio serviço, titulares internos, expectativa legítima; dado mínimo, cifrado e de vida curta |
| Produção — fora do escopo desta ciência | Sugestão para o ato futuro: **execução de contrato / procedimentos a pedido do titular** (art. 7º V) | O beneficiário iniciou o atendimento; o número só serve para devolver a resposta que ele pediu |

CONFIRMAR COM O JURÍDICO.

## 4. Prazo de retenção

- **30 dias** contados da última mensagem recebida do titular (`expires_at`, configurável por
  `RECIPIENT_VAULT_TTL_DAYS`; padrão `src/maezo/gateway/recipient_custody.py::DEFAULT_TTL_DAYS`).
- Cada nova mensagem do titular renova o prazo da linha da conversa.
- Ponto de atenção registrado: a retomada só envia se a última mensagem do titular tem **menos de
  24 h** (janela da Meta, `src/maezo/gateway/recipient_custody.py::JANELA_META`). Os 30 dias são,
  portanto, mais longos que o uso efetivo. Aceitos para o teste em dev; para produção a engenharia
  recomenda reavaliar para **7 dias** com base no tempo real que o atendente leva para devolver.

## 5. Condições desta ciência

1. **Limpeza diária ligada.** Até 20/10/2026 nada apagava as linhas vencidas
   (`src/maezo/gateway/recipient_custody.py::PostgresRecipientVault.purge_expired` sem chamador); a
   leitura só as ignorava. A ciência vale com o job `python -m maezo.platform.custodia_limpeza`
   (`deploy/aws-ecs/envs/dev-sa-east-1/task-custodia-limpeza.tf`) ligado e agendado uma vez por dia.
   O job não tem chave de KMS: só apaga o que venceu.
2. **Eliminação a pedido do titular.** Não existe ponte da referência de DSR até a conversa
   (`PONTE_AUSENTE`, `spec/policies/retention/erasure-plan.template.yaml`, camada
   `custodia_contato_retomada`). Em dev, aceita-se atender por remoção manual da linha ou pelo
   rollback (voltar `recipient_vault_enabled = false`; a chave entra em exclusão de 30 dias e o
   cifrado fica ilegível). **Antes de produção** é condição construir o procedimento por telefone:
   o titular informa o número, o sistema recalcula o mesmo identificador de conversa e apaga a
   linha, com registro na cadeia de auditoria.
3. **Nunca em claro.** Nenhum log, mensagem de erro ou `repr` carrega o número (já fixado em
   `tests/unit/gateway/test_recipient_custody.py`).
4. **Separação de papéis mantida.** O receptor só cifra; a retomada só decifra; a limpeza não faz
   nenhum dos dois.
5. **Revisão.** Esta ciência expira na primeira das datas: designação formal do encarregado para
   produção (planilha, linha g) ou 90 dias a partir da assinatura — o que vier primeiro, quando
   deve ser renovada ou encerrada.

## 6. O que esta ciência NÃO faz

- Não muda o status do ADR-0061 (continua `Proposto`) nem autoriza outro ambiente.
- Não ratifica a matriz de retenção (`docs/sme-dispatch/dpo/RETENTION-MATRIX-CANDIDATE.md`, §9).
- Não substitui o RIPD (`docs/compliance/ripd-kickoff.md`, bloqueado pela designação do
  encarregado).
- Não liga nada: ligar a limpeza é um PR de `tfvars` + apply, depois desta assinatura.

---

**Ciência registrada por (DPO):** ______________________  **data:** ______________________

**Alterações feitas pelo DPO neste texto (se houver):** ______________________

# docs/compliance/human-windows — Pacotes de evidência para janelas humanas (W5 5.6)

**Sessão:** 2026-10-24 · **Missão:** W5 CLNT item 5.6 de `docs/plans/maezo-completion-program.md` — "pacotes de evidência para cada pendência humana (médico-auditor clínico; jurídico/DPO/operação do lucas)", conforme §HDR do plano. Molde: `docs/compliance/legal/` (pacotes W1, PR #935).
**Regra de ouro desta árvore:** os pacotes INFORMAM; NÃO DECIDEM. Nenhum bloco de decisão/ratificação/aprovação é preenchido aqui (ADR-0007; cerca §0(4) do plano). Todo fato citado traz `arquivo:linha` medido em `main` = `dc544a0e` (#965), checkout limpo.

## Conteúdo

| Arquivo | Papel |
|---|---|
| `MEDICO-AUDITOR-PACKAGE.md` | Pendências do médico-auditor (§HDR do plano): (A) ratificação das 6 fontes do portão auth-criteria (GAP-AUTH-4) — nada ratificado hoje; (B) as 4 DMNs de triage red-flag DRAFT; (C) o desvio C2 `leitura_phi_clinica` em sombra até o muro 2027-02-09 (janela de evidências fecha 2027-01-26). |
| `LUCAS-DPO-OPERACAO-PACKAGE.md` | Pendências do DPO / operação-financeiro do Lucas: D-25 (contras-assinaturas do plano de eliminação — 41 relações PENDENTE + insumos GP11/M2 + VW4), D-26 (pontes de identidade do erasure — substrato #965 em dark), revisões de habilitação do Lucas (2 DMNs, fonte de cobrança, DPA, flag do roteador) e a cadeia R1–R5 que bloqueia OP17. |

## Consumo esperado

1. O humano nomeado no §HDR do plano abre o pacote do seu papel, lê as pendências numeradas e responde no formato de decisão assinável de cada pacote (§"Formato de resposta").
2. Toda resposta vira mudança de DADOS em PR próprio, CODEOWNERS-gated, assinada por quem decide — nunca por agente.
3. Os prazos de verdade externa do programa (muro 2027-02-09; fim da janela C2 em 2027-01-26) valem para as duas árvores e estão explicados em `MEDICO-AUDITOR-PACKAGE.md` §3.

## Limites declarados

- Esta árvore não edita `src/**`, `spec/**`, PLANS.md, COORDINATION.md, BPMN/DMN, nem nenhum artefato de autor.
- Pacotes jurídicos D-03/D-04/REG-1.13 NÃO estão aqui: já existem em `docs/compliance/legal/` (PR #935).
- O bundle de auditoria `docs/audits/JOURNEYS-COMPLETENESS-2026-10/` é gitignored e editado concorrentemente; onde o pacote cita o bundle, o trecho relevante está transcrito verbatim no corpo do pacote.

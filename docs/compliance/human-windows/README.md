# docs/compliance/human-windows — Pacotes de evidência para janelas humanas (W5 5.6)

**Sessão:** 2026-10-24 · **Missão:** W5 CLNT item 5.6 de `docs/plans/maezo-completion-program.md` — "pacotes de evidência para cada pendência humana (médico-auditor clínico; jurídico/DPO/operação do lucas)", conforme §HDR do plano. Molde: `docs/compliance/legal/` (pacotes W1, PR #935).
**Regra de ouro desta árvore:** os pacotes INFORMAM; NÃO DECIDEM. Nenhum bloco de decisão/ratificação/aprovação é preenchido aqui (ADR-0007; cerca §0(4) do plano). Todo fato citado traz `arquivo:linha` medido em `main` = `dc544a0e` (#965), checkout limpo.

## Conteúdo

| Arquivo | Papel |
|---|---|
| `MEDICO-AUDITOR-PACKAGE.md` | Pendências do médico-auditor (§HDR do plano): (A) ratificação das 6 fontes do portão auth-criteria (GAP-AUTH-4) — nada ratificado hoje; (B) as 4 DMNs de triage red-flag DRAFT; (C) o desvio C2 `leitura_phi_clinica` em sombra até o muro 2027-02-09 (janela de evidências fecha 2027-01-26). |
| `LUCAS-DPO-OPERACAO-PACKAGE.md` | Pendências do DPO / operação-financeiro do Lucas: D-25 (contras-assinaturas do plano de eliminação — 43 relações PENDENTE (rótulo histórico 41; ver D-25 41→43 no plano) + insumos GP11/M2 + VW4), D-26 (pontes de identidade do erasure — substrato #965 em dark), revisões de habilitação do Lucas (2 DMNs, fonte de cobrança, DPA, flag do roteador) e a cadeia R1–R5 que bloqueia OP17. |
| `ATO-INDICACAO-ENCARREGADO-MINUTA.md` | Minuta do ato formal de indicação do encarregado (Res. CD/ANPD 18/2024, arts. 3º–4º, 8º–9º) + checklist até a janela DPO de 2026-11-10 (DEC-07/PEND-075, issue #1082). Campos humanos em branco. |
| `ATO-INDICACAO-ENCARREGADO-FILIPE-CARMO.md` | Ato formal de indicação do encarregado/DPO derivado da minuta, com o titular **Filipe Carmo** nomeado pelo dono em 2026-10-31 (DL-0158). Também traz o modelo de declaração de conflito de interesse (arts. 18–21) e o checklist de publicação e onboarding. Controlador, substituto (obrigatório pelo art. 4º), data, assinaturas e URL de divulgação ficam em branco para humanos. |
| `R-070-dec-teto-d07.md` | Minuta de decisão do teto de auto-aprovação do REEMBOLSO (R-070 / DEC-26, issue #1084): estado medido, moldura regulatória, proposta rígida do orquestrador a pedido do dono (não vinculante), opções A/B/C e bloco de decisão em branco para dono + atuária. |
| `R-040-dec-bedrock.md` | Registro da decisão do dono DEC-20 (issue #1084, 2026-10-31): opção (a), mover a chamada de embeddings Bedrock (F-051) para `runtime.inference` (AGENTS.md regra 6). Descreve o que foi construído no PR do pacote. Bloco de ratificação em branco para o dono. |
| `R-033-dec-glosa-m4.md` | Registro da decisão do dono DEC-14 (issue #1084, 2026-10-31): categoria de glosa desconhecida na `glosa_triage` (M-4 / OQ-10) vai a humano, opção (a). Traz a fonte verbatim, o estado medido antes, o que a opção significa no modelo (implementado no R-062) e o bloco de ratificação em branco. |

## Consumo esperado

1. O humano nomeado no §HDR do plano abre o pacote do seu papel, lê as pendências numeradas e responde no formato de decisão assinável de cada pacote (§"Formato de resposta").
2. Toda resposta vira mudança de DADOS em PR próprio, CODEOWNERS-gated, assinada por quem decide — nunca por agente.
3. Os prazos de verdade externa do programa (muro 2027-02-09; fim da janela C2 em 2027-01-26) valem para as duas árvores e estão explicados em `MEDICO-AUDITOR-PACKAGE.md` §3.

## Limites declarados

- Esta árvore não edita `src/**`, `spec/**`, PLANS.md, COORDINATION.md, BPMN/DMN, nem nenhum artefato de autor.
- Pacotes jurídicos D-03/D-04/REG-1.13 NÃO estão aqui: já existem em `docs/compliance/legal/` (PR #935).
- O bundle de auditoria do programa (JOURNEYS-COMPLETENESS-2026-10, sob `docs/audits/...`, gitignored — elisão declarada) é editado concorrentemente; onde um pacote cita o bundle, o trecho relevante está transcrito verbatim no corpo do pacote, e a citação de fonte viva usa caminho rastreado (`docs/Tarefas_Pendentes.md`, `docs/review-queue.md`, `docs/plans/maezo-completion-program.md`).

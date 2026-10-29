# Minuta — Ato formal de indicação do encarregado (DPO) + checklist da janela de 2026-11-10

**Natureza:** MINUTA INFORMATIVA (regra de ouro desta árvore: informa, não decide — ADR-0007). Nenhum campo de nome, data ou assinatura é preenchido por agente. **Origem:** decisão pendente DEC-07 / PEND-075 (issue #1082), auditoria forense @f57f0821; pedido do dono em 2026-10-29 para "detalhar e executar o que for possível".

**Fonte normativa:** Resolução CD/ANPD nº 18, de 16/07/2024 (DOU 17/07/2024; https://www.in.gov.br/en/web/dou/-/resolucao-cd/anpd-n-18-de-16-de-julho-de-2024-572632074; texto conferido em cópia de lgpd.ms.gov.br, sha256 do PDF `3f360e02ab7bcc92…`, acesso 2026-10-29). Trechos verbatim abaixo.

- Art. 3º: "A indicação do encarregado deve ser realizada por ato formal do agente de tratamento, do qual constem as formas de atuação e as atividades a serem desempenhadas." § 1º: "documento escrito, datado e assinado, que, de maneira clara e inequívoca, demonstre a intenção do agente de tratamento em designar como encarregado uma pessoa natural ou uma pessoa jurídica." § 2º: apresentado à ANPD quando solicitado.
- Art. 4º: "Nas ausências, impedimentos e vacâncias do encarregado, a função será exercida por substituto formalmente designado."
- Art. 6º: "A indicação de encarregado por operadores é facultativa".
- Arts. 8º e 9º: divulgar e manter atualizadas identidade e contato, "em local de destaque e de fácil acesso, no sítio eletrônico do agente de tratamento" (mínimo: nome completo; meios de contato que viabilizem o exercício dos direitos dos titulares e o recebimento de comunicações da ANPD).
- Art. 12: pessoa natural (do quadro ou externa) ou pessoa jurídica. Arts. 18–21: autonomia técnica e vedação a conflito de interesse; o encarregado declara qualquer situação de conflito (art. 20).

## 0. Questão prévia (COUNSEL/DPO — não decidida aqui)
Quem é o **controlador** dos dados dos beneficiários tratados pela plataforma? Pela leitura usual, a **operadora** (tenant, ex.: AMH) é controladora e a plataforma Maezo atua como **operadora no sentido da LGPD** (art. 6º da Res. 18: indicação facultativa para operadores). Consequências: (a) o ato abaixo é assinado pelo representante legal do **controlador**; (b) se a Omni/Maezo também for controladora de algum dado próprio (ex.: identidades do portal, `portal_*`), ela precisa de ato próprio. CODEOWNERS de `spec/policies/retention/` já lista um revisor DPO (`.github/CODEOWNERS:138,142`), mas isso **não** substitui o ato formal.

## 1. Minuta do ato

> **ATO DE INDICAÇÃO DO ENCARREGADO PELO TRATAMENTO DE DADOS PESSOAIS**
>
> [RAZÃO SOCIAL DO CONTROLADOR], inscrita no CNPJ sob nº [__], neste ato representada por [NOME / CARGO DO REPRESENTANTE LEGAL], nos termos do art. 41 da Lei nº 13.709/2018 e dos arts. 3º e 4º da Resolução CD/ANPD nº 18/2024, **indica** como encarregado pelo tratamento de dados pessoais:
>
> **Titular:** [NOME COMPLETO, se pessoa natural / NOME EMPRESARIAL + pessoa natural responsável, se pessoa jurídica] — [interno/externo].
> **Substituto (art. 4º):** [NOME COMPLETO].
>
> **Formas de atuação e atividades (art. 3º, caput; art. 15):** receber reclamações e comunicações dos titulares, prestar esclarecimentos e adotar providências; receber comunicações da ANPD e adotar providências; orientar funcionários e contratados sobre práticas de proteção de dados; e, no âmbito da plataforma Maezo: (i) contra-assinar as decisões por relação do plano de eliminação (`spec/policies/retention/erasure-plan.template.yaml`); (ii) revisar pontes de identidade do erasure (D-26) e insumos GP11/M2/VW4; (iii) decidir aceitação de risco e prazos de DSR (LGPD art. 18/19) registrados nas issues da auditoria.
>
> **Autonomia e conflito de interesse (arts. 18–21):** o encarregado atua com autonomia técnica e declarou [não possuir / possuir, com as medidas: __] situação de conflito de interesse.
>
> **Divulgação (arts. 8º–9º):** identidade e contato publicados em [URL do sítio do controlador] e mantidos atualizados; canal: [e-mail/formulário].
>
> [Local], [DATA]. — [ASSINATURA DO REPRESENTANTE LEGAL] — Ciência: [ASSINATURA DO ENCARREGADO] / [ASSINATURA DO SUBSTITUTO]

## 2. Checklist até a janela de 2026-11-10 (sessão "Lucas DPO — só contrassinaturas", pacote `W8-OWNER-SESSION-DPO-PACKAGE-v1`)
1. [ ] Questão prévia §0 respondida por COUNSEL/DPO (controlador vs operador).
2. [ ] Ato §1 assinado pelo representante legal do controlador, com substituto (art. 4º).
3. [ ] Identidade e contato publicados no sítio do controlador (art. 9º); link registrado em `docs/evidence-ledger.md`.
4. [ ] Contagem viva das relações a contra-assinar re-conferida no dia: hoje **43** (`src/maezo/platform/lifecycle/erasure_plan.py`, `spec/policies/retention/erasure-plan.template.yaml`, pins `tests/unit/platform/test_erasure_plan.py:831,833`; base `ae8c9f52`). **Atenção:** o PR #1074 (REEMBOLSO v2) adiciona a migração `0031_reembolso_coleta` e edita o plano de eliminação — se entrar antes da sessão, a contagem passa a 44.
5. [ ] Sessão: o DPO decide por relação o triplo `decisao_dpo` / `base_legal` / `retencao` (vocabulário em `erasure-plan.template.yaml`); agentes não preenchem campos humanos.
6. [ ] Pós-sessão: PR de DADOS com o que foi assinado, testes anti-drift verdes, linha da `docs/review-queue.md:27` atualizada (checklist §4 do pacote de sessão).
7. [ ] Emenda de rótulo D-25 "41 → 43" (ou 44) registrada pelo dono no plano (D-25 já registrada como "41→43" em D-53..D-56).

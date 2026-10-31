# Ato formal de indicação do encarregado (DPO): Filipe Carmo

**Natureza:** documento para assinatura humana, derivado da minuta `ATO-INDICACAO-ENCARREGADO-MINUTA.md` (mesma pasta). O nome do titular foi preenchido porque o dono o informou. Os demais campos humanos ficam em branco. Nenhum agente assina, data ou preenche bloco de decisão ou ratificação (ADR-0007).

**Origem:** decisão do dono do repositório em sessão de 2026-10-31: **Filipe Carmo (GitHub `@filipecarmo81`, CODEOWNER) é o encarregado/DPO.** Registro: DL-0158 em `docs/decisions-log.md`. Pendência de origem: DEC-07 / PEND-075 (issue #1082).

**Fonte normativa:** Resolução CD/ANPD nº 18, de 16/07/2024 (DOU 17/07/2024). A minuta transcreve literalmente os arts. 3º e 4º e trechos dos arts. 6º e 9º; os arts. 8º, 12, 15 e 18 a 21 aparecem em paráfrase, conferida contra o texto do DOU de 17/07/2024. Quem assina deve conferir esses artigos no texto oficial antes de assinar.

---

## 0. Questão prévia: quem é o controlador (NÃO decidida aqui)

Este ato **não decide** quem é o controlador. A pergunta (§0 da minuta) continua com o jurídico (COUNSEL), que precisa confirmá-la antes da assinatura.

- **Hipótese de trabalho da minuta (não vinculante):** a **operadora de saúde (tenant, ex.: AMH)** é a **controladora** dos dados dos beneficiários e assina este ato. A **Omni/Maezo** atua como **operadora no sentido da LGPD**. Para quem é operador, a indicação de encarregado é facultativa (art. 6º).
- **Consequência, se a hipótese se confirmar:** o representante legal da operadora de saúde (tenant) assina o bloco abaixo.
- **Se a Omni/Maezo também for controladora de dados próprios** (por exemplo, as identidades do portal, `portal_*`): ela precisa de um **ato próprio**, nos mesmos moldes, assinado pelo representante legal dela. Este documento não cobre esse caso.

---

## 1. Ato

> **ATO DE INDICAÇÃO DO ENCARREGADO PELO TRATAMENTO DE DADOS PESSOAIS**
>
> [RAZÃO SOCIAL DO CONTROLADOR — a preencher por humano], inscrita no CNPJ sob nº [__ — a preencher por humano], neste ato representada por [NOME DO REPRESENTANTE LEGAL — a preencher por humano], [CARGO — a preencher por humano], nos termos do art. 41 da Lei nº 13.709/2018 e dos arts. 3º e 4º da Resolução CD/ANPD nº 18/2024, **indica** como encarregado pelo tratamento de dados pessoais:
>
> **Titular:** **Filipe Carmo**, pessoa natural, [interno / externo — a preencher por humano].
> *(Nome completo para a divulgação do art. 9º: o DL-0086 registra "Márcio Filipe Filó do Carmo". O titular confirma a grafia antes de assinar. Este documento usa o nome informado pelo dono.)*
>
> **Substituto (art. 4º) — OBRIGATÓRIO:** [NOME COMPLETO — a preencher por humano]
> *(A resolução exige que haja um substituto formalmente designado para ausências, impedimentos e vacâncias. Sem esse nome, o ato fica incompleto.)*
>
> **Formas de atuação e atividades (art. 3º, caput; art. 15, em paráfrase):**
> 1. receber reclamações e comunicações dos titulares, prestar esclarecimentos e adotar providências;
> 2. receber comunicações da ANPD e adotar providências;
> 3. orientar funcionários e contratados sobre as práticas de proteção de dados pessoais;
> 4. executar as demais atribuições que o controlador determinar ou que normas complementares estabelecerem.
>
> **Atribuições no âmbito da plataforma Maezo** (itens 5, 7 e 9 vêm da minuta; itens 6 e 8 são acréscimos deste ato, a confirmar pelo controlador):
> 5. decidir e contra-assinar, para cada relação, o triplo `decisao_dpo` / `base_legal` / `retencao` do plano de eliminação (`spec/policies/retention/erasure-plan.template.yaml`) e o fecho `ratificado` / `dpo_review` / `dpo_reviewer` / `dpo_review_date` do mesmo arquivo. Esses campos ainda não estão preenchidos (`ratificado: false`, linha 97; `dpo_review`/`dpo_reviewer`/`dpo_review_date` com placeholders `PENDENTE*`, linhas 98, 101 e 102);
> 6. definir os valores de retenção (matriz de retenção; insumos em `docs/sme-dispatch/dpo/RETENTION-MATRIX-CANDIDATE.md`);
> 7. revisar as pontes de identidade do erasure (D-26) e os insumos GP11/M2/VW4;
> 8. prestar assistência e orientação na elaboração do RIPD/DPIA da plataforma (art. 16, III);
> 9. responder aos itens DPO da issue #1082 e opinar sobre aceitação de risco e prazos de DSR (LGPD arts. 18 e 19) registrados nas issues da auditoria.
>
> **Autonomia e conflito de interesse (arts. 10, I e III, e 18 a 21, em paráfrase):** o encarregado atua com ética, integridade e autonomia técnica (art. 18). O controlador lhe provê os meios necessários (art. 10, I) e garante a autonomia técnica necessária, livre de interferências indevidas (art. 10, III). O encarregado declarou o seguinte sobre conflito de interesse:
> [não possuir / possuir, com as medidas: __ — a preencher pelo encarregado; ver a declaração do Anexo A]
>
> **Divulgação (arts. 8º e 9º):** identidade e contato publicados em [URL DO SÍTIO DO CONTROLADOR — a preencher por humano] e mantidos atualizados. Canal de contato: [E-MAIL / FORMULÁRIO — a preencher por humano].
>
> [LOCAL — a preencher por humano], [DATA — a preencher por humano].
>
> ______________________________________ [ASSINATURA DO REPRESENTANTE LEGAL]
>
> Ciência: ______________________________________ [ASSINATURA DO ENCARREGADO — Filipe Carmo]
>
> Ciência: ______________________________________ [ASSINATURA DO SUBSTITUTO]

---

## Anexo A — Declaração de conflito de interesse (arts. 18 a 21; modelo para o encarregado preencher e assinar)

**Por que este anexo é necessário (fatos medidos, não juízo):**

- Filipe Carmo também é revisor de código (CODEOWNER) de `/deploy/`, como Diretor de Tecnologia e dono da organização (`.github/CODEOWNERS:324-327`).
- Antes deste ato, ele já autorizou decisões de tratamento de dados registradas como "Dono (CTO/DPO)" ou "dono e DPO". Exemplos: DL-0078, DL-0085, DL-0086, DL-0116, DL-0117, DL-0117 (adendo 3), DL-0119, DL-0120, DL-0126, DL-0127, DL-0130 e DL-0155 em `docs/decisions-log.md`.
- Em paráfrase da Res. 18 (texto conferido no DOU de 17/07/2024): o encarregado deve atuar com ética, integridade e autonomia técnica, evitando conflito de interesse (art. 18); pode acumular funções desde que atenda plenamente às atribuições e inexista conflito (art. 19, caput); o conflito pode se configurar com o acúmulo da atividade de encarregado com outras que envolvam a tomada de decisões estratégicas sobre o tratamento de dados pessoais pelo controlador (art. 19, § 1º, II); o encarregado deve declarar ao agente de tratamento qualquer situação que possa configurar conflito, respondendo pela veracidade da declaração (art. 20); e, constatada a possibilidade de conflito, o agente de tratamento deve não indicar a pessoa, implementar medidas para afastar o risco ou substituí-la (art. 21, parágrafo único, I a III).

**Regra operacional proposta para a Maezo (a confirmar na declaração; o agente não decide):** Filipe Carmo **não aprova como DPO** matéria que ele mesmo decidiu ou implementou como engenheiro, CTO ou dono. Nesses casos, quem aprova é o substituto do art. 4º. O tratamento das autorizações anteriores ao ato (as DLs listadas acima) é decisão do encarregado com o jurídico: ciência, ratificação pelo substituto ou revisão. Este documento não as valida nem as invalida.

> **DECLARAÇÃO DE CONFLITO DE INTERESSE DO ENCARREGADO**
>
> Eu, [NOME COMPLETO — a preencher pelo encarregado], indicado como encarregado pelo tratamento de dados pessoais de [RAZÃO SOCIAL DO CONTROLADOR], declaro, nos termos dos arts. 18 a 21 da Resolução CD/ANPD nº 18/2024:
>
> 1. Funções que exerço além da de encarregado, junto ao controlador ou a outros agentes de tratamento:
>    [a preencher pelo encarregado]
> 2. Situações que possam configurar conflito de interesse:
>    [ ] não identifico nenhuma situação.
>    [ ] identifico as seguintes: [a preencher pelo encarregado]
> 3. Medidas adotadas para cada situação (por exemplo: abstenção e encaminhamento ao substituto; segregação de aprovação nos caminhos de código e de dados):
>    [a preencher pelo encarregado]
> 4. Tratamento das decisões que tomei antes deste ato na dupla condição de dono/CTO e DPO:
>    [a preencher pelo encarregado, com o jurídico]
> 5. Comprometo-me a atualizar esta declaração sempre que surgir situação nova.
>
> Declaro que as informações acima são verdadeiras.
>
> [LOCAL], [DATA]. ______________________________________ [ASSINATURA DO ENCARREGADO]

---

## 2. Checklist de publicação e onboarding (até a janela de 2026-11-10)

O agente não marca nenhum item como feito. Cada item é marcado pelo humano que o executa, em PR de dados próprio.

1. [ ] **Questão prévia (§0):** o jurídico confirma quem é o controlador e quem é o operador. Se a Omni/Maezo também for controladora, preparar um ato próprio.
2. [ ] **Substituto (art. 4º):** escolher e nomear. O campo é obrigatório no ato.
3. [ ] **Assinaturas:** o representante legal do controlador assina o ato (§1); o encarregado e o substituto dão ciência; o encarregado preenche e assina o Anexo A.
4. [ ] **Publicação (arts. 8º e 9º):** publicar identidade (nome completo) e contato em local de destaque no sítio do controlador. Registrar o link em `docs/evidence-ledger.md`. O link vem de quem publicou; o agente não inventa URL.
5. [ ] **GitHub (revisão DPO nos caminhos de privacidade e retenção):** hoje as linhas `/spec/policies/privacy/` (`.github/CODEOWNERS:120`), `/spec/policies/retention/` (`:138`) e `/spec/policies/retention/erasure-plan.template.yaml` (`:142`) listam `@lucasreisEvah` como revisor DPO. Essa atribuição foi registrada como "PENDENTE DE CONFIRMACAO DO DONO" (`:108-109`). `.github/CODEOWNERS` é caminho protegido, e o agente não o edita. O diff proposto ao dono está no corpo do PR que introduz este documento. Manter ou não `@lucasreisEvah` é decisão do dono.
6. [ ] **Engine (grupo `dpo`):** incluir o encarregado e o substituto no grupo `dpo` do IdP/console de User Tasks. O grupo é candidato de `UT_RevisaoDpo` via `spec/processes/dmn/lgpd_dsr_routing.dmn:58` e aparece também em `suppression_routing.dmn`. O nome real do grupo está em branco em `docs/sme-dispatch/po/org-taxonomy-table.yaml:331-342` (`nome_real: ''`) e é preenchido pelo dono organizacional da operadora.
7. [ ] **Primeira sessão: 2026-11-10.** O encarregado decide, relação por relação, o triplo `decisao_dpo` / `base_legal` / `retencao`. Hoje são **43 relações** (pins em `tests/unit/platform/test_erasure_plan.py:831,833`). Se o PR #1074 (REEMBOLSO v2, migração `0031_reembolso_coleta`) entrar antes da sessão, passam a ser **44**. Re-conferir a contagem no dia.
8. [ ] **Pós-sessão:** abrir PR de DADOS com o que foi assinado, incluindo `dpo_reviewer`, que hoje está `PENDENTE-IDENTIDADE-DO-DPO` (`spec/policies/retention/erasure-plan.template.yaml:101`). Os testes anti-drift precisam ficar verdes.

# R-041 — Candidate groups (DEC-21): registro das escolhas do dono e janela de assinatura

**Natureza:** REGISTRO DE DECISÃO PARA ASSINATURA. Segue a regra de ouro desta árvore: informa, não decide (ADR-0007). As escolhas abaixo foram feitas pelo dono na issue #1084 (comentário 6545662766, 2026-10-31); este arquivo só as transcreve e aponta onde cada uma virou artefato. O bloco do §5 fica em branco: nenhum agente preenche nome, data ou assinatura.

**Origem:**
- Item R-041 do roadmap da auditoria forense (decisão humana; refs F-040, PEND-414/442/457/461/478/485/496/502/513/522/533).
- DEC-21 na issue #1084: rodada 1 ("apresentar papel a papel"), rodada 2 ("Grupos") e rodada 3 (Q1–Q6).
- Análise de suporte: mapeamento grupo × cargo do tenant (38 grupos), fora do repo. O resultado vive em `docs/sme-dispatch/po/org-taxonomy-table.yaml` (`grupos_registrados`) e em `ORG-TAXONOMY-TABLE.md`.

## 1. Escolhas do dono (transcritas)

| # | Escolha | Onde está no repo |
|---|---|---|
| G1 | Remover os co-grupos das 5 tarefas adversas de decisor único | CRED `UT_AnaliseDescredenciamento` → `juridico-rede`; CANCEL `UT_AnaliseRescisao` → `juridico-contratos`; INADIMPLENCIA `UT_AnaliseInadimplencia` → `juridico-contratos`; PROGRAMA `UT_DecisaoClinica` → `coordenacao-clinica`. A 5ª (NIP `UT_RevisaoJuridicaNip`) fica para o pacote NIP (§3) |
| G2 | Dividir `coordenacao-financeira` em admissibilidade × aprovação | PAGTO `UT_AnaliseAdmissibilidade` → `analise-contas-pagar` (novo); `UT_CoordenacaoAlcada` segue `coordenacao-financeira` |
| G3 | Criar o grupo `ouvidoria` (RN 623, art. 16) | Grupo criado no bootstrap do engine e na taxonomia. O processo SP-OP-OUVIDORIA-001 é pacote separado |
| G4 | Criar TODOS os grupos no bootstrap do engine e atualizar a tabela de taxonomia | `src/maezo/platform/engine_bootstrap/bootstrap.py` (`GRUPOS_CANONICOS`, 38 grupos); `org-taxonomy-table.yaml` (`grupos_registrados`, mesma ordem) |
| Q1 | Na escalação das 5 tarefas adversas, o coordenador só reatribui, prorroga ou decide favorável; o desfecho adverso fica com o grupo decisor (guarda) | Registrado nos contratos CRED/CANCEL/INADIMPLENCIA. A guarda ainda NÃO existe (§3) |
| Q2 | NIP: 1ª instância Coordenador de Regulação; escalação `gerencia-regulatoria` = Gerente Jurídico e de Compliance; cossinatura médica quando clínico | `gerencia-regulatoria` criado no bootstrap; mudança de BPMN no pacote NIP (§3) |
| Q3 | Manter a DMN (teto clerical R$ 100k) e emendar a descrição de cargo do Coordenador de Tesouraria | `pagto_alcada.dmn` intocada; emenda de cargo = ato do dono |
| Q4 | Criar o cargo de Ouvidor (reporta à Diretoria) + SP-OP-OUVIDORIA-001 + cossinatura médica para manter negativa | Cargo mapeado na taxonomia; processo em pacote próprio |
| Q5 | Criar descrições completas dos cargos de staff | Ato do dono/RH; a taxonomia marca "cargo de staff a descrever (Q5)" |
| Q6 | Plantão clínico P1 = telemedicina externa (só) | `plantao-clinico` mapeado assim na taxonomia |
| — | Escalação da auditoria médica = cargo de Médico Auditor (CRM), não o Coordenador de Auditoria | `coordenacao-auditoria-medica` mapeado assim na taxonomia |

Além das escolhas literais, a entrega aplica duas consequências do modelo de 38 grupos: `nucleo-ans` fundido em `regulatorio-ans` (`nip_routing`/`nip_classification`), e `coordenacao-rede` removido como co-candidato de ADEQUACAO `UT_DecisaoFallback` (o contrato já dizia "compromisso SO aqui" para `gestao-rede`).

## 2. Estado medido

- Antes: o bootstrap criava 4 grupos (as filas de ESCALATION). Os demais candidate groups não existiam no engine.
- Depois: o bootstrap cria os 38 grupos sem membership e sem grant. Quem entra em cada grupo é o IdP/dono, com o cargo da taxonomia.
- Prova: `tests/unit/platform/test_engine_bootstrap_canonical_groups.py` (todo grupo para onde uma User Task pode ir existe no bootstrap; decisor único nas 4 tarefas; checker ≠ aprovador; registro da taxonomia = bootstrap).

## 3. Fora desta entrega (cada um é pacote próprio)

1. **Pacote NIP:** `UT_RevisaoJuridicaNip` → `coordenacao-regulatorio` + nova `UT_CossignaturaMedicaNip` (`medico-auditor`) + `UT_CoordenacaoNip` → `gerencia-regulatoria`. Vai junto porque tirar `medico-auditor` sem a cossinatura deixaria um não-médico manter sozinho uma negativa clínica (L0 `authorization_denial`). Uma User Task nova também exige binding de portal e guarda de worker.
2. **Pacote SP-OP-OUVIDORIA-001** (G3/Q4).
3. **Guarda Q1** nas 5 escalações e guardas por instância P1–P11 + lint estrutural S1–S11 no IdP/bootstrap.
4. PAGTO `UT_AprovacaoComite` multi-instância (controle duplo) e ESCALATION `grupo_supervisao` clínico.

## 4. O que a assinatura confirma

A nomeação do dono confirma o cargo de cada grupo em `grupos_registrados` e libera o preenchimento de `nome_real` na taxonomia. Não confirma nenhum dos pacotes do §3.

## 5. Bloco de assinatura (em branco — só o dono preenche)

- Opção escolhida: ______
- Papel: owner (dono organizacional da operadora)
- Nome: ______
- Data: ______
- Assinatura: ______
- Gate cego aplicável: ______

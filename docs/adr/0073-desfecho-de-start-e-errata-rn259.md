# ADR-0073: Desfecho de start na trilha de auditoria (tentativa × efeito) — e errata da citação RN 259 do ADR-0005

**Status:** Proposed — redigido por AGENTE por ordem do dono (2026-10-31, pacote W0-F: F-148 opção (b) e errata RN 259);
**ratificação humana PENDENTE** (CODEOWNERS de `/docs/adr/`). Nenhum código de runtime, BPMN, DMN ou worker muda por
esta ADR. Nenhuma célula de aprovação é preenchida aqui (ADR-0007).
**Data:** 2026-10-31
**Área:** Auditoria (ADR-0007) | Orquestração (chokepoint de start) | Compliance (ADR-0005)
**Emenda (não substitui):** ADR-0007 — só a leitura da linha de auditoria de start (Parte A). ADR-0005 — só a citação
normativa do §Contexto (Parte B, errata). Nenhuma decisão do ADR-0005 ou do ADR-0007 muda.
**Apoia-se em:** ADR-0045 (fail-notify canônico, desfecho `erro_inicio_processo`), ADR-0041 (precedente: emenda por ADR
nova, sem tocar o arquivo do ADR Accepted), achado F-148 (SEAM-F7) da auditoria forense @f57f0821, folha R-054 linha L9
(`docs/compliance/legal/R-054-RATIFICACAO-CITACOES-POR-LINHA.md`).

---

## Parte A — Desfecho de start (F-148)

### Contexto

**O achado.** F-148 (🟢, P2, prova por sonda): com o motor fora do ar no momento do start, fica na trilha durável uma
linha de auditoria de um start que não aconteceu. O comportamento é deliberado: o chokepoint único de start,
`src/maezo/tools/mcp_cibseven/transport.py::start_process_idempotent`, grava a linha de proveniência ANTES do efeito
(passo 1 do docstring, "audit BEFORE effect") e só depois faz o POST ao motor (passo 4). Se o POST falhar, a linha fica.
O esperado, pelo ADR-0007, é que a auditoria distinga a **tentativa** do **efeito concluído**. Hoje ela não distingue.

**Como está hoje (medido em `main` `baa29683`).**
- A linha de start é montada por `src/maezo/tools/mcp_cibseven/transport.py::build_start_audit_record`, com
  `action = "start_process:<process_key>"` e `decision = "START_PROCESS"` (constante
  `src/maezo/tools/mcp_cibseven/transport.py::_START_DECISION`). O comentário acima dessa constante já diz que a linha
  atesta "o agente se comprometeu a iniciar esta instância", e não que a instância nasceu. Mas nada na trilha registra o
  que aconteceu depois.
- O chokepoint já calcula o desfecho, e o devolve ao chamador como valor tipado:
  `src/maezo/tools/mcp_cibseven/transport.py::StartOutcome` (`STARTED`, `ALREADY_ACTIVE`, `ALREADY_COMPLETED`, e
  `UNREPORTED` para instância montada fora do chokepoint). Esse valor vive só no retorno e em log. Não vai para a trilha.
- Na falha, o chokepoint repassa o `src/maezo/tools/mcp_cibseven/transport.py::CibSevenError` sem mudança. O agente
  roteia para `src/maezo/runtime/start_outcome.py::notify_start_failure` e grava o desfecho de agente
  `src/maezo/runtime/start_outcome.py::DESFECHO_ERRO_INICIO_PROCESSO` (ADR-0045). Isso é estado do agente e telemetria.
  A trilha durável continua só com a linha de tentativa.
- O transporte HTTP (`src/maezo/tools/mcp_cibseven/transport.py::CibSevenHttpTransport.start_process_instance`)
  converte tanto `httpx.HTTPStatusError` quanto `httpx.RequestError` no mesmo `CibSevenError`. Assim, "conexão recusada"
  (o pedido nem saiu) e "tempo de leitura esgotado" (o pedido saiu, e o motor pode ter criado a instância) ficam
  indistinguíveis para quem audita.
- Nas famílias com trava (`EXCLUSIVE`/`PERMANENT`), a linha de tentativa também é o claim de exclusão mútua. O claim
  órfão já é anunciado em log (`cibseven_start_claim_orphaned`) e, na próxima entrega, vira
  `src/maezo/tools/mcp_cibseven/transport.py::StartClaimWithoutInstanceError` (indecidível e ruidoso, por desenho).

**Alternativas consideradas.**

| Opção | O que faz | Por que não |
|---|---|---|
| (1) Apagar ou reverter a linha de tentativa quando o POST falha | trilha "limpa" | A trilha é append-only (ADR-0007). E a linha é o claim de exclusão: liberá-la depois de um timeout que pode ter tido efeito é o pagamento em dobro que o B-3 fechou. |
| (2) Gravar a auditoria só depois do efeito | uma linha só, sempre verdadeira | Quebra o fail-closed: abre caminho para efeito sem auditoria (o que o passo 1 existe para impedir). |
| (3) Atualizar o status da linha de tentativa | uma linha com estado final | Não existe UPDATE numa cadeia append-only com hash encadeado. |
| **(4) Duas fases, ambas append-only (escolhida)** | mantém a linha de tentativa e acrescenta uma linha de desfecho, ligada a ela | Custo de uma linha a mais por start; precisa de reconciliação para o caso em que a linha de desfecho não chega. |

### Decisão proposta

**A.1 — A linha de tentativa continua como está, e passa a ter leitura declarada.** Uma linha
`start_process:<process_key>` / `START_PROCESS` atesta a **tentativa** (o compromisso de iniciar, e, nas famílias com
trava, o direito exclusivo de tentar). Ela **nunca** prova o efeito. Quem audita, a partir desta ADR, lê assim. O literal
`START_PROCESS` não muda (compatibilidade de leitura com a cadeia já gravada).

**A.2 — O desfecho terminal vira uma segunda linha, append-only, ligada à tentativa.** Depois de cada saída do chokepoint posterior à linha de tentativa (passos 2, 3 e 4), o chokepoint grava uma linha de desfecho na mesma trilha:

- `action = "start_process_outcome:<process_key>"`;
- `decision` = um valor do vocabulário fechado de A.3;
- `details` = `process_key`, `attempt_record_hash` (o `record_hash` da linha de tentativa, devolvido por
  `src/maezo/gateway/audit.py::EmitOnceOutcome`), `instance_id` quando houver instância (é o id do motor, sem PHI),
  `error_class` (só o NOME da classe tipada do erro; nunca o texto da exceção, que já provou carregar identificador —
  CC-06), e `fonte` (`chokepoint` ou `reconciliacao`);
- os mesmos campos de identidade do ADR-0007 da linha de tentativa (`agent_id`, `agent_version`, `tenant`,
  `model_id`, `prompt_version`, `dmn_versions`).

Gravação com `emit_once_status` e chave de dedup
`<start_dedup_key>:outcome:<attempt_record_hash>:<decision>`: uma reentrega não duplica o mesmo desfecho, e uma
sequência real (primeira entrega falha, segunda inicia) fica registrada como duas linhas.

**A.3 — Vocabulário fechado do desfecho de start.**

| `decision` da linha de desfecho | Quando | Espelho no código | Desfecho de agente (ADR-0045) |
|---|---|---|---|
| `STARTED` | o motor devolveu a instância criada por esta chamada | `StartOutcome.STARTED` | sucesso do agente |
| `ALREADY_ACTIVE` | já havia instância viva para a chave; nada foi iniciado | `StartOutcome.ALREADY_ACTIVE` | sucesso do agente |
| `ALREADY_COMPLETED` | trava estrita: claim + histórico do motor provam que a chave já rodou; nada foi iniciado | `StartOutcome.ALREADY_COMPLETED` | sucesso do agente (recusa honesta) |
| `FAILED_BEFORE_EFFECT` | erro **provadamente anterior ao efeito**: falha de conexão antes do envio do pedido, ou recusa do motor com resposta 4xx | `CibSevenError` tipado (A.4) | `erro_inicio_processo` |
| `EFFECT_UNKNOWN` | o pedido pode ter chegado e o efeito é desconhecido: timeout de leitura/escrita, conexão derrubada após o envio, resposta 5xx | `CibSevenError` tipado (A.4) | `erro_inicio_processo` |
| `NOT_STARTED` | **só na reconciliação** (A.5): o histórico do motor prova que não há instância para a tentativa | — | — |

`StartOutcome.UNREPORTED` não gera linha de desfecho: ele só existe fora do chokepoint, onde não há tentativa gravada.

Regra de leitura: o **desfecho terminal** de uma tentativa é a última linha de desfecho dela, na ordem da cadeia.
`STARTED`, `ALREADY_ACTIVE` e `ALREADY_COMPLETED` encerram a tentativa. `EFFECT_UNKNOWN` nunca é terminal para quem
audita: ele pede reconciliação.

**A.4 — A classificação vem de tipo, nunca de texto.** O transporte HTTP passa a levantar duas subclasses de
`CibSevenError`, que mantêm intacto todo `except CibSevenError` dos agentes:
- uma para "não chegou ao motor" (`httpx.ConnectError`, `httpx.ConnectTimeout`) e para resposta 4xx do motor →
  `FAILED_BEFORE_EFFECT`;
- uma para "pode ter chegado" (`httpx.ReadTimeout`, `httpx.WriteTimeout`, `httpx.RemoteProtocolError`, resposta 5xx e
  qualquer `httpx.RequestError` não listado acima) → `EFFECT_UNKNOWN`.

Na dúvida, a classificação é `EFFECT_UNKNOWN`. Classificar como `FAILED_BEFORE_EFFECT` um timeout que teve efeito
seria uma afirmação falsa na trilha. Os nomes das subclasses ficam para o PR de implementação.

A classificação usa também a POSIÇÃO no chokepoint, não só o tipo: (i) qualquer `CibSevenError` levantado nos passos 2 ou 3 (`find_active_instance`/`find_any_instance`, antes do POST) → `FAILED_BEFORE_EFFECT`; (ii) `CibSevenTenantError` levantado no passo 4 → `FAILED_BEFORE_EFFECT`; (iii) `StartClaimWithoutInstanceError` → `EFFECT_UNKNOWN` (indecidível por desenho; segue para decisão humana, nunca libera claim); (iv) qualquer exceção que não seja `CibSevenError` depois do POST ter devolvido 2xx → `EFFECT_UNKNOWN`; (v) resposta 408 ou 499 → `EFFECT_UNKNOWN` (um intermediário pode tê-la emitido depois de repassar o pedido). Para sinks que não implementam `emit_once_status`, `attempt_record_hash` é o hash devolvido por `emit_once`, e a linha de desfecho é gravada com `emit_once`.

**A.5 — Reconciliação.** Uma tentativa sem desfecho terminal (processo morreu entre as duas linhas, ou a linha de
desfecho falhou) ou com desfecho `EFFECT_UNKNOWN` é resolvida pelo histórico do motor
(`src/maezo/tools/mcp_cibseven/transport.py::HistoryQueryingTransport`), que é a segunda trilha do ADR-0007. A
reconciliação grava `STARTED` (com o `instance_id` achado) ou `NOT_STARTED`, sempre com `fonte = reconciliacao`. Ela
nunca libera claim de família com trava. O claim órfão segue no caminho que já existe
(`StartClaimWithoutInstanceError`, decisão humana). Nesse caminho, a reconciliação só registra o que o motor mostra.

**A.6 — A linha de desfecho nunca muda o resultado do start.** Se a gravação da linha de desfecho falhar:
- depois de um start bem-sucedido, o chokepoint devolve a instância normalmente e emite log + métrica de falha de
  auditoria de desfecho. Transformar um start real em exceção faria o agente gravar `erro_inicio_processo` para um
  processo que existe, o espelho do fato fabricado que o CC-01 fechou;
- depois de uma falha de start, o `CibSevenError` original é relançado sem mudança.

A tentativa sem desfecho fica para a reconciliação (A.5). A linha de tentativa continua obrigatória e fail-closed
(`AuditPersistenceError` impede o start, como hoje).

**A.7 — Fora do escopo desta parte.** As recusas que acontecem ANTES da linha de tentativa (tenant do motor, scrub de
PHI, autorização D7, seams da trava ausentes) não gravam tentativa hoje e continuam sem linha de desfecho, porque não há
ambiguidade a desfazer. O caminho de intake humano (`start_human`) tem trilha própria e não é tocado. O texto que o agente
mostra ao beneficiário na falha é território do ADR-0045 e não muda aqui.

**A.8 — Entrega no escuro.** A implementação entra atrás de flag default-off (nome proposto:
`MAEZO_START_OUTCOME_AUDIT`), passada ao chokepoint como parâmetro keyword default `False`, no mesmo molde do
`promote_start_dedup` (R-101). Com a flag desligada, o comportamento é byte a byte o de hoje.

### Consequências

**Positivas**
- F-148 deixa de ser ambíguo. A linha de tentativa continua (o fail-closed não muda), e passa a ter par: o desfecho
  `FAILED_BEFORE_EFFECT` ou `EFFECT_UNKNOWN` diz a quem audita que o start não aconteceu, ou que ainda não se sabe.
- A trilha durável e o desfecho de agente do ADR-0045 passam a contar a mesma história, com mapeamento declarado (A.3).
- O `StartOutcome` que hoje só existe em retorno e log ganha registro durável, append-only e não-repudiável.

**Negativas (aceitas)**
- Uma linha a mais por start, no volume já particionado pelo ADR-0007.
- Uma rotina de reconciliação a construir e operar.
- `EFFECT_UNKNOWN` é desconfortável de propósito: o sistema registra que não sabe, em vez de adivinhar.

### Critério de aceite testável (DoD da implementação)

1. Com a flag desligada, a sequência de linhas gravadas por `start_process_idempotent` é idêntica à de hoje (teste de
   paridade sobre o sink de teste).
2. Com a flag ligada e um transporte que levanta erro de conexão antes do envio: uma linha de tentativa + uma linha
   `FAILED_BEFORE_EFFECT` com `attempt_record_hash` igual ao hash da tentativa; o `CibSevenError` chega ao chamador.
3. Com a flag ligada e timeout de leitura (ou resposta 5xx): linha `EFFECT_UNKNOWN`, nunca `FAILED_BEFORE_EFFECT`.
   Teste negativo: mutar a classificação para `FAILED_BEFORE_EFFECT` deixa o teste vermelho.
4. Com a flag ligada e start bem-sucedido: linha `STARTED` com o `instance_id`; caminhos de dedup geram
   `ALREADY_ACTIVE`/`ALREADY_COMPLETED`.
5. Falha da gravação da linha de desfecho: start bem-sucedido continua devolvendo a instância (sem exceção); falha de
   start relança o `CibSevenError` original.
6. Nenhuma linha de desfecho carrega texto de exceção nem variável de start (cerca PHI sobre `details`).
7. Reentrega da mesma tentativa com o mesmo desfecho não duplica a linha (dedup).
8. Reconciliação: tentativa com `EFFECT_UNKNOWN` + histórico com instância → `STARTED` com `fonte = reconciliacao`;
   sem instância → `NOT_STARTED`; nenhum claim de família com trava é liberado.
9. Todo membro de `StartOutcome`, exceto `UNREPORTED`, aparece na tabela A.3 desta ADR (cerca já entregue neste PR em
   `tests/unit/docs/test_adr0073_desfecho_start_errata_rn259.py`).
10. Integração no motor local (CIB Seven), duas vezes no mesmo motor, com o motor parado para o caso 2.
11. Com a flag ligada e `find_active_instance` levantando `CibSevenError` no passo 3: uma linha de tentativa + uma linha `FAILED_BEFORE_EFFECT`; o `CibSevenError` chega ao chamador.

---

## Parte B — Errata da citação RN 259 (ADR-0005, `L0-core.yaml`, contratos)

### Contexto

O §Contexto do ADR-0005 diz: "RN 259 proibe negativa de cobertura sem medico auditor." O mesmo fundamento aparece no
comentário de `authorization_denial` em `spec/policies/autonomy/L0-core.yaml`. Dois problemas:

1. **A RN 259/2011 foi revogada** pela RN 566/2022, art. 16, I. Texto-base no repo:
   `docs/compliance/legal/textos-base/RN-566-2022.md` — "Art. 16. Ficam revogados: I - a Resolução Normativa - nº 259,
   de 17 de junho de 2011;".
2. **A RN 259 tratava de garantia de atendimento (prazos), não de quem decide a negativa.** Nenhuma das normas abaixo
   diz, com essas palavras, que "toda negativa exige médico auditor". A regra L0 hard do ADR-0005 é mais restritiva que o
   texto legal, e é uma escolha interna de desenho.

**Por que a errata mora aqui e não no ADR-0005.** O ADR-0005 é Accepted e está fixado byte a byte por
`tests/unit/docs/test_adr_amendments.py` (sha256 do arquivo, e a proibição de bloco `## Emenda` dentro dele). A regra do
índice (`docs/adr/README.md`, regra 2) é que ADR aceito só muda por ADR nova. Por isso a errata fica nesta Parte B, no
molde do ADR-0041, e o arquivo do ADR-0005 não é tocado.

### Decisão (errata aprovada pelo dono em 2026-10-31; ratificação jurídica pendente — R-054 L9)

**B.1 — Onde se lê "RN 259" como fundamento da negativa humana, leia-se:**

- **Negativa por mérito clínico:**
  - Lei 12.842/2013, art. 5º, II — "Art. 5º São privativos de médico: I - (VETADO); II - perícia e auditoria médicas;
    coordenação e supervisão vinculadas, de forma imediata e direta, às atividades privativas de médico;"
  - RN 424/2017, quando houver divergência técnico-assistencial: junta médica ou odontológica (art. 6º); "§ 4º O
    parecer do desempatador será acatado para fins decobertura." (art. 6º, § 4º; grafia colada do HTML do DOU,
    preservada no texto-base); e a não realização indicada pelo
    desempatador não caracteriza negativa indevida se o rito foi cumprido (art. 20). Texto-base:
    `docs/compliance/legal/textos-base/RN-424-2017.md`.
- **Negativa administrativa:**
  - ADR-0005 (política interna, escolha de desenho; não é norma);
  - RN 623/2024, art. 14 — redução a termo da negativa, com o motivo e a cláusula contratual ou o dispositivo legal que a
    justifique; e art. 16 — reanálise pela Ouvidoria. Texto-base: `docs/compliance/legal/textos-base/RN-623-2024.md`;
  - LGPD (Lei 13.709/2018), art. 20, caput — "O titular dos dados tem direito a solicitar a revisão de decisões tomadas
    unicamente com base em tratamento automatizado de dados pessoais que afetem seus interesses, [...]".

**B.2 — Onde se lê "RN 259" como norma de garantia de atendimento (prazos, rede), leia-se RN 566/2022**, que a sucedeu
(art. 16, I). A RN 566 não traz critério de distância (R-054 L6): onde um contrato fala em "tempos/distâncias", o
componente de distância fica sem âncora normativa e segue DRAFT/verify. Nenhum valor de prazo ou threshold muda por esta
errata.

**B.3 — O que esta errata NÃO muda.** A regra continua igual: `authorization_denial` segue `L0` + `hard: true`, a
negativa continua decisão de médico auditor humano, e o mecanismo do ADR-0005 (PEP, User Task, separação de
credenciais, recusa auditável) é o mesmo. A errata troca a fonte citada, não a regra.

**B.4 — Onde a errata foi aplicada neste PR:** o comentário de `authorization_denial` e o de
`adequacao_fallback_commitment` em `spec/policies/autonomy/L0-core.yaml`; as linhas que citavam a RN 259 sozinha em
`docs/processes/contracts/SP-OP-ADEQUACAO-001.md`, `docs/processes/contracts/SP-OP-AUTH-001.md` e
`docs/processes/contracts/SP-OP-REEMBOLSO-001.md`. As linhas que já traziam a co-citação "RN 259/566" ficaram como
estavam (em `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`, as linhas 109, 158, 217, 354 e 359; e
`docs/processes/contracts/SP-OP-CRED-001.md:270`): a co-citação já nomeia a sucessora, e esta errata não reescreve essas
linhas.
Os números de linha não mudaram, para não quebrar as âncoras de `docs/sme-dispatch/regulatorio/rn-currency-matrix.yaml`,
e o token "RN 259" ficou nas linhas ancoradas, agora com a menção à revogação.

**B.5 — Citações de RN 259 que ficam para outro pacote** (lista NÃO exaustiva; o inventário completo é `git grep -nP "RN ?259(?!\d)" -- . ':!docs/audits'`). Entre elas, as que ainda dão a RN 259 como fundamento da negativa:
`docs/processes/catalog.md`, `docs/adr/0001-cib-seven-governance-langgraph-runtime.md` (Accepted; a mesma regra de
emenda por ADR nova vale), os comentários em `src/maezo/gateway/pep.py` e `src/maezo/gateway/credential_vault.py`, a
documentação do BPMN `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`, as DMNs e os manifestos de
ratificação (`spec/processes/dmn/`) e `spec/policies/autonomy/action-approvals.yaml`.

**Fontes e verificação.** Os verbatins da RN 566, RN 623 e RN 424 foram conferidos nesta sessão contra os textos-base
versionados acima. Os da Lei 12.842/2013 e da LGPD art. 20 vêm da folha R-054 L9, que os transcreveu de planalto.gov.br
em 2026-10-31, com sha256 do download (`60432dba…4879` e `11e45058…d5dd`). Nova tentativa de download nesta sessão:
planalto.gov.br não respondeu (HTTP 000), e esses dois verbatins não foram reconferidos aqui.

### Consequências

**Positivas:** a política L0 deixa de se apoiar em norma revogada, e a defesa regulatória fica honesta: a regra é
interna e mais restritiva que a lei, e cada parte tem sua base legal.
**Negativas (aceitas):** a errata depende de ratificação jurídica (R-054 L9) e convive, até o próximo pacote, com as
citações listadas em B.5.

---

## Supersedes

—. **Emenda** o ADR-0007 (leitura da linha de start, Parte A) e o ADR-0005 (citação do §Contexto, Parte B), sem editar
os arquivos deles. Complementa o ADR-0045 (o desfecho de agente `erro_inicio_processo` ganha par na trilha durável).

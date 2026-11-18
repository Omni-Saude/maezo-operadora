# ADR-0079: Dossiê pseudonimizado da autorização como zona geral — lista fechada de campos, chave HMAC, controles e resposta ao P8 (camada "seu caso" da Helena)

**Status:** Proposto (Proposta — aguarda aprovação do dono). Redigido por AGENTE a partir da decisão do dono de 18/11/2026 (PROG-COORD-31, pendência P4 do Programa Circuito Fechado). Nenhum agente marca esta ADR como aprovada e nenhum campo de assinatura ou ratificação foi preenchido (ADR-0007). `/docs/adr/` é CODEOWNERS.
**Data:** 2026-11-18
**Área:** LGPD / Segurança (zonas de dado) / Inferência / Autorização prévia

## Contexto

### A pendência P4 e a decisão do dono

Nas citações, o texto entre colchetes é inserção editorial desta ADR: troca o prefixo `mzo:` do original, que aponta para este repositório, por "[no Maezo,]", ou completa o caminho de um símbolo.

O plano do Programa Circuito Fechado, no repositório `regras-contratos-operadora` (rco), registra a pendência assim (rco, arquivo docs/programa/plano.md, linha 37):

> "P4 | Zona de dado sensível do `mzo`: contratar endpoint de inferência residente no Brasil com DPA **ou** aprovar ADR que trata o dossiê pseudonimizado como zona geral | Falta é contratual, não de código ([no Maezo,] `docs/status/validacao-aws-2026-08-18.md:164`); sem isso Rafael e Marina não sobem em produção"

A lista de pendências do dono repete a pergunta (rco, docs/programa/decisoes-pendentes-do-dono.md, linha 9): "Zona de dado sensível do Maezo: contratar endpoint de inferência residente no Brasil, com DPA, ou aprovar ADR que trata o dossiê pseudonimizado como zona geral | Agentes de autorização em produção | Onda 2". O P4 está no caminho crítico do programa (rco, docs/programa/plano.md, linha 187: "**P4** (zona de dado sensível) → MZO-7").

O relatório de validação do Maezo descreve as duas saídas (`docs/status/validacao-aws-2026-08-18.md:162-170`):

> "1. **Provisionar o endpoint BR-resident + o DPA** (`MAEZO_PHI_VENDOR_DPA_REF`). É o caminho que o desenho prevê. [...]
> 2. **Decidir que a narrativa do dossiê pseudonimizada é chamada de zona geral.** [...] **É decisão registrada, com ADR, não ajuste de deploy** — e não é minha para tomar."

**Decisão do dono, 18/11/2026 (PROG-COORD-31):** aprovar uma ADR de zona geral. O dossiê pseudonimizado é tratado como zona GERAL, e não como zona sensível. Esta ADR registra essa decisão e não a reabre.

**Alternativa considerada e não escolhida:** contratar um endpoint de inferência residente no Brasil, com acordo de tratamento de dados (DPA), para servir a narrativa na zona PHI. Era o caminho que o desenho previa (`docs/status/validacao-aws-2026-08-18.md:162-163`) e a recomendação da coordenação do programa. O adaptador correspondente já existe e recusa construir sem a referência do DPA (`docs/status/validacao-aws-2026-08-18.md:152-155`). A escolha entre as duas é do dono.

### A pendência P8

O plano registra (rco, docs/programa/plano.md, linha 41): "P8 | Confirmar no ADR do P4 se a camada 'seu caso' da Helena (que avalia CPT, dado de saúde) roda em zona geral | [no Maezo,] `spec/agents/helena/agent.yaml:78`; se não, HEL-4 sai do piloto". A lista de pendências (rco, docs/programa/decisoes-pendentes-do-dono.md, linha 13): "Confirmar se a camada 'seu caso' da Helena, que avalia CPT, pode rodar em zona geral | Helena, camada 3 | Onda 2". A unidade HEL-4 é a "Camada 3 'seu caso': avaliação dos DMN do `rco` com os dados do beneficiário" (rco, docs/programa/plano.md, linha 146). Nota de precisão: no head desta ADR, a linha `security_zone: general` da Helena está em `spec/agents/helena/agent.yaml:79`, não `:78`.

### O modelo de zonas que esta ADR aplica

- ADR-0006 divide por agente: "**Zona Geral** (Helena, Lucas, Fernando, Gustavo): PHI pseudonimizado no Tool Gateway ANTES do contexto LLM; mapa de reidentificacao so no gateway; modelos cloud com DPA + regiao" e "**Zona PHI/Financeira** (Rafael, Marina, ...): PHI integral somente em modelo on-prem ou endpoint contratado com residencia BR + zero-retention" (`docs/adr/0006-phi-two-zones.md:9-14`).
- O Rafael é zona PHI (`spec/agents/rafael/agent.yaml:11`); a Marina também (`spec/agents/marina/agent.yaml:24`); a Helena é zona geral (`spec/agents/helena/agent.yaml:79`).
- ADR-0049 D3: "Acesso humano autorizado a um dossiê não dissolve as zonas: componentes que servem PHI bruto e anexos permanecem na Zona PHI; engine na Zona Geral recebe somente a projeção classificada e permitida pelo contrato, referências/digests de evidência e proveniência minimizada. Não copiar anexos ou narrativa clínica bruta ao engine, audit log ou evento geral." (`docs/adr/0049-portal-humano-e-comandos-atomicos.md:197-200`).
- A inferência marcada `phi=True` contra provedor que não é apto a PHI levanta `PhiZoneRoutingError`, sem cair para o provedor geral (`src/maezo/runtime/inference/__init__.py:697-705`).

### O que é o "dossiê" hoje, no código

O dossiê é montado por `src/maezo/agents/rafael/graph.py::RafaelGraph._build_dossier` (`graph.py:782-847`). Ele faz a única chamada de modelo de linguagem do fluxo de autorização: a narrativa que o médico auditor lê antes de decidir (`graph.py:823-829`, `task_kind="reasoning"`). O prompt é `dossier_prompt()` + `route` + `motivo_auditor` + os fatos renderizados (`graph.py:806-809`). Os fatos são, literalmente (`graph.py:785-803`): `numero_guia_tiss`, `codigo_procedimento_tuss`, `categoria_procedimento`, `carater_atendimento`, `valor_estimado_brl`, `cid10`, `beneficiario_ativo`, `carencia_cumprida`, `dut_atendida`, `dentro_teto_l2`, `rede_credenciada`, `documentacao_completa`, `admissibilidade`, `recomendacao_auto`, `dmn_refs`, `sla_analise` e `lacunas_enriquecimento`.

Três desses campos impedem chamar o prompt de hoje de "pseudonimizado" no sentido desta ADR: `numero_guia_tiss` (identificador que o prestador e a operadora ligam à pessoa sem a chave), `cid10` (diagnóstico) e `lacunas_enriquecimento`, que é `list[str]` e não vocabulário fechado no tipo (`graph.py:210`), embora hoje só receba frases fixas e o nome da classe de exceção (`graph.py:484-487`, `:505`, `:517`).

O dossiê devolvido também carrega `documentos_refs` e `decisao_cobertura` (sempre `None`, guarda L0) (`graph.py:832-841`), e vai ao motor na variável `dossie_rafael` (`graph.py:869`). A narrativa passa pela limpeza de texto livre `src/maezo/tools/workers/phi_vars.py::redact_free_text_vars` antes do start do processo (`phi_vars.py:15-20`).

O envelope A2A que chega ao Rafael carrega "the case's non-PHI identifiers (tenant, guide number, pseudo ids, category, care type, value) plus the pre-resolved-by-worker booleans; `payload_ref` is the FHIR coverage/beneficiary resource [...]. Rafael never receives a CPF/name over this seam" (`src/maezo/agents/rafael/delegation.py:37-40`); o `cid10` entra no estado quando vem no `payload_meta` (`delegation.py:126-127`).

### O interruptor que já existe

A zona da narrativa já é decidida por um artefato de dado, não por código: `src/maezo/platform/privacy/dossier_zone.py::dossier_narrative_requires_phi_zone` lê `spec/policies/phi/dossier-narrative-zone.yaml` e, sem ratificação válida, devolve `True` (zona PHI). O arquivo está `status: DRAFT`, `ratificado: false` (`dossier-narrative-zone.yaml:68-69`) e diz quem pode ratificar: "Somente o DPO **e** o corpo clínico (médico auditor responsável), juntos. Nenhum agente, nenhum orquestrador, nenhum automatismo pode preencher estes campos." (`dossier-narrative-zone.yaml:21-23`). A ratificação é presa ao sha256 de `graph.py` (`dossier-narrative-zone.yaml:55-59`). A variável `MAEZO_DOSSIER_NARRATIVE_GENERAL_ZONE_SYNTHETIC_ONLY` só declara ambiente sem dado real e não é ratificação (`dossier_zone.py:40-56`).

O próprio arquivo descreve o argumento contrário: "o prompt da narrativa ([`src/maezo/agents/rafael/graph.py::RafaelGraph._build_dossier`]) leva os FATOS do caso [...] Fato clínico sobre uma pessoa continua sendo dado de saúde mesmo sem o nome, se o pseudônimo é re-identificável por quem detém a chave — e o tenant detém." (`dossier-narrative-zone.yaml:34-38`). E a consequência prática: o provedor da zona geral hoje usa um perfil de inferência que "**roteia entre regiões por desenho** [...] Ratificar GERAL é, na prática, aceitar transferência internacional para essa narrativa." (`dossier-narrative-zone.yaml:44-49`; também `docs/runbooks/phi-inference-ops.md:51-57`).

O provedor da zona geral declara `phi_allowed=False`, `retention_policy=RetentionPolicy.UNSPECIFIED` e `training_on_input_prohibited=False`, e o código registra que "no zero-retention or no-training agreement with AWS exists anywhere in this repo's tree" (`src/maezo/runtime/inference/capabilities.py:303-311`).

### Base normativa e o seu limite

Fonte: Lei 13.709/2018 (LGPD), registrada no rco como `LEI-13709-2018` (rco, fontes/registro-fontes.csv, linha 5; texto primário em rco, fontes/brutas/LEI-13709-2018/).

- Art. 5º, II: dado pessoal sensível inclui "dado referente à saúde [...] quando vinculado a uma pessoa natural".
- Art. 11, *caput*: "O tratamento de dados pessoais sensíveis somente poderá ocorrer nas seguintes hipóteses: [...]"; § 1º: aplica-se "a qualquer tratamento de dados pessoais que revele dados pessoais sensíveis e que possa causar dano ao titular".
- Art. 12: só os dados **anonimizados** deixam de ser dados pessoais, "salvo quando o processo de anonimização [...] for revertido, utilizando exclusivamente meios próprios, ou quando, com esforços razoáveis, puder ser revertido".
- Art. 13, § 4º: "Para os efeitos deste artigo, a pseudonimização é o tratamento por meio do qual um dado perde a possibilidade de associação, direta ou indireta, a um indivíduo, senão pelo uso de informação adicional mantida separadamente pelo controlador em ambiente controlado e seguro."
- Art. 33: hipóteses em que a transferência internacional de dados pessoais é permitida.

**Limite da base.** O art. 13 § 4º define pseudonimização "para os efeitos deste artigo", que trata de estudos em saúde pública por órgãos de pesquisa. Usar essa definição para o tratamento feito pela operadora é aplicação por analogia. A LGPD não diz, em nenhum artigo, que dado de saúde pseudonimizado pode ser tratado com menos salvaguardas que dado de saúde identificado, nem que ele dispensa a hipótese do art. 11 ou do art. 33. Portanto, **classificar o dossiê pseudonimizado como zona geral é interpretação sem base normativa expressa**. A coordenação do programa registra este ponto, à parte, em `governanca/juridico` do rco, para revisão assíncrona do encarregado (DPO) e do jurídico. Esse registro **não bloqueia** esta ADR nem a sua implementação.

## Decisão

### 1. O que é o dossiê pseudonimizado e para que serve

O **dossiê pseudonimizado** é uma projeção tipada, de campos fechados, que o Maezo monta a partir do estado do caso de autorização prévia e que é a **única entrada** permitida da chamada de modelo de linguagem que redige a narrativa do dossiê (`RafaelGraph._build_dossier`).

- **Finalidade única:** redigir a narrativa que ajuda o médico auditor a ler o caso. A narrativa não decide nada: a rota (`auto_approve` ou `human_auditor`) e os fatos booleanos chegam prontos dos workers e das tabelas DMN; `decisao_cobertura` continua sempre `None`; a negativa só nasce na tarefa humana (`graph.py:23-33`).
- **Zona:** a projeção é zona GERAL. O que fica fora dela continua zona PHI (seção 4).
- **Natureza jurídica:** é **pseudonimização, não anonimização**. O controlador mantém a informação que reassocia a projeção à pessoa (a chave HMAC e o vínculo do processo). Logo, a projeção **continua sendo dado pessoal** e, por conter procedimento, caráter e fatos de cobertura de uma pessoa, **continua sendo dado pessoal sensível** (LGPD, arts. 5º, II, e 12). Ser "zona geral" muda o provedor que pode recebê-la; não muda a natureza do dado nem as obrigações da LGPD.
- **Alcance:** só a narrativa do dossiê do Rafael. A Marina e qualquer outro agente da zona PHI ficam fora desta ADR; estender exige uma lista própria de campos, por emenda.
- **O agente não muda de zona.** `spec/agents/rafael/agent.yaml` continua `security_zone: phi`; a leitura FHIR, os anexos e as tarefas humanas do Rafael ficam onde estão.

### 2. Campos

A projeção é uma lista de permissão (allowlist). Campo que não está na tabela "pode entrar" não entra, mesmo que não esteja na tabela "nunca entra".

**Pode entrar** (tudo já existe no estado do Rafael; nenhum campo novo é criado):

| Campo | Forma na projeção | Por que pode | Origem |
|---|---|---|---|
| `codigo_procedimento_tuss` | código TUSS, sem descrição livre | é o objeto do pedido; sem ele a narrativa não existe | `graph.py:787` |
| `categoria_procedimento` | enum fechado (`consulta`, `exame_simples`, `exame_especial`, `terapia`, `internacao`, `opme`, `alta_complexidade` ou vazio) | vocabulário fechado | `graph.py:115-117`, `:788` |
| `carater_atendimento` | `urgencia` ou `eletivo` | vocabulário fechado (DL-0170) | `graph.py:789` |
| `valor_estimado_brl` | número | sem identificação; o teto é apurado pelo motor | `graph.py:790` |
| `beneficiario_ativo`, `carencia_cumprida`, `dut_atendida`, `rede_credenciada`, `documentacao_completa` | `true`, `false` ou sem dado | fatos já resolvidos por worker; o CID que levou a `dut_atendida` fica fora | `graph.py:792-798`, `:394-400` |
| `dentro_teto_l2` | só a linha fixa "apurado pelo motor" | a apuração é do motor | `graph.py:403-416` |
| `admissibilidade`, `recomendacao_auto` | enums fechados | vocabulário fechado | `graph.py:112-113` |
| `route`, `motivo_auditor` | enums fechados | vocabulário fechado | `graph.py:111`, `:114` |
| `dmn_refs` | só a referência da decisão DMN avaliada, nunca os valores de entrada | proveniência | `graph.py:583-604` |
| `sla_analise` | duração ISO 8601 | prazo, não pessoa | `graph.py:801` |
| `lacunas_enriquecimento` | só tokens de um vocabulário fechado (as frases fixas de hoje e o nome da classe da exceção) | diagnóstico técnico, sem argumento da chamada (CC-10) | `graph.py:484-487`, `:497-505`, `:513-517` |

**Nunca entra** (nem em claro, nem pseudonimizado, nem resumido, nem como parte de outro campo):

| Dado | Por quê | Âncora |
|---|---|---|
| Nome, CPF, telefone, e-mail | identificação direta | `src/maezo/gateway/pseudonymizer.py:41-48` (`PHI_FIELDS`) |
| CNS (Cartão Nacional de Saúde) | identificação direta | rco, validacao/backtest/coleta/pseudonimizar.py, linhas 55-58 (padrão `PROIBIDAS`) |
| Número da carteirinha e matrícula do beneficiário | identificação direta | `src/maezo/tools/workers/phi_vars.py:68` (`matricula_beneficiario`) |
| Endereço, CEP, bairro | identificação indireta | rco, pseudonimizar.py, linhas 55-58 |
| Data de nascimento e qualquer data ligada à pessoa (atendimento, internação) | quase-identificador; a narrativa não precisa de data | rco, pseudonimizar.py, linhas 14-15 (idade só em anos ou faixa) |
| Idade exata e sexo | não usados pela narrativa hoje; entram só por emenda, e então a idade só em faixa de 5 anos | rco, pseudonimizar.py, linha 62 (`FAIXAS`) |
| `numero_guia_tiss`, número da guia no prestador | o prestador e a operadora ligam a guia à pessoa sem a chave | `graph.py:786`; rco, pseudonimizar.py, linhas 55-58 |
| `beneficiario_pseudo_id`, `portable_subject_ref`, `prestador_id`, `hk1_*` e qualquer outro pseudônimo | a narrativa não precisa de identidade; a correlação fica no Maezo | seção 3 |
| `payload_ref`, `coverage_ref`, `patient_ref`, `documentos_refs` e o conteúdo dos recursos FHIR (`coverage_facts`, `patient_facts`) | apontam para dado integral ou anexo da zona PHI | `delegation.py:124-128`; `graph.py:478-526`; ADR-0049 D3 |
| CID-10 (`cid10`, `cid10_referencia`), diagnóstico, código ou descrição de doença | dado de saúde explícito; a decisão DUT chega como booleano | `phi_vars.py:64`, `:71`; `src/maezo/platform/validation/phi_completeness.py:730-756` |
| Texto livre de qualquer origem: justificativa clínica, indicação clínica, laudo, notas, fundamentação, mensagem do beneficiário, resumo de contexto | conteúdo clínico não estruturado | `phi_vars.py:61-73` (`PHI_PROCESS_VARS`), `:312-320` (`PHI_FREE_TEXT_VARS`) |
| Anexos e imagens | zona PHI | ADR-0049 D3 (`0049:197-200`) |
| CRM, CNPJ ou outro identificador do profissional ou do estabelecimento | identificação de terceiro; `rede_credenciada` basta | rco, pseudonimizar.py, linhas 55-58 |
| Situação de CPT, doença ou lesão preexistente e declaração de saúde | dado de saúde; não é usado pela narrativa | seção 6 |

**Mudança de código que esta lista exige antes de valer:** o prompt de hoje leva `numero_guia_tiss` e `cid10` (`graph.py:786`, `:791`). Esses dois saem do prompt; continuam no dicionário `fatos` do dossiê devolvido ao motor, que é outra superfície e segue as regras que já tem.

### 3. Pseudonimização: algoritmo, chave e reassociação

- **O provedor não recebe identificador nenhum.** A projeção da seção 2 não leva pseudônimo. A narrativa volta para a mesma execução do grafo que a pediu, e é essa execução que a liga ao caso. Por isso o dossiê pseudonimizado é pseudonimizado por **supressão** dos identificadores, e não pela troca de um identificador por outro.
- **Pseudônimo para correlação interna.** Onde o Maezo precisar registrar a chamada de inferência (log, auditoria, métrica), o caso aparece só como pseudônimo `hk1_` gerado por HMAC-SHA256 com chave, pelo mesmo `src/maezo/gateway/pseudonymizer.py::Pseudonymizer` usado pelas business keys opacas (ADR-0035; ADR-0068 §2; `src/maezo/platform/privacy/key_scrubber.py::_digest`). Nenhum segundo algoritmo é criado.
- **Chave.** Em produção, a chave é `PHI_HMAC_KEY`, sincronizada do cofre pela ExternalSecret `phi-hmac-key`; sem ela, `Pseudonymizer.from_settings` levanta `PseudonymizerKeyMissingError` e nada é servido (`pseudonymizer.py:109-140`). Em dev/CI, a chave é derivada por tenant e não é secreta (`pseudonymizer.py:66-68`). Fato a registrar: no código de hoje, a chave de produção é **uma por implantação**, não derivada por tenant; a "chave por tenant" do ADR-0068 (`docs/adr/0068-migracao-chaves-maezo-payer-janela-unica-dual-read.md:173`) depende de implantação por tenant ou de derivação ainda não escrita. A chave nunca entra no repositório, em log, em prompt ou no ambiente de um agente da zona geral.
- **Quem pode reassociar.** HMAC não se inverte. Reassociar um pseudônimo a uma pessoa exige as duas informações adicionais, e as duas ficam na zona PHI e com o controlador: a chave e o identificador em claro (número da guia ou cadastro). Só um processo da zona PHI que já tem esse vínculo, ou um humano autorizado pela trilha do portal (ADR-0049 D3), reassocia. O procedimento humano formal de reassociação (quem pede, quem aprova, onde fica registrado) não existe no repositório: **LACUNA**, unidade U6 da seção 8.

### 4. O que fica na zona sensível (PHI)

Fica na zona PHI tudo o que a seção 2 lista como "nunca entra", e também:
- a leitura FHIR do Rafael (`gather`) e os recursos que ela devolve;
- os anexos e documentos do caso e as suas referências;
- o vínculo entre o caso e a pessoa: número da guia, pseudônimos e chave;
- a decisão clínica, que só existe na tarefa humana do médico auditor;
- os outros agentes da zona PHI (Marina, Beatriz, Valentina, Carolina, André), que esta ADR não toca;
- a avaliação de CPT que dependa de CID ou de declaração de saúde (seção 6).

### 5. Controles

1. **Projeção tipada e fechada.** Um tipo próprio (por exemplo `DossiePseudonimizado`, `frozen`) com exatamente os campos "pode entrar"; o prompt só é montado a partir dele. Campo novo exige emenda desta ADR.
2. **Interruptor inalterado.** `dossier_narrative_requires_phi_zone` continua decidindo a zona e continua fail-closed. Esta ADR não preenche `spec/policies/phi/dossier-narrative-zone.yaml`. O arquivo se preenche pelo procedimento descrito nele (`dossier-narrative-zone.yaml:51-63`), que hoje exige `dpo_review: APPROVED` e `medico_auditor_review: APPROVED`. A ratificação continua presa ao sha256 de `graph.py`; esta ADR acrescenta que ela também cubra a versão da projeção.
3. **Testes que fazem a lista valer** (a escrever, unidade U2):
   - o prompt montado contém só chaves da allowlist, e o teste falha se `_build_dossier` ler do estado um campo fora dela;
   - com valores plantados no estado (CPF válido, telefone, e-mail, CID, número de guia, `hk1_`, URL FHIR, texto livre), nenhum deles aparece no prompt;
   - `lacunas_enriquecimento` aceita só tokens do vocabulário fechado;
   - com a ratificação ausente ou inválida, a chamada continua `phi=True` (caso que já existe e deve continuar verde);
   - a cerca de completude PHI (`phi_completeness.check_sweep`) e o manifesto `spec/policies/phi/phi-dispositions-migration.yaml` não mudam; nome novo na projeção passa pela cerca como qualquer variável.
4. **Registro.** Cada chamada de narrativa em zona geral grava em log estruturado: zona efetiva, motivo (ratificação ou ambiente sintético), versão da projeção, sha256 do prompt e o pseudônimo `hk1_` do caso. Nunca o prompt, a narrativa ou um campo da projeção.
5. **Log de invocação no provedor desligado.** O log de invocação do provedor continua fora do conjunto de permissões, como hoje ("Ligar logging de invocação | DPO | fora do permission set, de propósito", `docs/status/plano-acesso-bedrock-time-agentes-2026-08-18.md:196`).
6. **Retenção.** No Maezo, a narrativa fica só em `dossie_rafael`, depois de `redact_free_text_vars`, com a retenção que a matriz de retenção der à variável do processo. A matriz ainda é rascunho não ratificado (`spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml`): **LACUNA** de prazo. No provedor, a retenção está declarada como `UNSPECIFIED` (`capabilities.py:306-311`): **LACUNA** contratual.
7. **Plano de eliminação.** `spec/policies/retention/erasure-plan.template.yaml` ganha uma linha para a narrativa do dossiê em `dossie_rafael` e para o log da seção 5.4. A eliminação continua não executável (`ErasureManager.erase()` levanta sempre, `erasure-plan.template.yaml:26-29`). O que foi enviado ao provedor não pode ser eliminado pelo Maezo; a mitigação é a projeção não levar identificador e a retenção do provedor ser resolvida por contrato.

### 6. P8: a camada "seu caso" da Helena e a CPT

**O que existe.** A habilidade `cobertura` da Helena tem três camadas: `regra_geral`, `seu_plano` ("carencia, cobertura contratual, CPT, rede, situacao do pedido — com as rotas do lago") e `seu_caso` ("avaliacao das tabelas do rco com os dados da pessoa — ONDA 3, nao existe aqui") (`src/maezo/agents/helena/habilidades/cobertura/__init__.py:6-10`; o tipo `Camada` só admite as duas primeiras, `:57-58`). A CPT que a camada 2 lê é minimizada na fonte: "nunca o CID nem a doenca, so' o sim/nao, QUANTAS linhas vigentes e a MAIOR data de fim" (`src/maezo/ports/tina.py:335-345`, `CptVigenteView`). O uso dessa fonte pela Helena foi autorizado como caso C1, só em dev e na allowlist, e depois a cobertura assistida saiu (DL-0130 e o adendo de 31/10/2026, `docs/decisions-log.md:95-96`).

A tabela de CPT do rco que a camada 3 avaliaria, `geral_cpt.dmn`, tem as entradas `dataAtendimento`, `caraterAtendimento`, `cptVigente`, `procedimentoAlcancadoCpt` e `cidNaDlpDeclarada`, e a sua linha de eletivo alcançado é "recomendação de negativa (Tab. 38: 3173 [...]), com decisão da auditoria médica" (rco, dmn/regras-gerais/geral_cpt.dmn, linhas 4 e 7-19).

**Resposta proposta ao P8: sim, com restrição.** A camada 3 pode rodar na zona geral nas condições abaixo. O que não cabe nelas fica fora do piloto ou vai para uma pessoa.

1. **Avaliação determinística, sem modelo de linguagem sobre o dado de saúde.** A tabela DMN é avaliada pelo motor (`mcp_dmn`). Nenhum modelo de linguagem recebe as entradas da tabela. O texto à pessoa, se houver, nasce só do identificador da regra e do desfecho, e passa no verificador da habilidade, como as camadas 1 e 2.
2. **Entradas permitidas na zona geral:** `cptVigente` (de `CptVigenteView.tem_cpt_vigente`), a quantidade e a maior data de fim da CPT vigente, `caraterAtendimento`, o código TUSS já resolvido e `dataAtendimento` (a data da solicitação, usada só para a vigência da regra; nunca vai a texto nem a log).
3. **Entradas que não podem ser calculadas na zona geral:** `cidNaDlpDeclarada` e `procedimentoAlcancadoCpt`. As duas dependem do CID do pedido e da declaração de saúde (doença ou lesão preexistente). Elas só podem ser calculadas na zona PHI, pelo montador de entradas (MZO-2), e chegar à camada 3 já como booleano. Enquanto a ingestão da declaração de saúde não tiver parecer do encarregado (P13, rco, docs/programa/decisoes-pendentes-do-dono.md, linha 18), essas entradas ficam sem dado e a regra não tem efeito na camada 3.
4. **Nunca passar à camada 3, ao seu log ou a um modelo:** CID; nome, código ou grupo da doença ou lesão preexistente; o conteúdo da declaração de saúde; o código de carência; laudo; o texto da mensagem da pessoa como entrada de tabela; qualquer identificador da seção 2. A pessoa é identificada só por `portable_subject_ref`, como na camada 2 (`cobertura/__init__.py:85-86`).
5. **Desfecho adverso nunca vira resposta.** Uma linha de "recomendação de negativa" (ou qualquer desfecho adverso) não é dita à pessoa como negativa nem como previsão de negativa: a camada devolve `transferir` e registra a lacuna, como já faz quando falta fonte. Isso preserva a guarda L0 de `authorization_denial` e a fórmula "informação não é decisão" (DL-0167).

Se o dono não aprovar estas condições, vale o corte já previsto no plano: "HEL-4 (camada 'seu caso') sai do piloto" (rco, docs/programa/plano.md, linha 196).

### 7. O que esta ADR não faz

- Não altera `spec/policies/autonomy/`, `_hard_frozen.yaml`, código, BPMN, DMN, `deploy/` nem helm.
- Não preenche `spec/policies/phi/dossier-narrative-zone.yaml` nem `spec/policies/phi/phi-dispositions-migration.yaml`.
- Não muda a partição por agente do ADR-0006: aplica-a a uma projeção de um agente da zona PHI.
- Não resolve a base de transferência internacional (art. 33) nem a hipótese do art. 11 para o envio ao provedor da zona geral. As duas vão ao registro jurídico da coordenação.

## Consequências

**Positivas:**
- O Rafael (narrativa) pode subir em produção sem endpoint residente, depois das unidades U1 a U4.
- A lista de campos deixa de ser uma afirmação ("Rafael never receives a CPF/name") e passa a ser uma allowlist testada.
- A narrativa enviada ao provedor perde três exposições que tem hoje: número da guia, CID e lista de texto aberta.
- O P8 recebe uma resposta objetiva: o que a camada 3 pode avaliar na zona geral, com quais entradas, e o que fica fora.

**Negativas (aceitas pelo dono):**
- O dado continua sendo dado pessoal sensível. A zona geral passa a tratar dado de saúde pseudonimizado de autorização, e não só dado de atendimento.
- O provedor da zona geral roteia entre regiões, não tem retenção nem proibição de treino declaradas e não tem DPA registrado no repositório (`capabilities.py:303-311`; `docs/runbooks/phi-inference-ops.md:51-57`). O envio da projeção é, na prática, transferência internacional.
- A narrativa fica mais pobre: sem CID e sem número de guia, ela não descreve o quadro clínico; o médico auditor continua lendo o caso integral na zona PHI.
- A camada 3 na zona geral avalia menos regras: as que dependem de CID ou da declaração de saúde só funcionam com o booleano vindo da zona PHI.

**Riscos:**
- Reidentificação pela combinação de procedimento raro, valor e caráter em tenant pequeno. Mitigação: a projeção não leva identificador nem data; a revisão do encarregado avalia o risco residual.
- A projeção crescer sem decisão. Mitigação: a allowlist tipada, os testes da seção 5.3 e a exigência de emenda.
- A ratificação do interruptor depender hoje de `dpo_review: APPROVED`, enquanto a revisão do encarregado sobre este ponto é assíncrona e não bloqueia. Se o dono quiser que o interruptor não dependa dessa aprovação, isso é uma mudança no carregador e no arquivo de política, com decisão própria; esta ADR não a faz.

**Nota de revisão jurídica.** A classificação do dossiê pseudonimizado como zona geral é interpretação sem base normativa expressa (Contexto, "Limite da base"). A coordenação do programa registra o ponto em `governanca/juridico` do rco para revisão assíncrona do encarregado (DPO) e do jurídico. O registro não bloqueia a aprovação desta ADR pelo dono nem as unidades da seção 8. Se a revisão concluir de outro modo, a reversão é `ratificado: false` no arquivo do interruptor (`dossier-narrative-zone.yaml:65`), e a narrativa volta à zona PHI no boot seguinte.

**LACUNAS registradas:**
- procedimento humano de reassociação de pseudônimo (seção 3);
- derivação da chave HMAC por tenant em produção (seção 3);
- prazo de retenção da narrativa no Maezo (matriz não ratificada) e retenção no provedor (seção 5.6);
- DPA e base de transferência internacional com o provedor da zona geral (seção 7).

### 8. Unidades de implementação que esta ADR libera (só lista)

- **U1** — Projeção `DossiePseudonimizado` e `_build_dossier` montando o prompt só a partir dela (sai `numero_guia_tiss` e `cid10` do prompt; `lacunas_enriquecimento` em vocabulário fechado).
- **U2** — Testes da allowlist e dos valores plantados (seção 5.3).
- **U3** — Registro estruturado da chamada em zona geral (seção 5.4).
- **U4** — Linha do plano de eliminação para a narrativa e o log (seção 5.7).
- **U5** — Ligação da versão da projeção ao digest da ratificação do interruptor (seção 5.2).
- **U6** — Procedimento de reassociação de pseudônimo, para o encarregado (seção 3).
- **U7** — Derivação da chave HMAC por tenant em produção, ou registro de que há uma implantação por tenant (seção 3).
- **U8** — HEL-4 nas condições da seção 6: avaliação determinística, entradas permitidas, desfecho adverso como `transferir`.
- **U9** — No montador MZO-2, cálculo de `procedimentoAlcancadoCpt` e `cidNaDlpDeclarada` na zona PHI, depois do P13.
- **U10** — Preenchimento de `spec/policies/phi/dossier-narrative-zone.yaml` pelo procedimento do próprio arquivo (ato humano, não de agente).

## Supersedes

—

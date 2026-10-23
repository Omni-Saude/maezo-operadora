# ADR-0067: Fronteira entre plataforma e agentes, e o comportamento da Helena como pacote

**Status:** Proposed — fase 1 (catraca de imports) entregue junto com esta ADR; as fases 2 a 5 dependem do aceite
do dono
**Data:** 2026-10-23
**Área:** Arquitetura | Agentes (Helena) | CI/CD | Segurança
**Decisor:** dono do repositório (filipecarmo81 — CTO e DPO). A ratificação de conteúdo de PHI (fase 3) exige
também o médico auditor; nenhum agente assina.
**Complementa (não altera):** ADR-0066 (roteador de habilidades, fases F2/F5), ADR-0009 (abstração de inferência e
versões de prompt), ADR-0003/ADR-0015 (A2A entre agentes). **Apoia-se em:** Ratificação Q-6 de 12/08/2026
(PLANS §0.8: produção recusa `MAEZO_SPEC_DIR`; exceção futura "somente por bundle imutável com digest permitido").

## Contexto

Ajustar a Helena obriga a mexer, testar e reimplantar a plataforma inteira. Medido em 23/10/2026 na `main`:

- **Dependência nos dois sentidos.** A plataforma importa o interior dos agentes em 53 pontos: o receptor do
  WhatsApp chama o grafo da Helena dentro do próprio processo e usa constantes internas dela; o gateway e o portal
  também a importam. Por agente: Lucas 30 (14 arquivos), Helena 9 (7), André 6, Rafael 3, Carolina 2, Fernando 2 e
  Valentina 1. No sentido inverso, a Helena importa 22 módulos internos de `runtime`, `tools`, `a2a`, `ports` e
  `conhecimento`.
- **Regra de agente morando na plataforma.** Em `src/maezo/platform/webhooks/whatsapp/` estão `helena_consultas.py`,
  `helena_identidade.py`, `lucas_turno.py` e `lucas_retomada.py`; o `dispatch.py` (1.917 linhas) cita a Helena 148
  vezes. Em `src/maezo/runtime/`, os `cobranca_*.py`, `caso_clinico.py` e `terceiro_no_texto.py` são regra de
  conversa, não runtime genérico.
- **Efeito no trabalho.** Dos 186 commits da Helena nos últimos 60 dias, 92 (49%) alteraram também código da
  plataforma (webhooks 47, runtime 27, gateway 15) e 41 tocaram `deploy/`.
- **Efeito no deploy.** Uma imagem `app` única serve todos os serviços. Os prompts (`prompts.py`), o
  `agent.yaml`, o `roteador.yaml` e a base de saúde vão dentro dela. Trocar uma frase da Helena exige build pelo
  CodeBuild e um `terraform apply` com as 7 variáveis de imagem copiadas à mão das task definitions
  (`docs/runbooks/aws-ecs.md`). O `cd.yml` é no-op (`AWS_ENABLED` ausente) e o `deriva-dev` está desligado.
- **O grafo da Helena.** `src/maezo/agents/helena/graph.py` tem 11.381 linhas; a classe `HelenaGraph` sozinha tem
  6.461 linhas e 135 métodos, e o método `classify` tem 959 linhas, 70 `if` e 31 `return`. O arquivo carrega 91
  expressões regulares e cerca de 144 mil caracteres de texto em literais longos; 35% das linhas são comentário ou
  docstring. É o acúmulo de regras que a ADR-0066 descreve ("~88 regras de palavra-chave"); o grafo LangGraph em si
  tem só 8 a 10 nós.

Alternativas consideradas:

- **(a) Separar o repositório da Helena agora.** Rejeitada por ora. Com 22 módulos internos usados pela Helena e 9
  pontos da plataforma usando a Helena, dois repositórios virariam versionamento de biblioteca: cada mudança no
  runtime exigiria publicar um pacote e abrir um segundo PR. Separar o git não desacopla nada; a fronteira é que
  desacopla. Volta a ser opção quando valer o critério do §6.
- **(b) Um microsserviço por habilidade da Helena.** Rejeitada. A pergunta pendente é estado do turno compartilhado
  entre roteador e habilidades (ADR-0066); distribuí-la acrescenta um salto de rede por habilidade e estado
  distribuído, sem ganho de deploy que o pacote do §4 não dê.
- **(c) Fronteira dentro do mesmo repositório, comportamento como pacote e imagem própria da Helena.** Escolhida.

## Decisão

### 1. Uma direção só

A plataforma (`platform`, `gateway`, `runtime`, `tools`, `portal`, `a2a` e afins) não importa `maezo.agents.<id>`.
Um agente não importa outro agente; conversa entre agentes continua pela A2A (ADR-0003/ADR-0015). O framework de
agentes (`src/maezo/agents/__init__.py`: carregador de `agent.yaml`, `resolve_spec_dir`) não é agente e segue
importável. `maezo.qa` fica de fora: é ferramenta de teste, já isolada da produção por `check_qa_isolada.py`.

A plataforma fala com o agente por um contrato em `src/maezo/contratos/` — para conversa, a porta
`AgenteDeConversa` (entrada do turno → saída tipada com intenção, tipo de resposta, handoff e texto). O contrato é
versionado; mudança nele roda as suítes dos dois lados.

A plataforma não importa a implementação: guarda só o id do agente e pede ao framework de agentes, que
a encontra por convenção (`maezo.agents.carregar_agente_de_entrada("helena")` → `maezo.agents.helena.conversa`,
atributo `AGENTE_DE_ENTRADA`) e falha fechado na construção do despachante. Tipos que os dois lados usam
(vocabulário da identidade, porta dos fatos do plano) também moram em `maezo.contratos`.

### 2. Catraca no CI (fase 1, entregue com esta ADR)

`scripts/ci/check_fronteira_agentes.py` (alvo `make check-fronteira-agentes`, passo do CI ao lado da cerca do QA)
varre `src/maezo/` por AST. As 53 dependências de hoje estão listadas, uma a uma, em
`scripts/ci/fronteira_agentes_baseline.txt` — esse arquivo é o inventário do que mover. Dependência nova reprova;
linha da baseline que já não existe no código também reprova, para a baseline só encolher. `--relatorio` imprime o
inventário agrupado por agente e a lista de módulos da plataforma que a Helena usa.

### 3. A regra volta para o dono

O que é regra da Helena (ou do Lucas) e mora em `platform/webhooks/whatsapp/` ou em `runtime/` volta para
`agents/<id>/`, um bloco por PR, até a baseline daquele agente zerar. O receptor fica só com o canal: assinatura,
deduplicação, identidade, sessão e a escolha do agente.

### 4. O comportamento da Helena como pacote (fase 3)

Prompts, `roteador.yaml`, base de saúde, textos das cercas, limiares e escolha de modelo saem da imagem para um
**pacote da Helena**: artefato imutável, com versão, assinado, com digest permitido por ambiente — a forma que a
Ratificação Q-6 já admite. O runtime carrega o pacote ativo e confere a assinatura; voltar a versão anterior é mover
o ponteiro. A promoção do pacote passa por um validador (esquema, política de conteúdo, cercas, régua de tom) e
pelos roteiros maduros do simulador com modelo real. A ratificação de prompt que hoje carimba o hash do código
passa a carimbar o pacote; isso exige DPO e médico auditor.

### 5. O grafo da Helena por habilidade (junto com as fases F2/F5 da ADR-0066)

O `graph.py` deixa de concentrar a regra. Estrutura alvo, no mesmo processo:

```
agents/helena/
  grafo.py            só a fiação LangGraph (nós e arestas)
  estado.py           HelenaState e a pergunta pendente
  travas_entrada.py   consentimento, alarme (DMN), risco psicossocial, PII
  roteador.py         escolha da habilidade (ADR-0066)
  habilidades/<nome>/ cobertura, plano, saude, sintoma, equipe, pedidos, cobranca, conversa
  travas_saida.py     cercas de saída (cerca_de_saida.py)
```

Cada habilidade tem o próprio estado, os próprios textos (que vão para o pacote do §4) e os próprios testes.
Habilidade nova entra pelo contrato do roteador, sem tocar as outras. As regras de palavra-chave que o roteador
substituir são removidas na F5 da ADR-0066, não migradas.

### 6. Imagem e trilhas de entrega (fase 4)

A Helena ganha imagem própria; o serviço `agent-helena`, que hoje não executa turno, passa a executá-lo, e o
receptor o chama pela porta (HTTP interno ou fila). O CI passa a ter três trilhas por caminho: conteúdo (só o
pacote), lógica da Helena (só a imagem dela) e plataforma. Cada trilha implanta o próprio digest em dev
automaticamente; produção continua com aprovação humana (L5).

Separar o repositório volta a ser avaliado quando, ao mesmo tempo: outro time cuidar da Helena, ela depender só de
`maezo.contratos` e de um SDK publicado do runtime, e o ritmo de entrega dela for diferente do da plataforma.

### Fases e critério de saída

| Fase | Entrega | Sai quando |
|---|---|---|
| 1 | Catraca + inventário (este PR) | CI com a cerca obrigatória |
| 2 | `maezo.contratos` + regra devolvida aos agentes | baseline da Helena = 0; lista do SDK do runtime fechada |
| 3 | Pacote de comportamento + validador + simulador como portão | uma mudança de texto vai a dev sem build |
| 4 | Imagem e serviço da Helena + trilhas de CI/CD | mudança de lógica da Helena implanta só o serviço dela |
| 5 | Baseline zerada para todos os agentes | `fronteira_agentes_baseline.txt` vazio |

## Consequências

**Positivas:** metade das mudanças da Helena deixa de tocar a plataforma (fase 2); texto, roteamento e modelo mudam
sem build e voltam em segundos (fase 3); o receptor do WhatsApp, o ponto mais crítico, para de mudar a cada ajuste
da Helena (fase 4); o acoplamento não volta, porque a catraca reprova.

**Negativas (aceitas):** um salto de rede de milissegundos num turno com mediana de cerca de 6 s (fase 4); mais um
serviço para operar; o contrato precisa de versionamento e de teste nos dois lados; os testes que leem código-fonte
como texto ou fixam prompt por hash quebram ao mover código sem que haja bug — a triagem deles anda junto com as
fases 2 e 3.

**O que não muda:** ADR-0006/0017 (zonas de dados), ADR-0012 (alarme pela tabela, nunca pelo modelo), ADR-0062
(número único) e ADR-0066 (as quatro camadas da Helena).

## Supersedes

—

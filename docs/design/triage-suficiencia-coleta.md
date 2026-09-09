# Coleta de sintomas e a tabela de suficiência — passo 4 do fluxo de triagem

**Status: DRAFT — proposta de engenharia, 09/09/2026. Nada aqui está ratificado nem implantado.**

| Peça | Estado | Quem fecha |
|---|---|---|
| Nó `collect` no grafo da Helena | **código em `main`, desligado** (`coleta_enabled=False`) | engenharia (feito) |
| Tabela `triage_sufficiency` | **proposta abaixo**, não existe como `.dmn` | médico auditor ratifica; então vira `spec/processes/dmn/triage_sufficiency.dmn` |
| Ligar a coleta na composição do receptor | não feito | decisão do dono, **depois** da tabela no motor |

## O defeito, medido

Em 09/09/2026, no motor de dev, nas quatro tabelas `triage_redflag_*`:

```
sintoma NAO mapeado + intensidade "desconhecida" -> red_flag=false, CONTINUE   (4 de 4 tabelas)
sintoma NAO mapeado + intensidade "grave"        -> red_flag=true,  ESCALATE_NURSE (fail-safe)
```

"Estou com dor de cabeça" produz `sintoma_codigo=None` (nada casa na allowlist) e `intensidade`
não dita vira `"desconhecida"` (`agents/helena/graph.py`, montagem da extração). O fail-safe das
tabelas exige `grave`; o catch-all devolve `false`; `_rota_informativa` libera a resposta automática.
A tabela respondeu com confiança sobre dados que não tinha.

**Por que não mexer na tabela de red flag.** `tests/unit/spec/test_triage_redflag_shadow_candidates.py::
test_an_explicitly_unknown_intensity_never_raises_a_red_flag` fixa que `desconhecida` **nunca** levanta
bandeira. É decisão deliberada e correta: intensidade não dita não é grave nem leve — é desconhecida,
e com o desconhecido a única ação honesta é **perguntar**.

## O desenho: perguntar depois da red flag, nunca antes

O documento do diretor colocava a coleta **antes** da tabela. Aqui ela entra **depois**, e o motivo
é segurança: uma mensagem que já casa um código de emergência ("dor forte no peito") não pode esperar
uma pergunta sobre intensidade. Ordem implementada em `classify`:

1. extração e validação (inalteradas);
2. risco psicossocial → escala (inalterado);
3. `intent == "symptom"` → **DMN de red flag sempre** (inalterado); `red_flag=true` → escala;
4. **novo**: `red_flag=false` e `coleta_enabled` → `triage_sufficiency` decide entre
   `SUFICIENTE` (segue para `inform` como antes), `PERGUNTAR_*` (nó `collect`) ou `ESCALAR`;
5. `coleta_enabled=False` → passo 4 não existe e o grafo é o de antes, byte a byte.

Três regras que o **código** impõe, independentes do conteúdo da tabela:

- **Falha para o lado seguro.** Só pergunta quando a red flag já disse `false`. Tabela indisponível,
  sem linha casada ou veredito fora do vocabulário → `falha_tecnica` → humano.
- **Tem fim.** `COLETA_MAX_RODADAS = 2`. Na terceira mensagem sem dado suficiente, escala com
  `MOTIVO_COLETA_ESGOTADA` **sem consultar a tabela** — a regra mora no código porque não pode depender
  de a tabela lembrar dela.
- **Não pergunta o que já sabe.** A tabela recebe o que **está** no estado; campo presente não vira
  pergunta.

### Memória entre turnos

`receive` zera todo campo de saída a cada turno (defesa contra valor plantado). A coleta precisa de
três campos que sobrevivem: `coleta_rodadas`, `coleta_pendente`, `coleta_contexto`. A exceção é nominal
(`_HELENA_MEMORIA_DE_CONVERSA`) e segura: o pior que um valor plantado consegue é **escalar mais
cedo**. `coleta_rodadas` é saturado em `COLETA_MAX_RODADAS` no `receive`.

Com pergunta em aberto, `_classify_llm` envia as mensagens anteriores **junto** com a atual, no mesmo
bloco não confiável — "forte" só significa algo ao lado de "dor de cabeça".

## A tabela proposta — `triage_sufficiency`

**Isto é proposta de engenharia sobre a forma. O conteúdo é do médico auditor.** Não invento limiar
clínico: as linhas abaixo só dizem "falta dado → pergunte pelo dado", e a única decisão de mérito
(o que fazer quando esgota) está marcada como aberta.

Entradas:

| campo | tipo | de onde vem |
|---|---|---|
| `intent` | string | extração |
| `population` | string | extração (`adult`, `pediatric`, `gestante`, `mental_health`, `none`) |
| `sintoma_reconhecido` | boolean | `sintoma_codigo is not None` |
| `intensidade_informada` | boolean | `intensidade ∈ {leve, moderada, grave}` |
| `campo_populacao_disponivel` | boolean | idade / meses / semanas / risco_imediato presente, conforme a população |
| `rodadas` | integer | perguntas já feitas nesta conversa |

Saída: `veredito` ∈ `SUFICIENTE`, `PERGUNTAR_CARACTERIZACAO`, `PERGUNTAR_INTENSIDADE`,
`PERGUNTAR_IDADE`, `PERGUNTAR_IDADE_GESTACIONAL`, `ESCALAR`. Hit policy `FIRST`.

Regras propostas (ordem importa):

| # | intent | population | sintoma_reconhecido | intensidade_informada | campo_populacao | rodadas | veredito | leitura |
|---|---|---|---|---|---|---|---|---|
| 1 | symptom | - | - | - | - | `>= 2` | `ESCALAR` | tem fim (redundante com o código, de propósito) |
| 2 | symptom | - | `false` | - | - | - | `PERGUNTAR_CARACTERIZACAO` | o sintoma não casou com nenhum código: nunca adivinhar |
| 3 | symptom | - | `true` | `false` | - | - | `PERGUNTAR_INTENSIDADE` | várias regras de red flag dependem de intensidade |
| 4 | symptom | gestante | `true` | `true` | `false` | - | `PERGUNTAR_IDADE_GESTACIONAL` | cinco de oito regras gestantes dependem de IG |
| 5 | symptom | pediatric | `true` | `true` | `false` | - | `PERGUNTAR_IDADE` | febre `< 3 meses` é P1 |
| 6 | symptom | adult | `true` | `true` | `false` | - | `SUFICIENTE` | só `febre >= 65` usa idade; **ABERTO ao médico** se vale perguntar |
| 7 | - | - | - | - | - | - | `SUFICIENTE` | catch-all: intents não-sintoma nunca chegam aqui, mas a tabela não pode ficar sem linha |

### Decisões que NÃO são de engenharia (para a ratificação)

1. **Para onde escalar quando esgota.** O documento do diretor diz "como pedido de humano"
   (`solicitacao_humano` → P3 / `atendimentoHumano` / ack 4 h). Alternativa: `intencao_clinica`
   (P2 / `enfermagemTriagem` / ack 30 min). Um sintoma que não se conseguiu caracterizar em duas
   rodadas talvez mereça enfermagem, não atendimento. Implementado o que o documento pediu
   (`MOTIVO_COLETA_ESGOTADA`), trocar é uma constante.
2. **Regra 6.** Adulto com sintoma reconhecido e intensidade dita, sem idade: perguntar a idade
   sempre (custa uma rodada) ou só quando o sintoma for `febre`? Isto é conteúdo clínico.
3. **Duas rodadas.** `COLETA_MAX_RODADAS = 2` veio do documento. Um beneficiário com dor no peito não
   pode ficar num interrogatório; um com "não estou bem" talvez precise de três perguntas.
4. **O texto das perguntas.** `coleta_prompt` e `_PERGUNTA_FALLBACK` são rascunho. O documento é
   explícito: se a pergunta é a certa e em linguagem que o beneficiário entende, **é gente que julga**.

## O que ainda falta para a coleta existir de verdade

1. Médico auditor ratifica a tabela → `spec/processes/dmn/triage_sufficiency.dmn` (deploy pelo caminho
   normal, `engine_deploy`).
2. Composição do receptor passa `coleta_enabled=True` em `build(config)` — deliberadamente **não** há
   variável de ambiente lida dentro de `build()`: ligar coleta é ato de quem monta o runtime.
3. Passo 5 do fluxo (a conversa) — roteiro de mensagens vagas para pessoa percorrer. Não é teste de
   máquina.

## Evidência

- Lacuna medida nas quatro tabelas do motor em 09/09/2026 (bateria de evidência, passos 6 e 7).
- Testes: `tests/unit/agents/test_helena_coleta.py` — os três estados, o fim, a red flag antes da
  pergunta, o fail-closed e a memória entre turnos.

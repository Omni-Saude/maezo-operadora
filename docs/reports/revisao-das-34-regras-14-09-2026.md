# As 34 regras de triagem, lidas uma a uma

**Frente 4 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`.** Material para quem vai assinar, produzido em
14/09/2026 a partir das quatro tabelas em `spec/processes/dmn/triage_redflag_*.dmn`.

**O que este documento é e o que não é.** É o levantamento que a revisão precisa ter na mão: cada
regra, o que ela faz, e as perguntas que ela levanta. **Não é a assinatura** — nenhum agente cria o
signoff em nome de alguém, e a repo é explícita nisso (`docs/sme-dispatch/medico-auditor/
PACKAGE.md`). Quem assina preenche `SP-OP-ESCALATION-001.signoff.yaml`, e a cerca
`test_helena_dono_declarado.py` passa a exigir que o dono clínico declarado seja essa mesma pessoa.

As três perguntas de cada regra, do próprio documento: a conduta é a certa? Existe caso grave que
ela deixa passar? Existe caso banal que ela vira emergência?

---

# Primeiro: três padrões que valem mais que qualquer regra isolada

Ler as 34 uma a uma mostra três propriedades **estruturais**. Elas não estão em nenhuma linha
específica; estão no formato das quatro tabelas, e por isso são o que merece a primeira hora da
revisão.

## Padrão 1 — a rede de segurança só pega `grave`. `moderada` é o buraco.

As quatro tabelas terminam igual: uma regra genérica que escala **se a intensidade for `grave`**, e
um catch-all que não escala nada.

| Tabela | Rede de segurança | Último recurso |
|---|---|---|
| adulto | r10: qualquer sintoma + `grave` → P2 | r11: sem bandeira |
| pediátrica | r7: qualquer sintoma + `grave` → P2 | r8: sem bandeira |
| gestante | r7: qualquer sintoma + `grave` → P2 | r8: sem bandeira |
| saúde mental | r6: qualquer relato + `grave` → P2 | r7: sem bandeira |

**Consequência:** todo sintoma com intensidade `moderada` que não casar uma regra específica sai
**sem bandeira nenhuma**. Não é o caso leve que escapa — é o intermediário.

Três exemplos que caem exatamente aí, e todos são clinicamente discutíveis:

- **reação alérgica moderada num adulto.** A r6 exige `grave`. Uma reação moderada não casa a r6,
  não casa a r10, e sai sem bandeira. Anafilaxia progride.
- **dor abdominal moderada.** A r9 exige `grave`.
- **sangramento ativo leve.** A r5 exige `grave` ou `moderada`.

**Pergunta para quem assina:** a rede de segurança deveria pegar `moderada` também? Trocar
`"grave"` por `"grave","moderada"` nas quatro regras de rede é uma linha por tabela, e desloca o
custo para o falso positivo — mais escalonamento P2 de caso banal.

## Padrão 2 — na gestante, quatro dos cinco P1 dependem de um dado que a pessoa não costuma dar

| Regra | Condição de idade gestacional |
|---|---|
| r1 sangramento vaginal | **nenhuma** — sempre escala |
| r2 cefaleia com alteração visual | `>= 20` semanas |
| r3 movimentos fetais reduzidos | `>= 26` semanas |
| r4 perda de líquido | `< 37` semanas |
| r5 contrações regulares | `< 37` semanas |

O campo vem do texto da mensagem. **Quando ele não vem, a comparação é falsa e a regra não
dispara** — a gestante cai na rede de segurança (só se disser `grave`) ou no vazio.

Na prática: *"estou perdendo líquido"* sem dizer de quantas semanas **não escala**. O mesmo vale
para movimentos fetais reduzidos e para cefaleia com alteração visual.

**Pergunta para quem assina:** essas quatro regras deveriam disparar também quando a idade
gestacional é desconhecida? A alternativa conservadora é `-` na coluna (sempre) mais uma regra
posterior que refina por semanas. **É a mudança de maior impacto clínico deste documento.**

## Padrão 3 — a mesma dependência existe na pediatria, num único ponto crítico

A r1 pediátrica — febre em lactente com menos de 3 meses, emergência — depende de `idade_meses`.
Sem a idade, não dispara: a criança cai na rede de segurança.

E a fronteira é dura: 2 meses e 29 dias é emergência; 3 meses e 1 dia não é nada, a menos que o
relato diga `grave`. A bateria de 13/09 confirmou que a regra **funciona** (bebê de 2 meses com
febre deu P1; criança de 3 anos com febre não deu bandeira). A pergunta não é se funciona — é se
o degrau deveria ser um degrau.

**Isto conversa com a Frente 2:** a idade costuma aparecer num turno e a febre no seguinte, e hoje
a Helena esquece entre turnos. Enquanto a memória não existir, esta regra depende de a pessoa
repetir a idade na mesma frase do sintoma.

---

# Tabela do adulto — 11 regras

| | Regra | Conduta | Observação para a revisão |
|---|---|---|---|
| r1 | dor torácica, **qualquer** intensidade | P1 emergência | Conservadora por desenho. Contrasta com r2 e é o contraste que o documento pede para confirmar |
| r2 | dispneia `grave`/`moderada` | P1 emergência | **Dispneia leve não escala** (lacuna C2 da bateria). Numa pessoa com histórico cardíaco ou respiratório pode ser início de descompensação — e a Helena não conhece histórico de ninguém |
| r3 | déficit neurológico | P1 emergência | Tempo-dependente, sem gate. Correto |
| r4 | cefaleia súbita intensa | P1 emergência | A regra está certa. **O problema medido em 13/09 foi de EXTRAÇÃO**: "dor de cabeça" virou este código e abriu P1 de 5 minutos. É a Frente 3, não esta tabela |
| r5 | sangramento ativo `grave`/`moderada` | P1 emergência | Leve não escala — ver padrão 1 |
| r6 | reação alérgica **só `grave`** | P1 emergência | **Moderada não escala em regra nenhuma.** É o caso mais discutível da tabela: anafilaxia progride |
| r7 | síncope, qualquer intensidade | P2 enfermagem | Síncope em adulto pode ser cardíaca. P2 em vez de P1 é decisão clínica explícita — confirmar |
| r8 | febre `grave`/`moderada` **e** ≥ 65 anos | P2 enfermagem | Degrau duro em 65. Um adulto de 64 com febre moderada não escala. E depende de a idade estar na mensagem |
| r9 | dor abdominal **só `grave`** | P2 enfermagem | Moderada não escala — padrão 1 |
| r10 | qualquer sintoma + `grave` | P2 enfermagem | A rede de segurança. Ver padrão 1 |
| r11 | catch-all | sem bandeira | — |

---

# Tabela pediátrica — 8 regras

| | Regra | Conduta | Observação |
|---|---|---|---|
| r1 | febre **e** < 3 meses | P1 emergência | Ver padrão 3. Depende de `idade_meses` presente |
| r2 | dificuldade respiratória | P1 emergência | Sem gate de idade nem de intensidade. Correto |
| r3 | petéquias com febre | P1 emergência | Suspeita meningocócica. Sem gate. Correto |
| r4 | convulsão | P1 emergência | Sem gate. Correto |
| r5 | letargia | P1 emergência | Sem gate. Correto |
| r6 | desidratação `grave`/`moderada` | P2 enfermagem | Desidratação leve em criança pequena progride rápido — vale confirmar se `leve` deveria entrar |
| r7 | qualquer sintoma + `grave` | P2 enfermagem | Rede de segurança |
| r8 | catch-all | sem bandeira | — |

**Ausência a confirmar:** não há código nem regra para **vômitos persistentes** ou **recusa
alimentar** isolados — a lista pediátrica tem `sinais_desidratacao`, que os pressupõe já
instalados. Uma criança que ainda não desidratou não casa nada.

---

# Tabela da gestante — 8 regras

| | Regra | Conduta | Observação |
|---|---|---|---|
| r1 | sangramento vaginal | P1 emergência | Único P1 sem gate de semanas. Correto |
| r2 | cefaleia com alteração visual **e** ≥ 20 sem | P1 emergência | Padrão 2 |
| r3 | movimentos fetais reduzidos **e** ≥ 26 sem | P1 emergência | Padrão 2 |
| r4 | perda de líquido **e** < 37 sem | P1 emergência | Padrão 2. E acima de 37 semanas a perda de líquido não escala — é termo, mas ainda é trabalho de parto |
| r5 | contrações regulares **e** < 37 sem | P1 emergência | Padrão 2. Mesma observação sobre o termo |
| r6 | febre `grave`/`moderada` | P2 enfermagem | — |
| r7 | qualquer sintoma + `grave` | P2 enfermagem | Rede de segurança |
| r8 | catch-all | sem bandeira | — |

**Lacuna C1, confirmada:** **não existe código respiratório na lista da gestante.** Adulto tem
`dispneia`, pediátrica tem `dificuldade_respiratoria`, gestante não tem nenhum. Falta de ar numa
gestante produz código nulo e só escala se ela disser que é grave.

Se a decisão for incluir: **o código entra na lista e a regra entra na tabela na mesma entrega** —
existe teste que verifica a identidade entre as duas (`test_helena_codigos_casam_com_a_dmn.py`,
criado em 13/09). E a **posição importa**: a política é primeira-linha-que-casa, então uma regra de
dispneia depois da r7 nunca dispararia para intensidade grave.

---

# Tabela de saúde mental — 7 regras

| | Regra | Conduta | Observação |
|---|---|---|---|
| r1 | ideação suicida **e** risco imediato | P1 emergência | — |
| r2 | ideação suicida | P1 emergência | **A r1 é redundante**: r2 produz o mesmo P1 emergência sem o gate. Com política primeira-linha, a r1 só muda o texto do `motivo`. Não é defeito; é limpeza a confirmar |
| r3 | autolesão | P1 emergência | — |
| r4 | agitação/agressividade ou surto psicótico | P1 emergência | — |
| r5 | crise de ansiedade ou pânico | P2 enfermagem | — |
| r6 | qualquer relato + `grave` | P2 enfermagem | Rede de segurança |
| r7 | catch-all | sem bandeira | — |

**O que está bem resolvido aqui:** o campo `risco_imediato` é instruído no prompt a ser `true` na
dúvida, e a r2 garante que ideação suicida escala mesmo com o campo ausente. É a única tabela em
que a ausência de dado **não** enfraquece a regra — vale como referência para as outras três.

---

# O que fazer com este documento

1. **Quem revisar percorre as quatro tabelas** com a página do Canal de Teste ao lado: escreve a
   mensagem, vê a regra disparar. As treze conversas de 13/09 já cobrem as quatro tabelas.
2. **Responde primeiro os três padrões**, porque cada um deles muda várias regras de uma vez.
3. **Decide as duas lacunas de conteúdo** (dispneia na gestante, dispneia leve no adulto).
4. **Assina** `docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml` com nome
   completo e data, troca o carimbo `CONTEUDO CLINICO: DRAFT` das quatro tabelas, e preenche
   `owners.clinico` em `spec/agents/helena/agent.yaml` com o mesmo nome — a cerca cobra que sejam
   o mesmo.

**Nenhuma das mudanças propostas foi aplicada.** As tabelas estão exatamente como estavam; este
documento é leitura, não alteração.

# Melhorar com o uso — de conversa real a caso de teste

**Frente 8 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`.** Cobre os três passos: a conversa vira caso
(8.1), a cerca de saída alimenta o conjunto (8.2) e a cadência de revisão (8.3).

## O defeito

Não havia caminho nenhum pelo qual uma conversa do ambiente virasse caso de teste. **Cada defeito
das duas semanas de setembro foi achado por alguém testando à mão, lendo a tela** — o bebê triado
como adulto, a promessa de contato humano com zero processos abertos, a negativa clínica, o P1
aberto por dor de cabeça comum. Cada um custou horas de leitura humana e, depois de corrigido,
mais trabalho manual para virar teste.

O resultado é um sistema que fica igual até alguém reclamar.

---

## 8.1 · A conversa vira caso

```
python -m maezo.tools.conversa_para_caso transcricao.json \
  --origem bateria-20-09 --saida casos-novos.json
```

Um comando, como o documento exige — *"não um procedimento manual, senão ninguém faz"*.

Ele faz o trabalho chato: extrai os turnos do beneficiário, **pseudonimiza** (a mesma rede que
protege o `resumo_contexto` no start de processo, não uma cópia), formata no formato do corpus, e
**deduplica contra o corpus existente** — rodar duas vezes sobre a mesma bateria dobraria o peso
daquelas mensagens na nota sem ninguém perceber.

### A decisão que parece desperdício e não é

**O rótulo nasce vazio.** A extração observada está ali, bastaria copiá-la — e é exatamente o que
o comando **não** faz.

A extração observada é **o que pode estar errado**. Se o comando rotulasse o caso com a saída de
produção, o corpus passaria a afirmar que *"dor de cabeça"* **é** `cefaleia_subita_intensa`, porque
foi isso que o modelo fez naquele dia. A medição daria nota alta para o defeito.

> Um conjunto de testes gerado a partir do comportamento observado não mede correção: **mede
> estabilidade**, e abençoa o erro no dia em que ele acontece.

A extração observada vai junto, num campo separado (`observado`). A **diferença** entre ela e o
rótulo é o que se leva para a conversa com quem assina a régua.

E o caso gerado carrega `precisa_de_rotulo: true` com `esperado` em branco — o que faz a cerca do
corpus **reprovar** um caso colado sem rótulo, em vez de deixá-lo entrar mudo na medição.

---

## 8.2 · A cerca de saída alimenta o conjunto

Toda vez que a cerca de saída recusa um texto, houve uma frase que o modelo **tentou** dizer ao
beneficiário e não podia. Isso é um caso, e é dos melhores que existem: veio de tráfego real, num
ponto em que o sistema já sabia que estava errado.

**O caminho, que é o mesmo do 8.1 com uma entrada diferente:**

1. o contador `maezo_agent_resposta_recusada_total{motivo,response_kind}` diz **quantas** e **de
   que grupo** — é o gatilho, não a evidência;
2. o log de recusa (`helena_resposta_recusada`) carrega o `conversation_id` pseudonimizado e o
   grupo; a conversa se recupera por ele;
3. a transcrição entra no comando acima;
4. **o texto recusado vira teste de unidade da cerca**, em `test_helena_recusa_de_saida.py`, com o
   padrão real — antes da correção, para a cerca ser vista pegando o defeito.

> **A leitura que engana, e ela já enganou:** em 13/09, com o `response-v4` no ar, o contador ficou
> em **zero** nos cinco turnos da conversa pediátrica. Zero **não prova** que a cerca funciona —
> prova que ela não teve o que barrar naquele período. Quem prova são os testes de unidade. Zero
> sustentado só é interpretável junto do volume de turnos.

---

## 8.3 · A cadência, que é uma cerca e não um documento

O documento pede: *"Registrar a cadência, **não confiar na memória de ninguém**."*

Uma cadência que vive só numa página é uma cadência que ninguém honra. Este projeto já tem quatro
cascas vazias nomeadas pelo próprio diretor, e *"revisamos a cada seis meses"* é o formato exato de
uma quinta.

Então ela vive em **`spec/revisao-das-reguas.yaml`**, com data, e
`scripts/ci/check_revisao_das_reguas.py` deixa a esteira **vermelha** no dia em que a data passa —
a mesma disciplina que `check_deviation_expiry.py` já aplica.

| Régua | Dono | Revisar até | Ou após |
|---|---|---|---|
| Extração (as 5 regras) | médico auditor | 2026-12-15 | 1.000 conversas |
| As 34 regras clínicas | médico auditor | 2026-12-15 | 1.000 conversas |

**Duas condições, "o que vier primeiro"**, porque uma régua envelhece de dois jeitos: por **tempo**
(a prática clínica muda, o modelo é trocado) e por **volume** (mil conversas expõem casos que cem
não expunham). Só o calendário deixaria um canal que triplicou rodando com a régua de quando era
pequeno.

**Não há renovação silenciosa.** A saída é um PR que registra a revisão feita — atualizando
`ultima_revisao` e `revisar_ate` — ou declara por que ela não aconteceu. Empurrar a data sozinha é
a normalização do alarme com passos extras, e a mensagem da cerca diz isso na cara de quem a vir.

> **O limite honesto:** a condição de **volume** não é checada pela cerca. O contador de conversas
> vive no workspace de métricas (Frente 5), que o script não alcança — e inventar uma leitura seria
> pior que declarar o limite. Ela está no YAML para a revisão humana conferir, e vira cerca no dia
> em que o coletor estiver no ar com histórico.

---

## O ciclo inteiro, numa frase

Conversa real → comando → caso **sem rótulo** → **pessoa** rotula contra a régua → corpus →
medição ao vivo (Frente 3.3) → a nota cai quando a tradução piora. E a cadência força a régua a ser
relida antes de envelhecer.

**O elo humano é o rótulo, e é deliberado.** Tudo em volta dele foi automatizado justamente para
que a única coisa que exige julgamento clínico seja a única coisa que exige uma pessoa.

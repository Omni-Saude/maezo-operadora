# `/receptor/simular` precisa devolver duas coisas

**Destino no repositório:** junto de `docs/runbooks/publicar-pagina-canal-teste.md`
**Origem:** teste do diretor, 12/09/2026.

Entrega de duas peças:

1. **`escalonamento.html` reescrita como conversa** — pronta, vai para
   `src/maezo/platform/testchannel/paginas/escalonamento.html`.
2. **Dois campos no retorno de `/receptor/simular`** — descritos aqui. É a única coisa a construir.

A página **funciona hoje sem isso** e degrada dizendo o que não recebeu. Com os dois campos, ela
acende sozinha — nenhum ajuste adicional do lado dela.

---

## O que o diretor perguntou

> *"Por que a Helena nessa página não está interagindo como se fosse um bot normal de troca de
> mensagens antes de partir pra um sintoma?"*

Parte da resposta é a Helena — ela é de um turno só, e os estados 1 e 2 da jornada não existem ou
estão desligados. **Mas parte era a página, e essa parte é nossa.**

A página nunca mostrou o que a Helena respondeu. O texto foi redigido, entregue ao envio do
WhatsApp e morreu num 401 de credencial de preenchimento. **Nenhuma resposta da Helena foi lida
por ninguém até hoje** — nem para avaliar se o texto presta, nem para conferir se ele vaza
orientação clínica, que é a cerca `leak_canaries` dos conjuntos golden.

A página nova tem a conversa na tela. Falta ela receber o que mostrar.

---

## Campo 1 — `resposta`

O texto que a Helena redigiu neste turno.

**Onde ele está.** `HelenaDispatcher.dispatch` executa o grafo até o fim e o rascunho vai para o
`_ScopedWhatsAppSender`. O `dispatch` já tem o estado final em mãos quando decide enviar.

**O que fazer.** O receptor devolve o texto no corpo de `/webhook`, e o Canal repassa. Se o envio
ao WhatsApp falhar (é o caso em dev: 401), **devolva o texto mesmo assim** — a falha é da última
milha, o turno aconteceu e o texto existe.

```json
{"status": "ok", "dispatched": 1, "resposta": "Olá! Sou a Helena…"}
```

**PHI.** O texto é conteúdo voltado ao beneficiário, redigido por um agente proibido de dar
orientação clínica, sobre uma mensagem já pseudonimizada. Ele não carrega identificador — mas
confiram contra `platform/privacy/key_scrubber.py` antes de decidir, e se houver dúvida devolvam
só em dev, sob o mesmo portão `CANAL_SIMULAR_RECEPTOR` que já cerca a rota.

---

## Campo 2 — `conversation_id`

O identificador da conversa que o receptor calculou para este telefone.

**Por que.** A chave do processo é `ESC-{tenant}-{conversation_id}`. A página não tem como
derivá-la: o `conversation_id` é `wa:{tenant}:{phone_hash}` e o `phone_hash` é HMAC com a
`PHI_HMAC_KEY`, que — corretamente — não sai do container.

**O que isso conserta.** Hoje, quando nenhum processo novo aparece, a página não distingue duas
situações:

- a Helena resolveu sozinha (esperado para saudação e dúvida administrativa)
- o turno se juntou a um escalonamento que a conversa já tinha

Sem o identificador, a página só consegue contar **todos** os escalonamentos abertos no motor — e
como sempre há alguns de baterias anteriores, ela nunca dá leitura firme. **Uma resolução
automática correta aparece como ambígua.** Foi exatamente o que aconteceu no teste de hoje: o
diretor mandou "Alou", a Helena resolveu sozinha (`desfecho=resolvido_automatico`, `route=inform`,
11:33) e a tela disse *"este número já tinha 9 casos abertos"* — frase falsa, porque os 9 eram do
motor inteiro.

Com o campo, a página consulta `businessKey=ESC-amh-{conversation_id}` e responde sem ambiguidade.
O código já está escrito e inativo, esperando o valor.

```json
{"status": "ok", "dispatched": 1,
 "conversation_id": "wa:amh:hk1_bc7fad5d…",
 "resposta": "Olá! Sou a Helena…"}
```

**O `conversation_id` é seguro de devolver:** é keyed-irreversível por desenho, e o próprio
`dispatch.py` documenta que ele já aparece em log e no Cockpit.

---

## Aceite

Com a rota devolvendo os dois campos, na página:

| Mensagem | O que deve aparecer |
|---|---|
| "oi" | Balão da Helena com o texto dela; passo "escalou?" = **NÃO ESCALOU**, leitura firme |
| "dor muito forte no peito e falta de ar" | Balão da Helena; **ESCALOU**, P1, `plantao-clinico`, 5 e 30 min |
| Segunda mensagem no mesmo número, com caso aberto | **SEM PROCESSO NOVO** dizendo que o turno se juntou ao caso existente — sem a ressalva de ambiguidade |

---

## O que mudou na página, para vocês conferirem no diff

1. **Virou conversa.** Fio de mensagens na esquerda, evidência do processo na direita. O balão da
   Helena aparece em amarelo tracejado, dizendo que ela respondeu e o texto não chega, enquanto
   os campos acima não existirem. **Nunca inventa um texto.**

2. **O número parou de avançar sozinho.** Avançar a cada envio impedia conversa de vários turnos —
   que é o que se quer testar quando a coleta for ligada. Agora o número fica e há um botão
   "Nova conversa".

3. **A contagem parou de mentir.** O texto agora diz que o número é do motor inteiro e não daquele
   telefone, e que a ambiguidade some quando a rota devolver o identificador.

4. **A correção da data continua** (`dataParaMotor`, fuso `+0000`). Sem ela o motor devolve 400 e
   a tela morre na primeira consulta depois do envio.

---

## Uma observação que não bloqueia nada

Mesmo com os dois campos, a Helena continua de **um turno só**: uma mensagem, uma resposta, fim.
Uma segunda mensagem começa do zero.

O que faz dela um bot de verdade são os estados 1 e 2 da jornada — o de coleta existe e está
desligado esperando `triage_sufficiency` ser ratificada; o de saudação e identificação depende do
elo telefone → beneficiário, que não existe em nenhuma das duas repos.

Esta entrega não substitui nenhum dos dois. Ela faz a conversa **aparecer**, que é condição para
avaliar os dois quando chegarem.

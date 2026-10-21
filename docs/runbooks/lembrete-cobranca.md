# Lembrete proativo de cobrança (dev)

Até aqui o Lucas só **reage** quando o beneficiário escreve. O lembrete é a primeira mensagem que o
sistema manda **sem ser chamado**: poucos dias antes do vencimento, "Sua mensalidade de outubro de 2026
(R$ 320,25) vence em 16/10." É um job agendado, `python -m maezo.platform.lembretes`
(`src/maezo/platform/lembretes/`), que **nasce desligado, em dry-run e preso a uma lista de conversas**.

## O texto (fixo, guia de tom)

```
Sua mensalidade de <mês> de <ano> (R$ <valor>) vence em <dd/mm>. Para a 2ª via, acesse o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento. Para não receber lembretes, responda PARAR.
```

Sem nome de agente, mês sempre com ano, nenhuma frase acima de 20 palavras, nenhum emoji. A resposta ao
PARAR é "Você não receberá mais lembretes." Nada é gerado por modelo: os três fatos (mês e ano, valor,
vencimento) vêm da fonte de cobrança do Lucas (`FonteCobrancaAmh`, os mesmos fatos de valor da resposta
ao beneficiário, mesma base legal), e o texto é uma constante (`texto.py`).

## Quem recebe (todas as condições)

1. Acesso **verificado** e **consentimento** em vigor (`conversa_acesso_beneficiario`, DL-0083): estado
   `verificado`, consentimento dado e gravado na AMH (`consent_ref`), não revogado (REVOGAR tira a pessoa),
   nada pendente de gravação no lago.
2. O pagador é **a própria pessoa**: `pagador_tipo = pessoa_fisica`. **Nunca** `pessoa_juridica`, coletivo
   por adesão (cobrança da administradora) nem `desconhecido`.
3. Há competência `em_aberto` (não paga, não vencida, não cancelada) com vencimento de hoje até hoje + N
   dias (`MAEZO_LEMBRETE_DIAS_ANTECEDENCIA`, padrão 3).
4. Ainda **não** foi lembrada daquela competência (`lembrete_cobranca_enviado`, uma por conversa e
   competência; reserva antes do envio, liberada se o envio falha).
5. Não pediu PARAR (`lembrete_cobranca_optout`) e não está no registro de supressão SP-OP-SUPP-001
   (`gateway/vendor_suppression.py`). Fonte de supressão fora do ar = **nada é enviado**.
6. Entre 08:00 e 20:00 em America/Sao_Paulo (só vale para envio real).
7. O telefone está na custódia cifrada (ADR-0061, 30 dias desde a última mensagem da pessoa). Sem
   telefone na custódia, sem envio.
8. Teto global de `MAEZO_LEMBRETE_LIMITE_POR_EXECUCAO` mensagens por rodada (padrão 50), com pausa de
   `MAEZO_LEMBRETE_PAUSA_ENTRE_ENVIOS_S` (padrão 1 s) entre elas.

Quem tem mais de uma competência no prazo recebe uma por rodada (a de vencimento mais próximo).

## Variáveis (todas seguras por padrão)

| Variável | Padrão | O que faz |
|---|---|---|
| `MAEZO_LEMBRETE_COBRANCA` | `false` | Interruptor geral. Desligado, o job sai antes de montar qualquer coisa. |
| `MAEZO_LEMBRETE_DRY_RUN` | `true` | Só calcula e loga **contagens**; não envia, não grava reserva. |
| `MAEZO_LEMBRETE_DIAS_ANTECEDENCIA` | `3` | Lembra quando o vencimento é em até N dias (1 a 15). |
| `MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS` | vazio | `conversation_id`s (`wa:amh:hk1_...`) separados por vírgula. |
| `MAEZO_LEMBRETE_ALLOWLIST_TELEFONES` | vazio | Telefones; viram conversa no boot (HMAC); nunca logados. |
| `MAEZO_LEMBRETE_EXIGIR_ALLOWLIST` | `true` | Envio real **sem** allowlist é recusado. Produção desliga em ato próprio. |
| `MAEZO_LEMBRETE_TEMPLATE_NAME` | vazio | Template aprovado na Meta, para fora da janela de 24 h. |
| `MAEZO_LEMBRETE_TEMPLATE_IDIOMA` | `pt_BR` | Idioma do template. |
| `MAEZO_LEMBRETE_LIMITE_POR_EXECUCAO` | `50` | Teto global por rodada. |
| `MAEZO_LEMBRETE_PAUSA_ENTRE_ENVIOS_S` | `1.0` | Espaço entre dois envios. |

**Allowlist definida = ninguém fora dela recebe nada.** Garantido em três lugares: a conversa fora da lista é
descartada antes de a cobrança dela ser lida; o `Enviador` recusa o destino fora da lista no momento do
envio (`DestinoForaDaAllowlistError`); e o simulador só aceita conversa da lista. Erro de digitação na
lista só **reduz** quem recebe (item inválido é descartado; lista definida e vazia = ninguém).

## Janela de 24 h e o template da Meta

- Conversa com mensagem da pessoa **nas últimas 24 h**: sai **texto livre** (o texto acima).
- Fora da janela: a Meta só aceita **template aprovado**. Com `MAEZO_LEMBRETE_TEMPLATE_NAME` configurado,
  sai o template, com o **mesmo texto**. Sem o nome, a pessoa é **pulada** (`fora_da_janela_sem_template`) e
  nada é reservado.

### Template a submeter no Meta Business Manager (WhatsApp Manager > Modelos de mensagem)

- **Nome:** `lembrete_mensalidade_v1` (o nome que for aprovado vai em `MAEZO_LEMBRETE_TEMPLATE_NAME`)
- **Categoria:** `Utility` (utilidade)  **Idioma:** Português (BR), `pt_BR`
- **Cabeçalho, rodapé e botões:** nenhum
- **Corpo (exato):**

```
Sua mensalidade de {{1}} (R$ {{2}}) vence em {{3}}. Para a 2ª via, acesse o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento. Para não receber lembretes, responda PARAR.
```

- **Variáveis e exemplos para a aprovação:**
  - `{{1}}` mês e ano da competência: `outubro de 2026`
  - `{{2}}` valor, vírgula decimal, sem "R$": `320,25`
  - `{{3}}` vencimento, `dd/mm`: `16/10`

No modo de teste, a variável 1 vai como `[TESTE] outubro de 2026` (o corpo aprovado não muda).

## Como ligar em dev (do mais seguro ao real)

Pré-requisitos do ambiente: acesso do beneficiário ligado (`MAEZO_ACESSO_BENEFICIARIO`), fonte `amh`,
`MAEZO_LUCAS_CONSULTA_VALORES` e a custódia do telefone (ADR-0061). A task herda o ambiente do receptor;
se algum faltar, o job **recusa montar** (`lembrete_cobranca_nao_montou`), nunca cai em outra fonte.

0. **Migration** `0027` (tabelas `lembrete_cobranca_enviado` e `lembrete_cobranca_optout`), pela task de
   migrations de sempre, **antes** de ligar o job.
1. **Terraform** (aplicado pelo time; o arquivo é `deploy/aws-ecs/envs/dev-sa-east-1/task-lembrete-cobranca.tf`):
   com `lembrete_cobranca = null` (padrão) nada existe. Para criar a task desligada:
   ```hcl
   lembrete_cobranca = {}   # habilitado=false, dry_run=true, schedule_enabled=false
   ```
2. **Dry-run** (conta, não envia). Rodar a task uma vez, ligando só o interruptor:
   ```
   AWS_PROFILE=amh-data-dev AWS_REGION=sa-east-1 aws ecs run-task --cluster <cluster> \
     --task-definition maezo-operadora-dev-lembrete-cobranca --launch-type FARGATE \
     --network-configuration '<subnets/sg das tasks>' \
     --overrides '{"containerOverrides":[{"name":"lembrete-cobranca","environment":[
        {"name":"MAEZO_LEMBRETE_COBRANCA","value":"true"}]}]}'
   ```
   No log (`/ecs/<name>/lembrete-cobranca`) aparece **uma linha** `lembrete_cobranca_resumo` só com
   contagens: `candidatos`, `enviaria`, `optout`, `suprimido`, `pagador_nao_e_pessoa_fisica`,
   `cobranca_da_administradora`, `sem_competencia_na_janela`, `ja_lembrado`, `fora_da_allowlist`,
   `fonte_indisponivel_*`. Nenhum telefone, CPF, valor ou id de conversa entra no log nem em métrica.
3. **Modo de teste do dono** (`--simular`). O dono é beneficiário de empresa (PJ), então nunca receberia um
   lembrete real. O simulador manda **um** `[TESTE] ...` com fatos **sintéticos** (competência do mês do
   vencimento, R$ 123,45, vencimento hoje + N) a **uma** conversa da allowlist; não lê a cobrança de
   ninguém, não grava reserva (pode repetir), respeita PARAR, a janela de 24 h/template e o horário:
   ```
   --overrides '{"containerOverrides":[{"name":"lembrete-cobranca",
     "command":["sh","-c","set -eu\n<mesmo export do DATABASE_URL>\nexec python -m maezo.platform.lembretes --simular --conversa wa:amh:hk1_XXXX [--ignorar-horario]"],
     "environment":[
       {"name":"MAEZO_LEMBRETE_COBRANCA","value":"true"},
       {"name":"MAEZO_LEMBRETE_DRY_RUN","value":"false"},
       {"name":"MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS","value":"wa:amh:hk1_XXXX"}]}]}'
   ```
   (Mais simples: deixar `lembrete_cobranca = { allowlist_conversas = "wa:amh:hk1_XXXX" }` no tfvars e
   mudar só `habilitado`/`dry_run` no override.) A conversa do dono é a do telefone dele no receptor; o
   `conversation_id` está em `conversa_acesso_beneficiario`. O telefone tem de estar na custódia, ou seja,
   ele precisa ter escrito para o número nos últimos 30 dias. Para repetir o teste depois de um PARAR:
   `python -m maezo.platform.lembretes --reativar --conversa wa:amh:hk1_XXXX` (só conversa da allowlist).
4. **Envio real, só para a allowlist:** `habilitado = true`, `dry_run = false`,
   `allowlist_conversas = "<as conversas>"` e, para ligar o agendamento, `schedule_enabled = true`
   (de hora em hora, 09h a 18h, America/Sao_Paulo; repetir é inofensivo).

## PARAR

`tratar_parar(texto, conversation_id=..., store=...)` (`platform/lembretes/optout.py`) reconhece a
mensagem inteira "parar" ou "parar lembretes" (sem acento nem pontuação), grava o opt-out e devolve
"Você não receberá mais lembretes.". **Falta ligar no receptor** (o job já respeita o opt-out). O gancho,
no despachante, antes de rotear para qualquer agente:

```python
resposta = await tratar_parar(message.text, conversation_id=conversation_id, store=self.lembrete_store)
if resposta is not None:
    ...enviar `resposta` pelo remetente do turno e encerrar o turno
```

`lembrete_store` é um `PostgresLembreteStore(dsn=..., tenant=...)`. REVOGAR (consentimento) continua sendo
do fluxo de acesso e vale mais: quem revogou sai da seleção.

## Como desligar

Em ordem de rapidez: `schedule_enabled = false` (para de agendar); `habilitado = false` (a task sai sem
fazer nada); `lembrete_cobranca = null` (remove tudo). Nenhuma mensagem já enviada é desfeita.

## O que não faz (limites conhecidos)

- Não consulta quem **não** tem acesso verificado: sem a referência pseudônima e o consentimento, não há
  como ler a cobrança, e o design exige os dois.
- O estado `verificado` fica até a próxima mensagem depois da validade da verificação (24 h): quem foi
  verificado e depois trocou de aparelho continua "verificado" até escrever de novo. O limite é a
  custódia do telefone (30 dias desde a última mensagem).
- Uma queda **entre** a reserva e o envio custa um lembrete a menos, nunca um a mais (reserva antes do
  envio, liberada na falha).
- A tabela de enviados e a de opt-out ainda não estão no plano de eliminação (`erasure-plan`) nem têm
  expurgo por retenção: só guardam `conversation_id` pseudônimo, competência e carimbo. Pendência para o
  DPO.

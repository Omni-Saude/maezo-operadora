# Pacote para a Segurança — classe `notificacao_interna_equipe` (DL-0150, 2026-10-20)

**Para quem:** área de Segurança (dona do bloco `seguranca` e, como responsável interina do desvio,
de `enforcement_padrao_nao_mapeado`). Médica e ANS assinam os blocos delas na mesma classe.
**Estado:** classe DECLARADA em sombra, **nada aprovado**. Este pacote não assina nada, não muda
`status`, `modo`, `enforcement_padrao_nao_mapeado`, o desvio nem `mapeamento_acoes`.
**Planilha de resposta:** `Seguranca_Decisoes_2026-10-20.xlsx` (pasta do projeto no OneDrive da DAFE),
uma linha por decisão, coluna "Resposta da Segurança" em branco.

## 1. O que muda

| Onde | Mudança |
|---|---|
| `spec/policies/autonomy/action-approvals.yaml` → `acoes:` | Classe nova `notificacao_interna_equipe`: `enforcement: shadow`, `dominios_exigidos: [medica, ans, seguranca]`, os três blocos `aprovado: false` / `PENDENTE`. Duas superfícies `choked: true`: os handlers `make_notify_team_handler` e `make_notify_supervisor_handler` de `src/maezo/tools/workers/escalation.py`. |
| mesmo arquivo → `mapeamento_topicos:` | `operadora.escalation.notify_team` e `operadora.escalation.notify_supervisor` → `notificacao_interna_equipe` (30 → 32 entradas). |
| mesmo arquivo → cabeçalho | Comentário de CODEOWNERS corrigido para os donos reais (`@rodaquino-OMNI @Omni-Saude/security-team`, igual à linha do arquivo em `.github/CODEOWNERS`). |
| `src/maezo/gateway/effect_classes.py` | `ActionClassSpec` novo em `ACTION_CLASSES`: degrau `C1` (notificação), forma de recusa `INCIDENTE_FALHA_FECHADA`. Catálogo passa de 16 para 17 classes. |
| Testes | Escada pinada em `tests/unit/gateway/test_effect_enforcement.py` (17 linhas); prova nova em `tests/unit/gateway/test_notificacao_interna_equipe.py`. |

**Efeito em execução hoje: nenhum.** O único efeito observável é a telemetria
`action_execution_gateway_shadow` desses dois tópicos: antes `action_class=NAO_MAPEADA`,
`reason=ACAO_NAO_MAPEADA`; agora `action_class=notificacao_interna_equipe`,
`reason=MANIFESTO_NAO_RATIFICADO`, ainda `WOULD_DENY` e `enforced=False`.

**O que a classe cobre.** Aviso INTERNO à equipe de atendimento (`ST_NotificarTime`) e ao supervisor
(`ST_NotificarSupervisor` e o fallback `ST_NotificarFallback`) no SP-OP-ESCALATION-001. Os handlers
publicam em `operadora.notifications.internal`; o aviso não sai da plataforma e não toca o
beneficiário. Por isso não foi posto em `comunicacao_beneficiario`.

**O que fica de fora: `operadora.events.publish`.** O tópico aparece em 17 BPMNs, com cargas de
natureza diferente (eventos de domínio, não só avisos a pessoas). Pôr esse tópico nesta classe daria à
assinatura um alcance que ela não descreve. Recomendamos uma classe própria, num PR separado.

## 2. Os quatro cenários (simulados contra cópias do manifesto)

| # | Configuração | `notify_team` / `notify_supervisor` | `operadora.events.publish` |
|---|---|---|---|
| 1 | **Hoje (sombra):** `status: DRAFT`, `modo: shadow` | classe nova, `WOULD_DENY`, `MANIFESTO_NAO_RATIFICADO`, **não bloqueia** | `ACAO_NAO_MAPEADA`, não bloqueia |
| 2 | **Só `modo: enforcing`** (classe e padrão não mapeado seguem `shadow`) | igual ao 1: **não bloqueia** (a classe está em `shadow`) | não bloqueia (padrão não mapeado em `shadow`) |
| 3 | **`modo: enforcing` + `enforcement_padrao_nao_mapeado: enforcing`** (ato terminal do desvio) | **não bloqueia**: a classe segue `shadow`. Sem este PR, os dois tópicos seriam `ACAO_NAO_MAPEADA` e **bloqueariam** (incidente, retries=0), e o aviso de escalonamento à equipe pararia | **BLOQUEIA** nos 17 BPMNs — por isso pedimos classe própria antes do prazo |
| 3b | o 3 + a classe virada para `enforcement: enforcing` sem aprovação | **BLOQUEIA** (`MANIFESTO_NAO_RATIFICADO` ou `APROVACAO_PENDENTE`): incidente fail-closed | bloqueia |
| 4 | **Aprovado:** `status: RATIFICADO` e os três blocos preenchidos | `WOULD_ALLOW`, `APROVADO` | segue `ACAO_NAO_MAPEADA` |

Consequência que a Segurança precisa ver, dita sem rodeio: ao mapear os dois tópicos para uma classe
em `shadow`, eles saem da rede do padrão não mapeado. No ato terminal (cenário 3) eles **não** passam a
bloquear sozinhos; isso só acontece quando a Segurança virar a própria classe para `enforcing`. É o
mesmo regime das outras 16 classes.

## 3. A decisão pedida

1. **Aceitar a classe** `notificacao_interna_equipe` como descrita (aviso interno, não toca o
   beneficiário), ou pedir outro recorte.
2. **Aceitar o degrau `C1`** (notificação; o rollout §9.4 vira C0 → C4).
3. **Aceitar a forma de recusa `INCIDENTE_FALHA_FECHADA`.** As superfícies são workers de external
   task: uma recusa enforçada vira recusa auditada + incidente (retries=0) pelo harness. A falha de
   publicação continua no fallback que o BPMN já modela (`ERR_ESC_NOTIFY_FAILED` →
   `BE_FalhaNotificacao`). A alternativa `ESCALONAMENTO_HUMANO` (a de `comunicacao_beneficiario`) não
   descreve o que o harness faz numa task de worker.
4. **`operadora.events.publish`:** manter fora (recomendação) e abrir classe própria; ou mapeá-lo aqui.
5. **Assinaturas:** os blocos `medica`, `ans` e `seguranca` da classe (`aprovado`, `aprovador`,
   `data`, `evidencia_ref`), preenchidos pelas próprias áreas em PR revisado. Nenhum agente preenche.

## 4. Prazo

O desvio de `enforcement_padrao_nao_mapeado` vence em **2027-02-09** (`deviation.expires`), e o
`scripts/ci/check_deviation_expiry.py` fica vermelho em todo PR a partir dessa data enquanto o valor
seguir `shadow`. Antes disso, cada tópico ainda não mapeado precisa de classe, ou bloqueia no ato
terminal. `operadora.events.publish` é o maior deles (17 BPMNs).

## 5. Como conferir

- `tests/unit/gateway/test_notificacao_interna_equipe.py`: os dois tópicos devolvem a classe nova,
  `WOULD_DENY`, `enforced=False`; `events.publish` segue `ACAO_NAO_MAPEADA`; aprovar a classe nova numa
  cópia forjada não aprova negativa, fraude, vínculo contratual nem autorização; tirar as duas linhas
  do mapa devolve os tópicos a `ACAO_NAO_MAPEADA`.
- `tests/unit/sec/test_action_execution_fence.py`: o arquivo segue `DRAFT`, toda referência existe, e
  continua sobrando tópico não mapeado.
- `python scripts/ci/check_effect_chokepoint_fence.py`: a escada fechada tem 17 classes.

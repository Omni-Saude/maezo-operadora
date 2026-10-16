# Runbook — bloqueio de verificação por CPF (D-04, decisão de 15/10/2026)

> **Deploy (ordem obrigatória).** A migration `0028_acesso_falhas_por_cpf` roda como **Job ANTES** da imagem
> nova. Sem a tabela, o passo de CPF **falha fechado**: o turno falha (a Meta reentrega) e ninguém verifica
> até a tabela existir. Nunca suba a imagem antes do Job.

**A regra.** 3 falhas de verificação por CPF em 6 horas, somadas entre **todas** as conversas. Passado o
limite, o CPF fica bloqueado até o fim da janela (6 h desde a primeira falha da janela) para qualquer
conversa, inclusive a do próprio dono do CPF. Cada bloqueio abre um atendimento humano
(SP-OP-ESCALATION-001, motivo `identidade_nao_verificada`).

**Por que existe.** O bloqueio de antes valia por conversa: quem tinha vários números tentava a data de
nascimento sem limite (9 datas erradas em 3 números e o 4º número entrou, red team de 13/10/2026).

## O que conta como falha, e o que não conta

| Conta | Não conta |
|---|---|
| CPF **válido** com a data de nascimento que não confere | Formato inválido ou CPF com dígito verificador errado (recusa local) |
| | A AMH fora do ar (a pessoa recebe "indisponível") |
| | Um sucesso **não zera** o contador (quem acerta o próprio documento não limpa as tentativas contra o de outra pessoa) |

A janela é **fixa**: abre na primeira falha e não anda com as seguintes. Depois dela, a contagem recomeça.

## O que o beneficiário vê

O mesmo texto fixo do bloqueio por tentativas ("Não consegui confirmar que é você. Vou passar seu atendimento
para alguém da nossa equipe."). O bloqueio por CPF aparece **antes** de pedir a data de nascimento.

## Como reconhecer no log

Transição `acesso_transicao` com `motivo=limite_por_cpf` (de `aguardando_cpf` ou `aguardando_nascimento` para
`bloqueado_humano`). O log não traz CPF nem hash.

**Alarme de re-bloqueio.** Quando o **mesmo** CPF é bloqueado em duas janelas seguidas (a segunda começa até
6 h depois do fim da primeira), sai o WARNING `acesso_cpf_bloqueio_reincidente` com `marca` (8 hex do começo
da chave derivada — correlaciona alarmes do mesmo CPF, não identifica ninguém) e sobe a métrica
`maezo_acesso_cpf_bloqueio_reincidente_total{tenant}`. Dispara uma vez por janela (na falha que bloqueia).
Sinal provável de alguém re-travando a verificação de outra pessoa: confira se o atendimento humano aberto
pelo bloqueio foi atendido e escale ao dono se a `marca` repetir.

## Custo aceito (risco registrado no DL-0109)

Alguém que erra a data de **outra pessoa** 3 vezes trava o CPF dela por até 6 h. Foi a escolha do dono: a
alternativa (limite só por conversa) deixava o acesso indevido sem teto.

**Risco aceito:** quem conhece o CPF de alguém pode **re-travar a verificação dessa pessoa indefinidamente**
— 3 datas erradas a cada 6 h, de qualquer número, inclusive pelo caminho de documento (telefone fora do
cadastro). Mitigação: cada bloqueio abre atendente, e o alarme de re-bloqueio acima mostra a repetição.

## Desligar o limite por CPF (sem reverter código)

`MAEZO_ACESSO_LIMITE_CPF=false` (default `true`) e reinício do receptor. Desligado, vale só o limite por
conversa (o comportamento de antes do D-04, que reabre o ataque de vários números) e o boot registra o
WARNING `acesso_limite_cpf_desligado`. Use só se o risco acima virar incidente, com decisão do dono.

## O que há no banco

Tabela `acesso_falhas_por_cpf` (migration 0028): `(tenant, chave, falhas, janela_inicio, ultima_em,
janela_anterior_bloqueada)`. A `chave` é uma derivação do hash de verificação (nunca o CPF e nunca o hash que a
AMH recebe). A linha é apagada sozinha uma janela depois de a sua passar (12 h desde a primeira falha: a
janela seguinte precisa saber se a anterior bloqueou), pelo varredor de retenção do acesso
(`expurgar_inativos`).

## Desbloqueio manual

**Não há ferramenta de desbloqueio**, de propósito: o bloqueio dura no máximo 6 h e o atendimento humano já foi
aberto. Se um caso real exigir liberar antes, a decisão é do dono, e a linha só pode ser apagada por quem
calcula a `chave` a partir do CPF (não há como achar o CPF a partir da tabela).

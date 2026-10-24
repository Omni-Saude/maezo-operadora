# Disposição deriva-dev — papel `AWS_DERIVA_ROLE_ARN` (GOV-0.9 · decisão D-31b)

**Data:** 2026-10-23 · **Base:** `main` `3c619d91` · **Missão:** W0 GOV-0.9 do
`docs/plans/maezo-completion-program.md` · **Decisão:** D-31b (registrada pelo dono em
2026-10-23 — este documento apenas a registra em forma de arquivo rastreado; nenhuma
decisão nova é tomada aqui).

## O que o dono decidiu (verbatim)

Linha da tabela §Decisões de `docs/plans/maezo-completion-program.md`, capturada verbatim
em 2026-10-23 sobre `main` `3c619d91`:

> | D-31b | deriva-dev = **ausência aceita** (nota datada do dono; sem novo papel AWS) | GOV-0.9 |

Linha correspondente do plano W0 (mesma fonte, capturada na mesma data — o texto
"pendente" descreve o estado anterior a este registro):

> | GOV-0.9 | Disposição do papel AWS_DERIVA_ROLE_ARN (criar ou registrar ausência aceita) | mecânica | S | pendente (D-31) |

## Disposição

**O papel `AWS_DERIVA_ROLE_ARN` NÃO foi criado e não será criado por ora: deriva-dev é
ausência aceita pelo dono (D-31b, 2026-10-23), sem novo papel AWS.** Nenhuma ação de
infra decorre desta disposição — nem criação de role OIDC somente-leitura, nem variável
de repositório, nem ativação do agendamento.

## Estado real da árvore (fatos, não promessas)

- `.github/workflows/deriva-dev.yml` está **DESLIGADO por construção**: só
  `workflow_dispatch` (o bloco `schedule` permanece comentado, linhas 23-24) e o primeiro
  passo do job (`Role somente-leitura configurada?`, linhas 50-57) falha com erro claro
  quando a variável de repositório `AWS_DERIVA_ROLE_ARN` não existe. A cerca natural da
  ausência é o próprio fail-closed do workflow: sem a variável, nada roda.
- O comentário de cabeçalho do mesmo workflow (linhas 15-20) registra o estado e o
  caminho de ativação completo (role → variável → descomentar `schedule`).
- `docs/runbooks/aws-ecs.md` §"Verificação de deriva do dev" (bloco "Estado: DESLIGADO
  até existir a role", a partir da linha 136) documenta trust/policy de menor privilégio
  para quando o dono da conta AWS decidir ligar — permanece como receita, não como
  pendência desta disposição.
- O classificador de ruído x deriva (`scripts/ci/classificar_deriva.py`) e seus testes
  (`tests/unit/ci/test_classificar_deriva.py`) permanecem rastreados e verdes: a
  capacidade analítica está pronta; o que falta é só a credencial AWS, aceita-ausente.

## Verificação da missão (GOV-0.9, item 3 do escopo)

`docs/Tarefas_Pendentes.md` **não contém** linha citando `AWS_DERIVA_ROLE_ARN` (grep de
2026-10-23 sobre `main` `3c619d91`: 0 ocorrências; as únicas ocorrências do token na
árvore rastreada são `docs/runbooks/aws-ecs.md`, `.github/workflows/deriva-dev.yml` e a
linha GOV-0.9 do plano). Nada a re-apontar: esta disposição é o alvo canônico da
pergunta "onde está a disposição do papel de deriva".

## Reversão (se o dono mudar de ideia)

Criar a role `maezo-operadora-dev-github-deriva` (trust/policy em
`docs/runbooks/aws-ecs.md`), definir a variável `AWS_DERIVA_ROLE_ARN`, descomentar o
`schedule` em `.github/workflows/deriva-dev.yml` e atualizar esta disposição com nova
data — decisão nova do dono, nunca ação autônoma de agente.

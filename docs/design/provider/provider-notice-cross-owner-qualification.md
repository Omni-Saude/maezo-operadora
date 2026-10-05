# Provider Notice — metadados de dependências com dono distinto

Addendum técnico do reparador terceiro; não é parecer independente nem admissão de
produção. Deriva de ADR-0063 e do contrato de Provider Notice existente, sem modificar
autoridades, grants, source SQL ou o helper de identidade.

## Problema reproduzido

No candidato `132159b149febc48e7f9209b14f384055dc971af`, a execução ROOT dos nove
testes PG teve três PASS e seis FAIL, sem skips. O diagnóstico público do producer
mostra `InsufficientPrivilegeError` em `qualify`, na resolução nominal de
`portal_communication.lock_session(text)`. O producer legitimamente não tem USAGE
nesse schema nem EXECUTE nesse helper; o recipient tem sua autorização própria.
Depois do helper, a resolução nominal de `portal_communication.content` tinha a
mesma dependência de USAGE do ator. Acrescentar esses privilégios ao producer não
é necessário para qualificar a instalação e alargaria sua superfície.

## Mudança de leitura de metadados

O qualifier usa os OIDs publicados no descriptor como parâmetros para consultar
`pg_catalog.pg_proc` e `pg_catalog.pg_class`, com joins aos namespaces e ao tipo
do argumento. Não executa o helper nem lê valores da relação protegida. Depois
confere o nome/schema exatos, a identidade, o owner, SECURITY DEFINER, o grant
EXECUTE do owner Notice e o digest de `pg_get_functiondef` já exigidos. O helper
também precisa ser função normal, com um argumento de entrada `pg_catalog.text`;
o content precisa ser a tabela `portal_communication.content`, com o mesmo OID
e owner e as mesmas recusas de acesso às colunas ciphertext/nonce/key.

O catálogo PostgreSQL define `pronargs` como a quantidade de argumentos de entrada
e `proargtypes` como os tipos dessa assinatura, com índice inicial zero. Por isso
o join consulta `proargtypes[0]`, sem resolver nomes da aplicação sob o principal
producer. [Documentação PostgreSQL 16 — pg_proc](https://www.postgresql.org/docs/16/catalog-pg-proc.html).
A antiga resolução `to_regprocedure` obtém um OID a partir de nome e tipos;
essa resolução textual é dispensada somente nessas dependências com dono distinto.
[Documentação PostgreSQL 16 — funções de informação](https://www.postgresql.org/docs/16/functions-info.html).

OID sozinho não é admissão. Um objeto renomeado, movido de schema, substituído,
com assinatura/owner/hash/ACL diferentes continua recusado. A consulta nominal
das funções do próprio schema Notice permanece no caminho original, com suas
verificações de ACL exata. Nenhum novo grant, fallback público, cópia de identidade
ou boolean de aprovação acompanha este reparo.

## Provas e limites

Os testes unitários cobrem a resolução que falhava antes, a leitura por OIDs sem
USAGE do ator e recusas por drift de identidade, assinatura, body e privacidade.
Os nove testes PG existentes foram preservados. Cinco casos PG adicionais verificam
qualificação do producer sem USAGE/EXECUTE no helper e recusas de rename/move dos
objetos pinados, mesmo quando o digest do helper alterado foi atualizado no
descriptor sintético de teste para isolar a checagem de identidade.

PG/Docker real e revisão independente pertencem ao ROOT. Dados e descriptors dos
testes são TestOnly e não comprovam instalação AUTH-SL1, credenciais, autoridade
ou atos externos de produção. A dependência do helper canônico AUTH com os pins
e contratos de currentness continua pendente de sua qualificação própria.

## Reparo v2 — configuração privada do engine

A execução ROOT seguinte, em `9f0772d41d713a7fa534af4479f7cb1f9d3f58f9`, executou
14 casos PG: oito PASS, seis FAIL, sem skips. A qualificação de metadados do
producer passou; o prepare foi recusado antes de I/O pelo fence de transação
finita existente. O fixture criava seus engines com o default
`hide_parameters=False`. A recusa é correta e o fence global permanece intacto.

O fixture declara agora `echo=False` e `hide_parameters=True` para seus próprios
engines, preservando o SSLContext verificado e os 14 corpos de testes. O store
recusa engine com echo ativo, parâmetros visíveis ou dialect distinto de
PostgreSQL já na construção e repete a checagem no início de `qualify`, cobrindo
mudança de configuração posterior. Nenhuma consulta de qualificação ocorre
antes dessa recusa. Os testes negativos usam AsyncEngine real sem abrir conexão;
não comprovam execução PostgreSQL. O próximo run ROOT e os veredictos
independentes continuam necessários para qualificar o corpo dos 14 casos.

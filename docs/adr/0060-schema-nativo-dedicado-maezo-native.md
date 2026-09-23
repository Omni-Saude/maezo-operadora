# ADR-0060: Relações nativas do portal num schema dedicado `maezo_native`, com dono próprio, à frente do path do engine e pinado no manifesto staff v2

**Status:** Accepted — decisão técnica aprovada pelo dono em 23/09/2026 (N7 do plano
`docs/plans/portal-autoridade-nativa-dev.md`, §6). **Implementação pendente** (T1.8 da Onda 1).
Ratificação humana formal: campos em branco no fim. · **Data:** 2026-09-23 · **Área:** Dados /
Segurança / Instalação do engine

> **Enquadramento.** O portal humano staff (`/cases`) depende de relações nativas `mzo_*` que o
> plugin Java do CIB Seven lê e grava **na transação do engine** (ADR-0049 D5). O plano de
> autoridade nativa propôs pô-las em `public` (D-C). A Onda 0 (23/09/2026, S1 R0 a R5, §7.3 do
> plano) **refutou** D-C e provou uma alternativa. Este ADR registra a alternativa como decisão,
> nomeia o schema e o login dono, e fixa o que o código tem de mudar para não depender de um
> `'public'` escrito à mão.
>
> **Escopo: só docs.** Nenhum código, DDL ou Terraform muda neste ADR. O código é a T1.8; o DDL e o
> script de instalação são a T1.4; o path do engine é a T1.2 e a Onda 4.

---

## Contexto

### O que existe no database `maezo` de dev (M2, 23/09/2026, só leitura)

| Fato | Resultado medido |
|---|---|
| Identidade e engine no mesmo database | `cibseven_app`, `maezo_app` e `portal_bff_amh` com sessões vivas em `maezo` |
| Schemas | `amh` (dono `maezo_app`), `cibseven` (dono `cibseven_app`), `public` (dono `pg_database_owner`) |
| Quem age como dono de `public` | `maezo_app`, porque é o `datdba` de `maezo` |
| ACL de `public` | `{pg_database_owner=UC, maezo_app=U, cibseven_app=U}`; `maezo_app` tem CREATE em `public` |
| O que já mora em `public` | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` (checkpointer da Helena) |
| `ACT_*` | 49 tabelas, só em `cibseven` |
| `mzo_*` | nenhuma, em schema nenhum |
| Path do engine vivo | `DB_URL=jdbc:postgresql://<host>/maezo?currentSchema=cibseven` |

### O que o código exige

1. **Plano AUTH (obrigatório para staff).** `HumanCommandPlugin.java:60-65` só liga staff com
   `authRuntime`. `AuthInstallation.runtime` relê `ConsumerEdgeInstallation.session(owner=false)`
   na conexão do engine (`AuthInstallation.java:90-94`), que exige quatro coisas
   (`ConsumerEdgeInstallation.java:60-74`):
   - `current_schema()` é o schema das `mzo_auth_*`;
   - o dono desse schema não é o login do engine;
   - o login do engine não tem CREATE nele;
   - o login do engine não tem nenhuma role-membro.

   A instalação, por sua vez, exige sessão **do dono** com `current_schema()` igual a esse schema e
   TLS (`ConsumerEdgeInstallation.java:60-69`).
2. **Pin staff no Java.** `StaffCaseStore.java:44` confere cada uma das 14 relações staff com
   `WHERE n.nspname='public'`. `StaffCaseStore.java:31-33` exige que `databaseSchema` seja nulo ou
   `public` e que `tablePrefix` seja vazio ou `public.`.
3. **Pin do witness no Python.** A qualificação filtra `n.nspname='public'`
   (`src/maezo/gateway/staff_cases/postgres.py:241`), e a leitura qualifica `public.` à mão
   (`postgres.py:266-267`).
4. **SQL sem qualificação.** O plugin escreve `FROM MZO_PORTAL_READ_MEMBERSHIP`,
   `MZO_HUMAN_TENANT` e outras sem schema. Quem resolve é o `search_path` da conexão JDBC.

### O que a Onda 0 provou (S1, PostgreSQL 17.11 local, layout de ACL copiado do dev)

| Rodada | Path | Resultado |
|---|---|---|
| R1 | `cibseven` (hoje), `mzo_*` fora dele | cria os 49 `ACT_*` e morre em `human engine store unavailable`: `MZO_HUMAN_TENANT` não resolve |
| R2 | `cibseven,public` (**D-C**) | sobe, `ACT_*` em `cibseven`, pin staff de `public` passa nas 14 relações. **AUTH falha**: `current_schema=cibseven`, dono `cibseven_app`, `owner_differs=f`, `runtime_lacks_create=f` |
| R3 | `public,cibseven` (ordem invertida) | sobe e passa nas precondições de sessão. Mas `ALTER ROLE pg_database_owner LOGIN` falha com `role name "pg_database_owner" is reserved`, e uma `public.act_ge_property` criada por `maezo_app` **passa a ser a tabela que o engine resolve** |
| R4 | `public,cibseven`, banco vazio | não sobe: `ENGINE-03015 … permission denied for schema public` |
| R5 | `maezo_native,cibseven` (**esta decisão**), `mzo_*` em `maezo_native` com dono dedicado | **sobe**. `ACT_*` fica em `cibseven` (49) e `mzo_*` em `maezo_native` (20). AUTH passa: `current_schema=maezo_native`, `owner_differs=t`, `runtime_lacks_create=t`, memberships 0. Com `nspname='maezo_native'` o pin staff passa nas 14 relações; com o `'public'` fixo de hoje, 0 |

---

## Decisão

### D1 · Um schema dedicado com dono próprio

- **Schema:** `maezo_native`, no database `maezo`.
- **Dono:** o login de instalação **`maezo_native_schema_owner`**, criado só para isso.
  - Ele não é `maezo_app`, `cibseven_app`, `portal_bff_amh` nem o master.
  - Não tem super, createdb, createrole, bypassrls nem replication.
  - Não é membro de nenhuma role. Faz login só pela task avulsa de instalação (Onda 3).
- **Conteúdo:** todas as relações `mzo_*`, sem exceção: human, portal-read, staff, staff-event,
  AUTH e a tabela de admissão Q2 da T1.7. Nenhuma outra relação é criada ali.
- **Por que não reusar `maezo_app`:** ele é o dono do database e o runtime da Helena. Se fosse dono
  das tabelas de autoridade, o próprio app poderia gravar grants e designações.
- **Por que este nome de login:** o plano propunha `maezo_native_owner`. Ele colide, em grep e na
  leitura, com o schema `maezo_native_owner_v1` do programa native-v2
  (ADR-0054). O nome `maezo_native_schema_owner` não é substring de nada que exista no repo.

### D2 · O path do engine é `currentSchema=maezo_native,cibseven`

- **O que resolve onde:** `maezo_native` vem primeiro, então o SQL sem qualificação do plugin
  resolve as `mzo_*` ali, e `current_schema()` é `maezo_native`, como o AUTH exige. `cibseven` vem
  em segundo e continua resolvendo os `ACT_*`.
- **Sombreamento:** só o dono cria em `maezo_native`, e o `cibseven_app` não tem CREATE nele.
  Assim, nenhum runtime consegue pôr uma tabela na frente do `ACT_*`.
- **O que o `cibseven_app` recebe em `maezo_native`:** `USAGE` e os grants de tabela de cada DDL.
  Não recebe CREATE nem membership.
- **O que não muda:** `databaseSchema` e `tablePrefix` do engine continuam **não definidos**. A
  resolução por `search_path` é o desenho, e é isso que `StaffCaseStore.java:33` já exige. A leitura
  do `bpm-platform.xml` vivo (M1) não mostrou nenhuma das duas propriedades; a confirmação por
  execução fica **NÃO VERIFICADA** até a Onda 4.
- **TLS:** o `DB_URL` fixa `sslmode=verify-full`. Hoje o default do pgjdbc é `prefer` e o AUTH
  exige TLS (`ConsumerEdgeInstallation.java:69`; plano §7.2).

### D3 · O schema é pin, não constante

O nome do schema vira um campo pinado e sai do código.

| Onde | Hoje | Passa a ser |
|---|---|---|
| Manifesto do pacote staff | `portal-staff-material.v1` (`production_config.py:306`) | **`portal-staff-material.v2`**, com o campo novo `native_schema` (identificador PostgreSQL, `^[a-z_][a-z0-9_]{0,62}$`). A v1 é **recusada** no load: não existe pacote v1 publicado (§7 do plano: só `session-dsn` na conta), então não há o que migrar |
| Configuração Java do staff | `StaffCaseInstallation.Configuration` sem schema (`StaffCaseInstallation.java:15-17`) | ganha `nativeSchema`, e ele **entra no `digest()`** (`StaffCaseInstallation.java:24-27`). Assim `native_configuration_sha256` (§4 do plano) e a designação instalada, que compara esse digest (`StaffCaseInstallation.java:172`), passam a amarrar o schema |
| Pin Java | `nspname='public'` (`StaffCaseStore.java:44`) | `nspname = ?` com o schema da configuração |
| Pin e leitura do witness | `nspname='public'` e `public.` à mão (`postgres.py:241`, `:266-267`) | o schema do manifesto, como identificador quotado. Nunca por interpolação de texto livre |
| Terraform | sem campo | `portal.staff.native_schema`, com a mesma validação de formato |

### D4 · O pin prova que o nome sem schema resolve para a relação pinada

Conferir o OID em `nspname = <pinado>` não basta. O SQL do plugin é sem qualificação, então quem
decide é o path, e o PostgreSQL busca `pg_temp` **antes** de tudo, por padrão. O `cibseven_app` tem
TEMP por herança de PUBLIC. O pin da T1.8 passa a exigir, para cada relação:

1. `current_schema()` = schema pinado;
2. `to_regclass('<nome sem schema>')::oid` = OID pinado. Isso recusa um homônimo em `pg_temp` ou
   num schema anterior;
3. as condições que já existem hoje: dono pinado, `relkind='r'`, sem RLS, login não é membro do
   dono, e matriz de escrita (`StaffCaseStore.java:46-60`).

A mesma regra (a e b) vale para o witness Python, que qualifica o nome e por isso só precisa da
condição de namespace.

**Alternativa não adotada:** pôr `pg_temp` explicitamente no fim do path
(`currentSchema=maezo_native,cibseven,pg_temp`). É barata, mas **não foi executada** no spike. A
condição 2 cobre o caso sem depender dela.

### D5 · Comparação exata, sempre

`maezo_native` é prefixo de `maezo_native_v2` e de `maezo_native_owner_v1`, schemas do programa
native-v2/D7 (imagem `secured-v2`, ADR-0054 a ADR-0058), que **este plano não usa**. Toda checagem
de schema é igualdade exata: nada de `LIKE 'maezo_native%'` nem de `startswith`. Os dois programas
coexistem sem compartilhar schema, dono ou grant.

### D6 · A matriz de `checkpoint_chunk` se corrige no código (decisão do I8)

`mzo_staff_case_checkpoint_chunk` só recebe `INSERT` e é lido por `ORDER BY chunk_index`. Nenhum
caminho faz UPDATE nela: `StaffCaseStore.java:89` lê e `StaffCaseStore.java:155` insere, e não há
outra ocorrência em `src/`. Ela é imutável por natureza. Por isso a T1.8 acrescenta o sufixo `_chunk`
à lista de imutáveis de `StaffCaseStore.java:50`, e o DDL fica com `SELECT,INSERT`
(`staff-case-schema-postgres.sql:138`), o menor privilégio. Não se concede UPDATE para agradar o
pin.

---

## Alternativas rejeitadas

| Alternativa | Por que não | Prova |
|---|---|---|
| **D-C:** `mzo_*` em `public`, path `cibseven,public` | Com `cibseven` na frente, `current_schema()` é `cibseven`, que pertence ao próprio `cibseven_app`. Nenhum layout com `cibseven` na frente qualifica o AUTH | S1 R2 |
| **Ordem invertida:** `public,cibseven` | (i) o dono de `public` é `pg_database_owner`, role reservada que não faz login, e a instalação AUTH exige login = dono do schema. Isso obrigaria a trocar o dono de `public` no database compartilhado; (ii) `maezo_app` tem CREATE em `public` e, com `public` na frente, **sombreia** o `ACT_*`; (iii) o checkpointer da Helena mora em `public`; (iv) banco vazio não sobe | S1 R3 (reserva e sombreamento provados), R4; M2 (ACL, checkpointer) |
| `mzo_*` dentro de `cibseven` | `cibseven` pertence a `cibseven_app`, e o AUTH exige dono ≠ login do engine e sem CREATE. Mudar o dono de `cibseven` quebra o auto-update do `ACT_*` (`databaseSchemaUpdate=true`, M1) | Leitura de `ConsumerEdgeInstallation.java:60-74`; M1. Não executado |
| `databaseSchema`/`tablePrefix` do engine apontando para outro schema | `StaffCaseStore.java:33` recusa, e o prefixo muda o nome de **todas** as tabelas do engine compartilhado com a Helena | Leitura |
| Reusar `maezo_native_v2` (native-v2/D7) | É outro programa (imagem `secured-v2`), com catálogo fechado de 6 relações e 19 funções (ADR-0054). Misturar dono ou grant com ele invalida a qualificação dele | ADR-0054; `deploy/cibseven/secured/NATIVE-V2.md` |
| Um schema por tenant (`amh_native`) | Hoje o dev tem um único tenant (`amh`), e o escopo por tenant já está nas colunas `tenant_` das `mzo_*`. Um schema por tenant multiplicaria o path do engine, que é um só para todos | Leitura. Reabrir quando houver segundo tenant no mesmo engine |

---

## Consequências

**Positivas**

- O AUTH qualifica e o staff pode ligar. Sem isso, `/cases` não existe (S1 R5).
- O runtime não consegue sombrear tabela nenhuma, nem do engine nem da autoridade.
- `public` e o checkpointer da Helena ficam intocados.
- O schema entra no digest da configuração nativa e no manifesto. Quem aprova (§4 do plano)
  confere o schema como qualquer outro pin, do catálogo vivo.

**Negativas (aceitas) e o que cada onda herda**

1. **Manifesto v2 (T1.8).** `portal-staff-material.v2` com `native_schema`. Tocam:
   - `production_config.py` (modelo, validação, recusa da v1);
   - `materials.py` (`verify_materials`);
   - `postgres.py` (pin e leitura);
   - Java: `StaffCaseInstallation.Configuration` e `StaffCaseStore`;
   - `portal-variables.tf` e `service-portal.tf`;
   - a fixture `tests/unit/gateway/test_staff_production_materials.py`.

   A T1.3 (gerador) monta v2 desde o início. A T1.1 (composição) lê `native_schema` do arquivo
   montado.
2. **Onda 3 com login dono dedicado.** Artefatos novos:
   - a role `maezo_native_schema_owner` (senha como verificador SCRAM calculado no cliente, mesma
     técnica do `portal_bff_amh`);
   - `CREATE SCHEMA maezo_native AUTHORIZATION maezo_native_schema_owner`, pela role de
     administração;
   - todo o DDL `mzo_*`, instalado **por sessão do dono** com `current_schema()=maezo_native` e TLS,
     como o AUTH exige;
   - `GRANT USAGE` ao `cibseven_app` e ao witness.

   A instalação AUTH (`AuthInstallation.installSchema`/`designate`) entra nesta onda. A reversão é
   `DROP SCHEMA maezo_native CASCADE` e `DROP ROLE`, e só vale enquanto as tabelas estiverem vazias
   (antes da Onda 4).
3. **Onda 4: path e imagem revertem juntos, nunca separados.**

   | Imagem | Path | Resultado |
   |---|---|---|
   | nova | antigo (`cibseven`) | morre no boot (I2, S1 R1) |
   | antiga | novo (`maezo_native,cibseven`) | sobe, mas não serve para nada |

   A reversão restaura o par que roda hoje: `engine_image_digest=sha256:6f478e23…` e
   `currentSchema=cibseven`. Ele fica anotado no plano (§3, Onda 4) e vai no mesmo apply.
4. **Runbook de recuperação de banco vazio (DR ou restore).** Com o dono dedicado na frente do path,
   o `cibseven_app` não tem CREATE no primeiro schema, e um banco **sem `ACT_*`** não sobe: o
   auto-update tenta criar ali (I9, S1 R4 no layout análogo). A ordem obrigatória:
   1. Subir **a imagem antiga** (sem plugin) com `currentSchema=cibseven` e esperar os 49 `ACT_*`.
      A imagem nova não serve aqui, porque morre com esse path (item 3).
   2. Reinstalar `maezo_native` (Onda 3) com uma **nova `database_incarnation`**. O README do engine
      exige incarnation nova depois de restore (`src/maezo/portal/engine/README.md:244`).
   3. Nova admissão Q2 (T1.7) e nova designação staff, assinadas pelo aprovador. Isso repete as
      Ondas 2, 3 e 5 do plano.
   4. Subir a imagem nova com `currentSchema=maezo_native,cibseven`.

   Em dev hoje o `ACT_*` existe, então isso não bloqueia a Onda 4. É passo de DR e fica no runbook.
5. **Texto a corrigir.** `src/maezo/portal/engine/README.md:238-239` manda aplicar
   `portal-read-schema-postgres.sql` "in the same PostgreSQL schema as CIB 2.1". Com esta decisão
   isso deixa de valer, e a T1.8 corrige o parágrafo. A cláusula de `mzo_*` em `public` do plano (D-C)
   já está riscada.
6. **Um login e um schema a mais no database compartilhado.** O custo recorrente é a custódia da
   senha do dono (Secrets Manager, caminho D-G). O dono só é usado em janela de instalação.

**O que este ADR não faz**

- Não cria nada na conta.
- Não muda o engine-rest. A dívida D7 em dev, N5, é assunto da cerca de CI da T1.9 do plano.
- Não decide quem vê qual caso (N3, T1.6).
- Não toca o programa native-v2.

---

## Relação com ADRs existentes

| ADR | Relação |
|---|---|
| ADR-0049 D5 | **Implementa** a parte de instalação: a autoridade continua dentro do engine e na mesma transação. Muda só *onde* moram as relações. Não emenda o texto |
| ADR-0054 a ADR-0058 (native-v2/D7) | **Coexiste** sem compartilhar schema, dono nem grant. D5 existe por causa da proximidade dos nomes |
| ADR-0004 (instância por tenant) | Preserva: o escopo por tenant continua nas colunas `tenant_` e nos pins de escopo. Não há RLS nas `mzo_*` lidas pelo witness, por exigência do próprio pin (`postgres.py:254`) |
| ADR-0027 (durabilidade em Postgres) | Sem mudança |

---

## Ratificação

A decisão técnica foi aprovada pelo dono em 23/09/2026 (N7). Os campos abaixo são para a revisão
humana L5 e **não são preenchidos por agente**.

| Campo | Valor |
|---|---|
| Ratificado por | *(em branco)* |
| Data da ratificação | *(em branco)* |
| Nome do schema (`maezo_native`) e do login (`maezo_native_schema_owner`) | *(pendente: confirma / troca)* |
| Runbook de banco vazio (Consequência 4) revisado pelo dono da plataforma | *(em branco)* |

## Supersedes

— (nenhum ADR). Supera, **dentro do plano** `docs/plans/portal-autoridade-nativa-dev.md`, a decisão
D-C, que não era ADR.

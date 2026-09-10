# Prova conjunta descartável D5/D6

Rastreio: ADR-0049 D5/D6; DL-0006/0017/0048; ADR-0006/0007. A prova usa apenas
`MZO-HUMAN-SYNTHETIC`, claim/release sem decisão/variável clínica e autoridades
explicitamente sintéticas. Não representa login humano, `AuthorizedAssignment`,
`PostgresHumanAdmission`/`project_assignment`, factory produtiva, seis bindings
reais, D7 REST/credenciais, retenção ratificada ou autorização clínica.

O ROOT é o único dono de Docker/PostgreSQL/imagem e do `EngineLock`. Seguir todos
os passos da [receita original](README.md), preservando preflight de montagem,
pin de imagem e runtime Java, fonte exata, isolamento de portas e teardown. Na
invocação de `prepare.py`, acrescentar **`--relay-synthetic`**. Essa opção cria
um tenant aleatório `relay_<24 hex>` válido para schema PostgreSQL e habilita o
formulário sintético somente no trust privado descartável. Sem a opção, a
fixture original continua `package-test`, com formulário inativo e os cinco
testes originais inalterados. Nenhuma configuração produtiva foi modificada.

O arquivo privado `relay-fixture.json` aponta exclusivamente para o PostgreSQL
loopback da fixture (15433), REST loopback (18080) e endpoint mTLS (18443). A senha
é lida somente do arquivo privado gerado; não exportar DSN com senha, imprimir
chaves ou copiar compose/XML/trust/configuração à evidência. Manter diretório
0700, arquivos privados0600 e binds `create_host_path=false`. O teste recusa
ausência de configuração, fonte diferente, servidor remoto ou opt-in inativo.

Após a imagem ficar pronta, executar sem xdist e sem outra suíte concorrente:

```sh
export MAEZO_HUMAN_RELAY_PRIVATE_DIR="$PRIVATE"
export MAEZO_HUMAN_RELAY_ARTIFACTS="$EVIDENCE/relay-artifacts"
mkdir -p "$MAEZO_HUMAN_RELAY_ARTIFACTS"
.venv/bin/python scripts/ci/run_live_pytest.py --root "$PWD" collect --output "$EVIDENCE/relay-collection.json" -- -q -m integration tests/integration/gateway/test_human_relay_live_cib.py
.venv/bin/python scripts/ci/run_live_pytest.py --root "$PWD" run --expected "$EVIDENCE/relay-collection.json" --evidence "$EVIDENCE/relay-execution.json" --junit "$EVIDENCE/relay-junit.xml" --validation "$EVIDENCE/relay-validation.json" -- -q -m integration tests/integration/gateway/test_human_relay_live_cib.py
```

A coleção esperada é **17 casos**, sem skip/xfail. A autoria realizou somente
coleção e controles offline; a primeira execução CIB/PG conjunta pertence ao
ROOT e deve preservar todas as falhas. Este perfil de imagem ainda não foi
integrado ao CI: nem coleta, nem as suites separadas anteriores fecham essa
prova. Invocar o módulo sem configuração explícita falha; não há fallback/mock.

Cada caso cria um schema tenant novo, aplica as migrações reais0002/0005/0013
necessárias ao outbox, usa um pool novo sem search_path pré-configurado e destrói
somente o schema que criou. Não é outra prova do grafo Alembic completo. O
mesmo PostgreSQL descartável hospeda as tabelas ACT/MZO em public; o processo
usa tenant real da fixture. O helper implanta/inicia o BPMN sintético por REST,
publica principal/evidência pelo endpoint assinado `human-authority` e deriva
comandos canônicos de dados efetivamente lidos em ACT/MZO e bytes do recurso
implantado. Esses acessos de bootstrap/observação não afirmam enforcement D7.

O caminho sob teste é `PostgresHumanOutbox` → `HumanCommandRelay` →
`PartitionedEd25519Signer`/`MTLSHumanEngineTransport` → servlet/plugin CIB real →
receipt durável → resultado e cadeia `PostgresAuditSink` reais. O observer apenas
delega ao transporte real e retém retornos; não fabrica resposta, receipt ou ACK.

| Oráculo | Evidência exigida |
|---|---|
| Intent durável e crash pré-dispatch | Processo filho termina com **rc73 intencional** após commit; nenhum transport criado nele; tarefa/receipt inalterados; novo PID recupera GET autenticado antes de POST |
| Claim/release | Revisão ACT consumida/resultante exata, responsável correto, task ainda ativa, receipt byte-exato, principal/workload/digest/intent e duas linhas auditáveis por comando |
| Commit com resposta perdida | Observer descarta somente após retorno real do mTLS; receipt é observado no PG; reinício consulta GET sem segundo POST/efeito |
| Falha de resultado | Triggers PostgreSQL de audit, mark e commit diferido; sequência não transacional prova que a falha foi atingida; resultado local revertido e recibo do engine preservado; novo PID repara via GET |
| Dois relays | Exclusão antes da expiração e takeover após expiração real no relógio PG; callback antigo não sobrescreve resultado; um único incremento/receipt |
| Conflitos | Mudança autoritativa de task/evidência/membership após intent produz conflito técnico auditado sem alterar vencedor |
| Identidade | Subject divergente e certificado de autoridade impedem GET/POST do relay; tenant/workload divergentes assinados recusados pelo transporte/serviço real, com controle positivo no mesmo serviço |
| Imutabilidade | Mesmo task/command com outro digest recusa no outbox e em GET/POST reais; replay exato mantém receipt e efeito originais; claim obsoleto não finaliza/libera sucessor |

Os negativos são testes que devem PASSAR por confirmar recusas; rc73 é apenas o
crash filho esperado, não sucesso de pytest. Receipts sintéticos brutos, eventos,
comandos canônicos, PID/rc e linhas ACT/MZO/audit/outbox ficam em subdiretórios
únicos de `relay-artifacts`; o ROOT conserva também imagem/digest, versão real
Java/CIB/Tomcat, logs privados, fontes e manifestos de fases/JUnit. Falha anterior
de setup não é RED do produto, e 202/pending não é execução.

As falhas são técnicas e locais: não provam falha física do host, failover de
banco, partição de rede real ou consistência distribuída com IdP/FHIR. A perda
de resposta ocorre na fronteira de retorno do transporte; o commit real já foi
confirmado e seus bytes observados antes da injeção. O raw REST atual permanece
um trabalho separado de D7; esta fixture não concede nem testa regras clínicas.

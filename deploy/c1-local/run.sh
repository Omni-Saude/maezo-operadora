#!/usr/bin/env bash
# C1 — checkpoint de integracao LOCAL (plano portal-autoridade-nativa-dev, Onda 1). Ver README.md.
#   run.sh all       roda tudo do zero (down -v, build, passos) e imprime uma linha medida por passo
#   run.sh <passo>   um passo so: build tls db-base bootstrap materials db-native digests engine-config
#                    engine-up auth-install auth-fixture seed publish issuer portal h1-task assignment
#                    portal-human rotate
#   run.sh logs      linhas relevantes do log do engine
#   run.sh down      apaga containers, rede e o volume c1private (chaves, senhas, raiz de TESTE)
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$HERE"
DC=(docker compose)
RUN=("${DC[@]}" run --rm -T runner)
# Isolamento (outra sessao com o C1 no ar): C1_PROJECT, C1_IMG, C1_PG_PORT, C1_ENGINE_PORT.
export C1_IMG="${C1_IMG:-maezo-c1}"
IMG="$C1_IMG"

build() {
  docker build -q -f ../cibseven/Dockerfile.human --build-arg INSTALL_STAFF_COMPOSITION=true \
    --build-arg INSTALL_PORTAL_READ=true -t "$IMG"-human:base ../.. >/dev/null
  docker build -q -f engine.Dockerfile --build-arg C1_HUMAN_BASE="$IMG"-human:base -t "$IMG"-engine:local ../.. >/dev/null
  docker build -q -f ../cibseven/Dockerfile -t "$IMG"-legacy:local ../.. >/dev/null
  docker build -q -f runner.Dockerfile -t "$IMG"-runner:local ../.. >/dev/null
  docker build -q -f ../cibseven/Dockerfile.human --build-arg INSTALL_STAFF_COMPOSITION=false     --build-arg INSTALL_PORTAL_READ=true -t "$IMG"-human:nostaff ../.. >/dev/null
  docker build -q -f engine.Dockerfile --build-arg C1_HUMAN_BASE="$IMG"-human:nostaff     -t "$IMG"-engine:nostaff ../.. >/dev/null
  echo "C1 build: PASS Dockerfile.human(STAFF_COMPOSITION=true,PORTAL_READ=true)+provedor, legado, runner"
}

wait_log() {  # wait_log <servico> <regex-de-fim> <segundos>
  local end=$((SECONDS + $3))
  while [ $SECONDS -lt $end ]; do
    if "${DC[@]}" logs "$1" 2>&1 | grep -Eq "$2"; then return 0; fi
    sleep 3
  done
  return 1
}

bootstrap() {
  "${DC[@]}" --profile bootstrap up -d engine-bootstrap >/dev/null 2>&1
  if wait_log engine-bootstrap "Server startup in" 180; then ok=PASS; else ok=FAIL; fi
  "${DC[@]}" --profile bootstrap stop engine-bootstrap >/dev/null 2>&1
  n=$("${DC[@]}" exec -T postgres psql -U postgres -d maezo -tAc \
    "select count(*) from pg_tables where schemaname='cibseven' and tablename like 'act_%'")
  echo "C1 bootstrap: $ok imagem viva (Dockerfile) com currentSchema=cibseven criou $n ACT_* em cibseven"
}

digests() {
  docker run --rm --entrypoint sha256sum "$IMG"-engine:local \
    /camunda/lib/maezo-human-command.jar /camunda/lib/maezo-portal-read-provider.jar \
    | "${RUN[@]}" sh -c 'cat > /c1/state/code-digests.txt'
  echo "C1 digests: PASS sha256 dos JARs carregados (engine, provedor) lidos da imagem"
}

engine_logs() {
  "${DC[@]}" logs engine 2>&1 | grep -E "Server startup|ENGINE-[0-9]+|SEVERE|staff_native_configuration_digest|portal_read_provider|FATAL|Caused by" \
    | sed -E 's/^engine-1 +\| //' | cut -c1-400 | head -${1:-25}
}

engine_up() {
  "${DC[@]}" --profile engine up -d --force-recreate engine native >/dev/null 2>&1
  wait_log engine "Server startup in|ENGINE-08043|Destroying ProtocolHandler|FATAL" 240 || true
  if "${DC[@]}" logs engine 2>&1 | grep -q "Server startup in" \
     && ! "${DC[@]}" logs engine 2>&1 | grep -q "ENGINE-08043"; then
    echo "C1 engine-up: PASS $("${DC[@]}" logs engine 2>&1 | grep -oE 'staff_native_configuration_digest=[0-9a-f]{12}|portal_read_provider root_sha256=[0-9a-f]{12}' | sort -u | tr '\n' ' ')"
  else
    cause=$("${DC[@]}" logs engine 2>&1 | grep -E "ENGINE-16004|at br\.com\.maezo" | sed -E 's/^engine-1 +\| +//; s/.*ENGINE-16004 //' | head -4 | tr '
' ' ')
    echo "C1 engine-up: FAIL ${C1_ENGINE_IMAGE:-$IMG-engine:local} nao subiu: $cause"
  fi
}

down() {
  "${DC[@]}" --profile bootstrap --profile engine --profile tools down -v --remove-orphans >/dev/null 2>&1
  echo "C1 down: containers, rede e volume c1private (chaves, senhas, raiz de TESTE) apagados"
}

py() {  # cada passo roda no servico que tem as montagens dele
  local service=runner
  case "$1" in publish|h1-task|assignment|tasks-sup) service=job ;; portal-human|portal-human-sup) service=portal-human ;; issuer|rotate|rotate-issuer|supervisor|auth-install|auth-fixture) service=issuer ;; portal-init) service=portal-init ;; portal|portal-r2|portal-sup) service=portal ;; esac
  "${DC[@]}" --profile engine --profile tools run --rm -T "$service" python -m c1 "$1"
}

case "${1:-all}" in
  build) build ;;
  tls) "${DC[@]}" run --rm -T --user 0:0 runner python -m c1 tls ;;
  db-base) "${DC[@]}" up -d --wait postgres >/dev/null 2>&1; py db-base ;;
  bootstrap) bootstrap ;;
  digests) digests ;;
  engine-up)  # engine-up [staff|nostaff] [debug]
    if [ "${2:-staff}" = nostaff ]; then export C1_ENGINE_IMAGE="$IMG"-engine:nostaff; fi
    if [ "${3:-}" = debug ]; then
      export C1_JAVA_OPTS="-Xms256m -Xmx768m -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005"
    fi
    engine_up ;;
  logs) engine_logs "${2:-40}" ;;
  materials|db-native|engine-config|assemble|w1|auth-install|auth-fixture|seed|publish|issuer|portal-init|portal|h1-task|assignment|portal-human|rotate|rotate-issuer|portal-r2|supervisor|portal-sup|tasks-sup|portal-human-sup) py "$1" ;;
  down) down ;;
  all)
    down; build
    "${DC[@]}" run --rm -T --user 0:0 runner python -m c1 tls
    "${DC[@]}" up -d --wait postgres >/dev/null 2>&1; py db-base
    bootstrap; py materials; py db-native; digests; py engine-config; py assemble
    engine_up                       # imagem staff, como a T1.2 entrega (F1/F2/F4 corrigidos em fix/c1-java; sem W1)
    py auth-install || true        # D8: qualifica a instalacao AUTH com a definicao deployada
    py auth-fixture || true        # D-K.2: entradas SYN- publicadas + human-auth-start assinado
    py seed; py publish || true; py issuer || true
    py h1-task || true               # H1-H4 (D-N): fonte real de tarefas no job T1.5, sem D11-D13
    py assignment || true            # Onda 8: plano de atribuicao ATIVO pelo instalador/ops do repo
    py portal-init || true; py portal || true    # /cases sob a admissao revisao 2 (regressao do staff)
    py portal-human || true          # Onda 8: /tasks?queue=mine|team com o plano humano ligado
    py rotate || true                # Onda 8: designacao r1->r2 pelo `rows` + segredo nativo com o digest da r2
    engine_up                        # o engine pina a designacao na composicao staff: reinicia com a r2
    py assemble; py portal-init || true   # o portal tambem pina: pacote novo da r2, task nova
    py rotate-issuer || true; py portal-r2 || true
    py supervisor || true; py portal-sup || true
    py tasks-sup || true; py portal-human-sup || true ;;   # /tasks?queue=team do supervisor: a tarefa dele, nunca 503   # SLA vencido: caso no supervisor continua vivo   # emissor sob a r2; /cases + detalhe com a tarefa
  *) echo "passo desconhecido: $1" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# Diagnostico do C1: o plugin recusa sem logar o motivo (fail-closed). Com o engine subido com
#   C1_JAVA_OPTS="-Xms256m -Xmx768m -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005"
# este script prende o jdb na 5005 e imprime a pilha do PRIMEIRO `Rejected`/`IllegalStateException`
# lancado enquanto o passo pedido roda (`jdb-catch.sh publish`). So local; nunca num engine real.
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "$0")"
step="${1:?passo}"
classes="${JDB_CLASSES:-br.com.maezo.human.Rejected}"
{
  for c in $classes; do echo "catch all $c"; done
  sleep "${JDB_WAIT:-40}"
  for _ in 1 2 3; do echo "where"; echo "cont"; sleep 1; done
  echo "exit"
} | docker run --rm -i --network maezo-c1_default maven:3.9.9-eclipse-temurin-17 \
    jdb -attach engine:5005 2>&1 | grep -E "Exception occurred|\[[0-9]+\] br\.com|\[[0-9]+\] java" | head -60 &
sleep 8
if [ "$step" = publish ]; then docker compose run --rm -T job python -m c1 "$step" >/dev/null 2>&1 || true
else docker compose run --rm -T runner python -m c1 "$step" >/dev/null 2>&1 || true; fi
wait

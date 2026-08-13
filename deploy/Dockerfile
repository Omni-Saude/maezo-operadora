# ============================================================
# Imagem de aplicação MAEZO — W-R0 / R2 (platform-completion-plan.md)
#
# Postura:
#   - Usuário não-root uid:gid 1000:1000 (alinha com Helm runAsUser 1000)
#   - Raiz somente-leitura: em tempo de execução apenas /tmp é gravável
#     (/tmp é montado como emptyDir pelo Helm — nada é escrito na raiz do FS)
#   - Multi-stage: "builder" isola deps de build; "runtime" copia apenas
#     o venv + código — sem pip, sem wheel cache, sem hatchling em produção
#   - Instala a partir de uv.lock (NÃO `pip install .` a partir do pyproject):
#     a imagem publicada carrega EXATAMENTE as versões resolvidas/auditadas no
#     lock — reprodutibilidade byte-a-byte entre o lock e o artefato implantado
#     (WS-3b predeploy; antes disso, correções de segurança no lock não
#     alcançavam deterministicamente a imagem de produção).
#
# Quatro componentes selecionados pelo Helm via command::
#   1. maezo.runtime.agent_runtime   — agent-runtime (W-R0/R1) [DEFAULT]
#   2. maezo.gateway                 — gateway HTTP/REST        (W-R1)
#   3. maezo.platform.webhooks       — webhook-receiver         (W-R1)
#   4. maezo.tools.mcp_fhir.sync    — fhir-sync daemon         (W-R1)
# ============================================================

# ── Stage 1: builder ────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv binário estático, versão pinada (mesma UV_VERSION usada em Makefile/CI — WS-3a)
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

# Sem bytecode, saída sem buffer durante o build; uv sem download de interpretador
# (o base image já traz Python 3.12 == requires-python) e sem hardlink entre
# camadas (evita avisos/heurísticas de link em builds multi-stage).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# pyproject.toml + uv.lock primeiro → layer de deps cacheada; só invalida se uma
# dependência mudar (não se apenas o código em src/ mudar). --no-install-project
# instala SÓ as dependências travadas no lock — o próprio pacote ainda não existe
# neste layer (src/ ainda não foi copiado).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Código-fonte e config necessários para o build hatchling (src layout). Segunda
# camada: instala o próprio projeto (não-editável) — rápida, pois as dependências
# já estão resolvidas/instaladas na camada anterior.
#
# T1.6 (predeploy-caliber defect, found while live-booting `docker compose --profile app`):
# pyproject.toml's `[tool.hatch.build.targets.wheel.force-include]` (ADR-0025 D2 Rev 1, added
# T1.8) requires `spec/policies/autonomy` to exist at the project root when hatchling builds the
# wheel — `uv sync --no-editable` below invokes exactly that build. Without this COPY, the build
# fails closed with `FileNotFoundError: Forced include not found: /app/spec/policies/autonomy`
# (this Dockerfile predates T1.8 and was never updated; the guarded `docker/build-push-action` in
# cd.yml is `if: vars.AWS_ENABLED == 'true'`, off by default, so no CI run had exercised this path
# since). Copy ONLY `spec/policies/autonomy` — matching the force-include's own narrow scope
# exactly; the rest of `spec/` is NOT shipped in the image (T0.3 `MAEZO_SPEC_DIR` / the per-agent
# `AGENT_DEFINITION_PATH` ConfigMap mount are the runtime discovery paths for everything else).
COPY spec/policies/autonomy ./spec/policies/autonomy
# Same failure class as the block above, second occurrence (2026-08-12, found live-booting
# `docker compose --profile app`): MZO-060 (PR #213) added a third force-include —
# `spec/policies/amh` (the AMH inbox DBA-ratification artifact) — and this Dockerfile was again
# not updated, so `uv sync --no-editable` failed closed with
# `FileNotFoundError: Forced include not found: /app/spec/policies/amh`. The AWS-gated cd.yml
# build still never runs in CI, which is why this surfaced only on a local boot.
COPY spec/policies/amh ./spec/policies/amh
COPY src/ ./src/
COPY config/ ./config/
RUN uv sync --frozen --no-dev --no-editable

# ── Stage 2: runtime ────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# libpq runtime: o psycopg 3 (pure-python, via langgraph-checkpoint-postgres) faz dlopen de
# `libpq.so.5` JA NO IMPORT — e `runtime/checkpoint.py` e importado no topo do agent-runtime.
# python:3.12-slim NAO traz libpq5; sem isto o container falha no import ANTES de subir o health
# server (CrashLoop), anulando o objetivo do W-R0. --no-install-recommends + limpeza p/ imagem enxuta.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Usuário não-root (ADR-0006; Helm securityContext.runAsUser 1000)
RUN groupadd --gid 1000 maezo \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin maezo

WORKDIR /app

# Copia o venv (deps + projeto, instalados a partir do uv.lock) do stage builder.
# --chown na COPY (em vez de um `RUN chown -R` separado): o venv agora vive dentro
# de /app (antes vivia em /opt/venv, fora da árvore chown'd) — um chown recursivo
# subsequente sobre uma camada de COPY já materializada força "copy-up" no overlayfs
# e DUPLICA o custo em disco do venv inteiro (~220MB extra medidos). --chown aplica
# a propriedade na mesma materialização da COPY, sem camada extra.
COPY --from=builder --chown=maezo:maezo /app/.venv ./.venv

# Copia código e configuração da aplicação
COPY --from=builder --chown=maezo:maezo /app/src/ ./src/
COPY --from=builder --chown=maezo:maezo /app/config/ ./config/
COPY --from=builder --chown=maezo:maezo /app/pyproject.toml ./
# alembic.ini — necessário para o Job de migrations (`alembic upgrade head`); script_location
# aponta para src/maezo/platform/migrations (relativo a /app).
COPY --chown=maezo:maezo alembic.ini ./

USER 1000

# Portas expostas:
#   8000 — agent-runtime / gateway / fhir-sync (HTTP health + API)
#   8080 — webhook-receiver
EXPOSE 8000 8080

# CMD padrão: agent-runtime (W-R0/R1).
# Helm sobrescreve `command:` por componente — ver comentário no topo.
CMD ["python", "-m", "maezo.runtime.agent_runtime"]

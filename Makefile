.PHONY: setup lint type test test-integration evals validate-artifacts validate-signoff \
        check-bpmn-error-allowlist \
        deploy-artifacts dev-stack dev-observability tf-validate localstack-up tf-smoke helm-lint

LOCALSTACK_COMPOSE := deploy/terraform/localstack/docker-compose.localstack.yml
TF_SMOKE_ENV       := deploy/terraform/envs/staging-sa-east-1

setup:            ## instala deps de dev
	uv sync --extra dev

lint:
	uv run ruff check src tests && uv run ruff format --check src tests

type:
	uv run mypy

test:             ## unit + invariantes de arquitetura (rapido, sem engine)
	uv run pytest tests/ -q

test-integration: ## contra engine real (docker compose up antes)
	uv run pytest tests/integration -q -m integration

evals:            ## golden datasets por agente (gate de promocao de prompt/modelo)
	uv run pytest tests/evals -q -m eval || [ $$? -eq 5 ]  # exit 5 = nenhum eval coletado ainda (scaffold); vira erro quando o 1o golden dataset entrar

validate-artifacts: ## BPMN/DMN/policies/agent-definitions (blocker de CI)
	# T2.1: validate_artifacts() is now a real, fail-closed gate (XML/YAML parsing,
	# BPMN<->DMN cross-refs, orphan-DMN allowlist, agent.yaml schema + MCP-server
	# allowlist) — see src/maezo/platform/validation/{bpmn,dmn,policy,agent_def,
	# crossref}.py. Third path repointed from src/maezo/agents to spec/agents:
	# spec/ is the single source of truth (per ADR) for every artifact family this
	# gate checks, and T0.3 (B14) is repointing every other consumer the same way —
	# src/maezo/agents/<id>/ will hold only graph.py once T0.3 lands. The validator
	# itself classifies a directory by its on-disk *structure* (bpmn/dmn
	# subdirectories, an autonomy/ subdirectory, or per-item agent.yaml files), not
	# by name, so it works against either location.
	# T0.3 × T0.4 reconciliation: spec/ is the single source of truth — spec/agents
	# replaced src/maezo/agents (T0.3 moved agent.yaml definitions there); the
# src/maezo/processes and src/maezo/policies paths never existed (T0.4). Pointer
# repair only. Real fail-closed validation since T2.1; orphans governed by
# spec/processes/dmn/orphans-allowlist.yaml.
	uv run python -m maezo.platform.validation.cli validate spec/processes spec/policies spec/agents

validate-signoff: ## gate de promocao de conteudo: artefato promovivel exige sign-off humano (Track C2)
	uv run python -m maezo.platform.validation.cli signoff

check-bpmn-error-allowlist: ## ADR-0030 §2: prova que todo WorkerBpmnError raised e um boundary code consumption-covered no spec (gate boundary-proof)
	# Computa de spec/** o conjunto de (topic, errorCode) em external tasks e verifica cada
	# `raise WorkerBpmnError(code)` dos workers contra o criterio consumption-covered (b1/b2).
	# FALHA em raise nao-coberto/nao-catalogado; clausula (c) dead-model e warn-only no Tier-0..2
	# (F5) — `--strict-dead-models` endurece para FALHA no fecho do Tier-3.
	uv run python scripts/ci/check_bpmn_error_allowlist.py

deploy-artifacts: ## deploy spec/processes/{bpmn,dmn} no engine CIB Seven (idempotente; requer `make dev-stack` de pe)
	# T1.3: POST /deployment/create multipart (enable-duplicate-filtering + deploy-changed-only)
	# contra o `cibseven` do docker-compose.yml (ENGINE_REST_URL, default http://localhost:8080/engine-rest).
	# spec/ e a fonte unica de verdade — deploya DIRETO de spec/processes/, nunca copia artefato
	# para outro lugar. Fail-closed: qualquer rejeicao do engine (BPMN/DMN invalido, HTTP nao-2xx)
	# sai non-zero com o corpo de erro do engine verbatim — ver src/maezo/platform/deploy/engine_deploy.py.
	uv run python -m maezo.platform.deploy

dev-stack:
	docker compose --profile core up -d

dev-observability: ## core + otel-collector + prometheus + grafana
	docker compose --profile core --profile observability up -d

tf-validate:      ## terraform validate em todos os modulos e envs
	@for dir in deploy/terraform/modules/*/; do \
		echo "Validating $$dir ..."; \
		cd "$$dir"; terraform init -backend=false -input=false -no-color > /dev/null; terraform validate; cd - > /dev/null; \
	done
	@for dir in deploy/terraform/envs/*/; do \
		echo "Validating $$dir ..."; \
		cd "$$dir"; terraform init -backend=false -input=false -no-color > /dev/null; terraform validate; cd - > /dev/null; \
	done

localstack-up:    ## sobe LocalStack + seed da substrate amh (CI-only; requer docker)
	docker compose -f $(LOCALSTACK_COMPOSE) up -d --wait localstack
	docker compose -f $(LOCALSTACK_COMPOSE) run --rm seed

tf-smoke:         ## plan de staging-sa-east-1 contra LocalStack (resolucao de data-sources; CI-only, requer docker+terraform)
	@echo "Bringing up LocalStack (edge) ..."
	docker compose -f $(LOCALSTACK_COMPOSE) up -d --wait localstack
	@echo "Seeding amh substrate stubs (VPC/subnets/EKS/OIDC/MSK-secret/state-backend) ..."
	docker compose -f $(LOCALSTACK_COMPOSE) run --rm seed
	@echo "Rendering LocalStack provider override into $(TF_SMOKE_ENV) ..."
	cp deploy/terraform/localstack/provider_override.tf.tmpl $(TF_SMOKE_ENV)/localstack_override.tf
	@echo "terraform init + plan (data-source resolution only; no apply) ..."
	@root="$(CURDIR)"; cd $(TF_SMOKE_ENV); \
		terraform init -backend=false -input=false -no-color > /dev/null && \
		terraform plan -input=false -no-color -lock=false \
			-var-file="$$root/deploy/terraform/localstack/smoke.staging.tfvars"; \
		status=$$?; \
		rm -f localstack_override.tf; \
		cd "$$root"; \
		docker compose -f $(LOCALSTACK_COMPOSE) down -v > /dev/null 2>&1 || true; \
		[ $$status -eq 0 ] && echo "tf-smoke OK — data sources resolved against the seeded substrate."; \
		exit $$status

helm-lint:        ## helm lint do chart maezo-tenant (requer helm >=3.15)
	helm lint deploy/helm/maezo-tenant/ --strict
	helm lint deploy/helm/maezo-tenant/ --strict -f deploy/helm/maezo-tenant/values-amh.yaml

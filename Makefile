.PHONY: setup lint type test test-integration evals validate-artifacts validate-signoff \
        dev-stack dev-observability tf-validate localstack-up tf-smoke helm-lint

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
	# T0.3 × T0.4 reconciliation: spec/ is the single source of truth — spec/agents
	# replaced src/maezo/agents (T0.3 moved agent.yaml definitions there); the
	# src/maezo/processes and src/maezo/policies paths never existed (T0.4). Pointer
	# repair only — validate_artifacts() in cli.py is unchanged and still fail-soft
	# during greenfield; hardening the gate itself is T2.1.
	uv run python -m maezo.platform.validation.cli validate spec/processes spec/policies spec/agents

validate-signoff: ## gate de promocao de conteudo: artefato promovivel exige sign-off humano (Track C2)
	uv run python -m maezo.platform.validation.cli signoff

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

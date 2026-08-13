.PHONY: setup lint type test test-integration evals validate-artifacts validate-signoff \
        check-bpmn-error-allowlist check-start-process-fence effect-chokepoint-fence \
        verify-amh-contract-pin \
        xfail-census-check xfail-census-write \
        deviation-expiry-check \
        release-floor-check release-floor-write \
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

check-start-process-fence: ## T3.4 F1: nenhuma chamada direta a start_process_instance fora do allowlist da fence (ADR-0007/T-C2)
	# AST-scan repo-wide de src/maezo: `start_process_idempotent` (mcp_cibseven/transport.py:1052)
	# e o UNICO chokepoint de start de processo sancionado (emit-before-effect + idempotencia por
	# business_key). Uma chamada direta a `transport.start_process_instance(...)` (ou um POST
	# hand-rolled a /process-definition/key/{key}/start) fora do allowlist pinado (a propria
	# transport.py + o decorator cibseven_engine.py) falha o gate, apontando para a fence.
	uv run python scripts/ci/check_start_process_fence.py

effect-chokepoint-fence: ## Onda 1 design §8: chokepoint de efeitos INEVITAVEL — construcao crua/import de politica/duplo de teste fora do registry falha o gate
	# AST-scan repo-wide de src/maezo (design §8.1-8.4) + completude contra o catalogo/manifesto
	# (§8.5): construcao das 15 classes de efeito cruas (CibSevenHttpTransport, FhirServer,
	# WhatsAppServer, InferenceProvider, DelegationDispatcher, ...) fora de gateway/tool_registry.py
	# + gateway/seams/*.py; cliente httpx cru ou literal de REST-path de efeito fora dos 5 modulos
	# de transporte legitimos; import do plano de politica (transportes concretos) ou leitura de
	# MAEZO_SPEC_DIR/MAEZO_ACTION_APPROVALS_PATH/MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT
	# fora de maezo/gateway (+ agents/__init__.py); duplo de teste Fake*/*Mock*/Noop* alcancavel de
	# uma raiz de composicao de producao. O gate tambem prova nao-vacuidade: o registry constroi
	# toda classe fenced, todo tool id de agent.yaml resolve no catalogo fechado (excecao unica e
	# disclosed mcp-memory.read_write), catalogo<->manifesto batem, e as 15 classes tem forma de
	# recusa declarada + teste de mutacao (ladder do design §6.1). O fence de start-process
	# (T3.4 F1) permanece separado e inalterado — protege um invariante diferente.
	uv run python scripts/ci/check_effect_chokepoint_fence.py

verify-amh-contract-pin: ## ADR-0037 XRD-04 (MZO-010/XRG-3): pin imutavel do contrato AMH intacto, completo e nao-regressivo
	# XRD-04 verbatim: "Digest divergente, schema ausente, topico errado ou versao rebaixada falham
	# fail-closed antes de merge/deploy de adapter." A AMH e a UNICA dona dos schemas canonicos; este
	# repo guarda so o pin (config/integrations/amh/contracts.lock.json) + as fixtures publicadas pela
	# AMH, gated por digest em tests/contract/amh/fixtures/. O gate recomputa cada sha256 vendorizado,
	# recusa arquivo nao-listado, e confere catalogo congelado (3 topicos + quarentena, 5 artefatos,
	# 3 version-IDs Glue, envelope de 28 campos na ordem congelada, vocabulario source_product fechado).
	# stdlib-only e SEM rede — verifica bytes que ja estao na arvore. Modos de steward (--candidate,
	# --manifest) em `python scripts/ci/verify_amh_contract_pin.py --help`.
	uv run python scripts/ci/verify_amh_contract_pin.py

xfail-census-check: ## Onda 0 §0.8: censo de strict-xfail GERADO bate com docs/xfail-census.json + PLANS.md (gate de drift)
	# Re-deriva do AST de tests/integration/processes/ todo marcador `@pytest.mark.xfail(reason=_*_REASON,
	# strict=True)`, classifica cada um pelo nome da constante `_*_REASON` contra o mapa comitado
	# (constante SEM entrada = FALHA; entrada apontando p/ constante que nao existe mais = FALHA), e
	# compara o total/distribuicao/breakdown contra docs/xfail-census.json E a regiao gerenciada de
	# PLANS.md (§0.5.3, entre marcadores <!-- xfail-census:*:begin/end -->). Drift em qualquer um -> FALHA
	# com diff preciso. Corrige o achado W5 do §0.8 (censo real 22 != PLANS 24 != handoff.yaml na mesma
	# semana) tornando o numero um artefato gerado, nunca mais recontado a mao.
	uv run python scripts/ci/generate_xfail_census.py --check

xfail-census-write: ## Onda 0 §0.8: regenera docs/xfail-census.json + a regiao gerenciada de PLANS.md
	uv run python scripts/ci/generate_xfail_census.py --write

deviation-expiry-check: ## PLANS §0.8 (2a leva Q-2/Q-10): desvio de sombra ratificado nao pode passar do prazo dele
	# O dono ratificou em 2026-08-13 que os dois `shadow` sobreviventes seguem em sombra COM owner
	# nomeado, prazo e criterios ("sombra sem dono e sem prazo e desvio permanente disfarcado").
	# Prazo escrito em prosa nao tem como notar que venceu; este gate le os blocos `deviation` do
	# spec/policies/autonomy/action-approvals.yaml (pelo LOADER, nunca por YAML cru) e reprova TODO
	# PR a partir do dia seguinte ao prazo enquanto o valor continuar `shadow`. Tambem reprova bloco
	# AUSENTE ou MALFORMADO com valor em shadow (apagar o prazo nao pode ser o caminho barato), e
	# avisa alto nos 14 dias que antecedem. Valor virado para `enforcing` => isento (desvio acabou).
	# SEM RENOVACAO SILENCIOSA: sair do vermelho e ou virar o valor, ou o dono re-ratificar um desvio
	# NOVO E DATADO em PR de dados sob CODEOWNERS (disciplina Q-1) — e esse PR e verde por construcao,
	# porque o gate le as datas da arvore em teste. `--today YYYY-MM-DD` simula qualquer data.
	uv run python scripts/ci/check_deviation_expiry.py

release-floor-check: ## Audit §5 (W-fillers): candidato nao pode ficar ABAIXO do floor de capacidade de release comitado (gate de regressao)
	# Composto de verdades JA GERADAS (nao um numero novo mantido a mao): total do censo de
	# strict-xfail (docs/xfail-census.json) nao pode SUBIR; o subconjunto P0/P1/P2 (automatable,
	# hoje 0/0/0) nao pode subir INDEPENDENTE do total — e o que impede um override de esconder uma
	# regressao P0 atras de uma melhora total nao relacionada (audit §5); contagem de testes unitarios
	# passando (mesma invocacao de `make test`) nao pode CAIR; e o conjunto de fences ja cabeados
	# (check-bpmn-error-allowlist/check-start-process-fence/effect-chokepoint-fence/
	# verify-amh-contract-pin/xfail-census-check) que passava no floor comitado precisa continuar
	# passando. Semantica de FLOOR (nao de ledger exato como o censo): FALHA so quando o candidato fica
	# ABAIXO do floor em alguma dimensao; PASSA em hold exato ou melhora genuina. Self-check de
	# nao-vacuidade proprio roda primeiro (prova que o comparador consegue FALHAR e consegue PASSAR)
	# antes de confiar em qualquer medicao real. `--write` (release-floor-write) refaz o floor
	# comitado a partir da arvore atual — recusa escrever se algum fence estiver falhando ou a suite
	# unitaria estiver vermelha.
	uv run python scripts/ci/generate_release_floor.py --check

release-floor-write: ## Audit §5 (W-fillers): regenera docs/release-capability-floor.json a partir da arvore atual
	uv run python scripts/ci/generate_release_floor.py --write

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

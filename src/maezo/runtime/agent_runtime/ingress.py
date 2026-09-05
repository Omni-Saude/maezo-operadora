"""Porta de entrada do agente: o caminho que faltava para um turno acontecer.

O QUE ISTO RESOLVE

Medido em 19/08/2026: `Harness.invoke()` — a API que executa um turno — existia e era
chamada em exatamente dois lugares: um exemplo de docstring e um teste unitário. Nada em
produção. O daemon do agente subia, compilava o grafo e parava, dizendo isso no próprio
log (`agent_graph_execution_not_performed_here`). Consequência: `llm_token_usage` = 0 em
todos os log groups, e o passo "preparar dossiê" do SP-OP-AUTH-001 era atendido por um
worker determinístico que devolvia `assigned_to: "rafael"` como RÓTULO.

Ou seja: o agente não estava lento nem quebrado. Não havia por onde chamá-lo.

A ARQUITETURA QUE ESTE MÓDULO RESPEITA

O Rafael NÃO é um passo dentro do processo. O grafo dele é
`receive → gather → assess → (auto_approve | human_auditor) → start_process → complete`:
ele recebe a solicitação, enriquece com FHIR, avalia e **inicia** o SP-OP-AUTH-001. É a
porta de entrada; o processo é o trilho de governança. O comentário em
`agents/rafael/graph.py` nomeia os dois chamadores previstos — "A2A delegation, the
portal-TISS worker" — e `RafaelState.canal` declara `a2a | portal_tiss`.

Este módulo implementa o segundo: o ingresso `portal_tiss`. Não implementa o A2A porque
esse exige um agente de origem atuando, e a dependência seria circular no estado atual.

POR QUE O CONTRATO NÃO É REDECLARADO AQUI

O corpo do POST é um dict e a validação é `new_rafael_state`, que já é o portão tipado do
grafo (`RAFAEL_INPUT_FIELDS`, com guarda de completude que falha em tempo de IMPORT se um
campo novo do estado não for classificado). Declarar os 20 campos de novo num modelo
Pydantic criaria duas fontes de verdade que divergem no primeiro campo adicionado — e a
divergência apareceria como "o agente ignora o que eu mandei", que é o pior modo de falha.

O construtor ESTRITO é deliberado: chave desconhecida levanta. Este ingresso é interno
(o Canal de Teste, atrás do Cloudflare Access), então uma chave estranha é bug nosso e
deve falhar alto, não ser tolerada.

DUAS IDENTIDADES, E ELAS NÃO SÃO A MESMA

  - `business_key` = `AUTH-{tenant}-{guia}` — vai para o CIB Seven. Legível, buscável pelo
    número da guia no Cockpit, e é o que dá idempotência ao start do processo.
  - `thread_id`    = `AUTH-hk1_{hmac}` — vai para o checkpoint do LangGraph.

A segunda existe porque `assert_phi_safe_thread_id` EXIGE um pseudônimo com chave, e a
primeira não tem: medido, `AUTH-amh-12345678` é RECUSADO com `ValueError`. Como as tabelas
de checkpoint carregam PHI e são indexadas por `thread_id`, o número da guia — que liga ao
beneficiário — não pode ser a chave. Sem esta separação, a PRIMEIRA invocação real falharia
e pareceria "o agente não funciona".

SEM AUTENTICAÇÃO PRÓPRIA, e dito aqui para não virar falsa segurança: esta rota escuta na
porta 8000 dentro da VPC, alcançável só pelo Security Group das tasks. A fronteira é o
Cloudflare Access na borda (quem chega ao Canal) e o SG na rede — a mesma postura já
registrada para o `engine-rest`. Exigir credencial aqui é trabalho próprio, e atinge o
Canal junto.

O INGRESSO É ESCOPADO POR AGENTE, E ISSO É INVARIANTE DE CÓDIGO (NEW-02)

Tudo o que este módulo monta é o contrato de UM agente: o corpo do POST é validado por
`new_rafael_state`, a `business_key` é `AUTH-{tenant}-{guia}` e a lista de campos
obrigatórios é a do formulário TISS. Até 05/09/2026 nada olhava para o `agent_id` do
daemon — `service.py` montava a rota sob a única condição `settings.agent_ingress_enabled`.
Medido no mesmo dia, com `agent_id="helena"`: `POST /v1/autorizacoes` respondeu **200** e
entregou um estado com forma de Rafael ao `harness.invoke()` da Helena.

O único freio existente era uma convenção de IaC
(`deploy/aws-ecs/envs/dev-sa-east-1/service-agents.tf` liga a flag com
`each.key == "rafael" ? "1" : "0"`). Convenção de operação não é invariante: um edit de
Terraform, um override local ou uma variável de ambiente manual bastavam.

O invariante agora vive aqui, em `INGRESS_BY_AGENT`: **o daemon de um agente só recebe rota
de ingresso se aquele agente DECLARAR o canal no `spec/agents/<id>/agent.yaml` e tiver a
entrada correspondente neste registro** — e as duas metades são amarradas por uma cerca de
paridade sobre os 11 `agent.yaml`
(`tests/unit/runtime/test_agent_ingress_agent_scoped.py`). Qualquer outro `agent_id` é
RECUSADO por `require_ingress_spec_or_fail_closed` com `IngressNotDeclaredForAgentError`,
antes de a primeira rota existir. A comparação é de igualdade EXATA — sem `strip()`, sem
`lower()`, sem prefixo — pela mesma razão que `AGENT_RUNTIME_MODE` não normaliza `" local "`:
normalizar um identificador de governança converte erro de configuração em acerto silencioso.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from maezo.agents.rafael.graph import new_rafael_state
from maezo.platform.privacy.key_scrubber import egress_pseudonymizer, scrub_key_value

if TYPE_CHECKING:  # pragma: no cover - só para tipo; evita import circular com service.py
    from maezo.runtime.agent_runtime.service import AgentState

logger = structlog.get_logger(__name__)

#: Prefixo e caminho da rota, separados porque o `APIRouter` precisa dos dois e a declaração
#: do spec (`ingress.rota`) precisa da junção. UMA definição, três leitores.
PREFIXO_DA_ROTA: Final[str] = "/v1"
CAMINHO_DA_ROTA: Final[str] = "/autorizacoes"
ROTA_DECLARADA: Final[str] = f"POST {PREFIXO_DA_ROTA}{CAMINHO_DA_ROTA}"

#: Campos de texto que o ingresso EXIGE preenchidos.
#:
#: O CRITÉRIO PARA ESTAR NESTA LISTA (e não é "todo campo do contrato"):
#: um campo entra aqui quando a AUSÊNCIA dele produz uma SUPOSIÇÃO. Fica de fora quando a
#: ausência produz um valor que já falha seguro.
#:
#: Medido em 24/08/2026, e foi o que motivou a lista crescer de dois campos para cinco:
#:
#:   - `carater_atendimento` ausente vira `"eletivo"` — o prazo LONGO. Um caso de urgência
#:     que chegue sem o campo ganha 5 dias úteis em vez de 2 horas, e ninguém é avisado.
#:     É a falha mais cara da lista, porque é regulatória (RN 259) e silenciosa.
#:   - `categoria_procedimento` ausente vira `"consulta"` — e o prazo é calculado como se
#:     fosse consulta. Foi exatamente o que aconteceu no teste de 19/08: a DMN de SLA
#:     "rodou sem significado", devolvendo a regra genérica.
#:   - `requer_autorizacao` ausente vira `True` (ver `CAMPOS_BOOLEANOS_OBRIGATORIOS`).
#:
#: Ficam FORA, de propósito: `documentacao_completa`, `beneficiario_ativo` e
#: `carencia_cumprida`. Os três já assumem `False` quando ausentes — o valor DESFAVORÁVEL,
#: que roteia para análise humana. Exigi-los agora também cimentaria o contrato errado: eles
#: são os fatos que o worker de apuração deve produzir, e quem chama não deveria enviá-los.
CAMPOS_OBRIGATORIOS: tuple[str, ...] = (
    "tenant_id",
    "numero_guia_tiss",
    "codigo_procedimento_tuss",
    "categoria_procedimento",
    "carater_atendimento",
)

#: Booleanos que precisam CHEGAR, e cuja ausência não pode ser lida como um valor.
#:
#: Checado por `is not bool`, NUNCA por veracidade: `False` é resposta legítima e um teste de
#: veracidade o trataria como ausente. `requer_autorizacao` está aqui porque o default é
#: `True` — o valor que MANDA o caso seguir para análise. Um pedido que não requer
#: autorização e chega sem o campo entra no fluxo inteiro à toa.
CAMPOS_BOOLEANOS_OBRIGATORIOS: tuple[str, ...] = ("requer_autorizacao",)

#: Campos de saída que a resposta devolve. Lista fechada de propósito: devolver o estado
#: inteiro exporia campos internos do grafo e faria a resposta mudar de forma a cada nó
#: novo — quem consome (o Canal, e depois um portal) precisa de contrato estável.
CAMPOS_DE_RESPOSTA: tuple[str, ...] = (
    "business_key",
    "route",
    "admissibilidade",
    "recomendacao_auto",
    "desfecho",
    "motivo_auditor",
    "sla_analise",
    "dossier",
    "dmn_refs",
    "process_started",
    "process_ref",
    "error",
)


@dataclass(frozen=True)
class IngressSpec:
    """O contrato de ingresso de UM agente — tudo o que a rota precisa saber sobre ele.

    Guardar o construtor de estado AQUI (e não importá-lo direto no corpo da rota) é o que
    torna a rota agnóstica: `build_ingress_router` deixa de conhecer o Rafael e passa a
    conhecer "o agente declarante". Um segundo agente com canal próprio ganha a sua entrada
    com o SEU construtor e a SUA lista de campos — não um `if agent_id == ...` no meio do
    handler, que é como um caminho de ingresso vira dois contratos numa função só.
    """

    agent_id: str
    #: `RafaelState.canal` declara `a2a | portal_tiss`; este é o segundo.
    canal: str
    #: Como a declaração aparece no `agent.yaml` do agente (a cerca de paridade compara).
    rota: str
    #: Portão tipado do grafo: chave desconhecida LEVANTA (`ValueError` -> 422).
    montar_estado: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    campos_obrigatorios: tuple[str, ...]
    campos_booleanos_obrigatorios: tuple[str, ...]
    campos_de_resposta: tuple[str, ...]


#: OS AGENTES QUE DECLARAM CANAL DE INGRESSO HTTP. Hoje: só o Rafael (`portal_tiss`).
#:
#: `MappingProxyType` e não `dict`: um registro de governança que pode receber entrada em
#: runtime (um plugin, um teste que esquece de desfazer) deixa de ser o allowlist que este
#: módulo afirma ser. Escrever nele levanta `TypeError`.
#:
#: PARA HABILITAR UM NOVO AGENTE são necessários os DOIS lados, e a cerca de paridade
#: (`tests/unit/runtime/test_agent_ingress_agent_scoped.py`) recusa qualquer um sozinho:
#:   1. `ingress: {canal, rota}` no `spec/agents/<id>/agent.yaml` DELE (spec-first);
#:   2. a entrada aqui, com o construtor de estado e os campos do contrato DELE.
INGRESS_BY_AGENT: Final[Mapping[str, IngressSpec]] = MappingProxyType(
    {
        "rafael": IngressSpec(
            agent_id="rafael",
            canal="portal_tiss",
            rota=ROTA_DECLARADA,
            montar_estado=new_rafael_state,
            campos_obrigatorios=CAMPOS_OBRIGATORIOS,
            campos_booleanos_obrigatorios=CAMPOS_BOOLEANOS_OBRIGATORIOS,
            campos_de_resposta=CAMPOS_DE_RESPOSTA,
        ),
    }
)


class IngressNotDeclaredForAgentError(RuntimeError):
    """Recusa FAIL-CLOSED: este `agent_id` não declara canal de ingresso (NEW-02).

    `RuntimeError` pelo mesmo motivo que `_require_signer_or_fail_closed` e os outros
    `_require_*_or_fail_closed` das raízes de composição usam: a recusa é do MESMO tipo, e
    quem isola um bloco de bring-up não precisa aprender uma hierarquia nova por capacidade.
    """

    def __init__(self, agent_id: str, declarados: Sequence[str]) -> None:
        self.agent_id = agent_id
        self.declarados: tuple[str, ...] = tuple(declarados)
        super().__init__(
            f"o agente {agent_id!r} nao declara canal de ingresso HTTP, entao nao recebe rota "
            f"alguma (NEW-02). Declaram hoje: {list(self.declarados)}. O corpo desta rota e' "
            "validado pelo contrato do agente DECLARANTE, entao monta-la no daemon de outro "
            "agente entregaria um estado de forma alheia ao grafo dele. Para habilitar um novo "
            "agente: declare `ingress:` no `spec/agents/<id>/agent.yaml` DELE e acrescente a "
            "entrada correspondente em `INGRESS_BY_AGENT` — a cerca de paridade exige os dois."
        )


def require_ingress_spec_or_fail_closed(agent_id: str) -> IngressSpec:
    """O contrato de ingresso deste `agent_id`, ou a recusa tipada. NÃO devolve `None`.

    O ponto ÚNICO em que o escopo por agente é decidido: a rota (`build_ingress_router`) e o
    boot (`agent_runtime/service.py::mount_ingress_if_declared`) passam os dois por aqui, para
    que não exista uma segunda leitura do registro que possa divergir desta.

    Igualdade EXATA, sem `strip()`/`lower()`: ver o docstring do módulo.
    """
    spec = INGRESS_BY_AGENT.get(agent_id)
    if spec is None:
        logger.error(
            "agent_ingress_refused_undeclared_agent",
            agent_id=agent_id,
            agentes_declarados=sorted(INGRESS_BY_AGENT),
            rota_nao_montada=ROTA_DECLARADA,
            motivo="o agente nao declara `ingress:` no seu agent.yaml (spec-first, NEW-02)",
        )
        raise IngressNotDeclaredForAgentError(agent_id, sorted(INGRESS_BY_AGENT))
    return spec


def build_ingress_router(state: AgentState) -> APIRouter:
    """Monta a rota de ingresso lendo o `AgentState` do daemon.

    RECUSA ANTES DE CRIAR QUALQUER ROTA quando o `agent_id` do daemon não declara canal
    (NEW-02): a primeira linha é o portão, então "recusado" significa ZERO rota montada e não
    uma rota que responde erro — uma rota que existe já é superfície, já aparece no roteador e
    já pode ser alcançada por quem estiver dentro do Security Group.

    O `agent_id` vem de `state.settings.agent_id` e NÃO de um parâmetro próprio, de propósito:
    a identidade do daemon tem uma fonte só. Um segundo canal para informá-la permitiria a
    quem monta declarar um id diferente do que o daemon serve — que é exatamente a classe de
    defeito que NEW-02 descreve, reintroduzida um nível acima.

    O estado é lido a cada chamada, não capturado: o daemon sobe o servidor HTTP no STEP A,
    ANTES de as dependências existirem (para `/healthz` responder de imediato). Se a rota
    capturasse o harness na montagem, ela guardaria `None` para sempre.
    """
    spec = require_ingress_spec_or_fail_closed(state.settings.agent_id)
    router = APIRouter(prefix=PREFIXO_DA_ROTA, tags=["ingresso"])

    @router.post(CAMINHO_DA_ROTA)
    async def receber_solicitacao(payload: dict[str, Any]) -> JSONResponse:
        """Recebe uma solicitação de autorização e EXECUTA um turno do agente."""
        harness = state.harness
        if harness is None:
            # 503 e não 500: é indisponibilidade temporária de bring-up, e um balanceador
            # deve poder distinguir "ainda não" de "quebrado".
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "o agente ainda não terminou o bring-up (harness ausente) — "
                    "consulte /readyz para ver qual dependência falta"
                ),
            )

        faltando = [c for c in spec.campos_obrigatorios if not str(payload.get(c) or "").strip()]
        # Booleano ausente NÃO é o mesmo que booleano falso, e a diferença é o ponto: sem esta
        # checagem separada, `requer_autorizacao=False` seria lido como "não veio" e o default
        # `True` entraria por cima — invertendo a resposta de quem chamou.
        faltando += [c for c in spec.campos_booleanos_obrigatorios if not isinstance(payload.get(c), bool)]
        if faltando:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"campos obrigatórios ausentes, vazios ou de tipo errado: {sorted(faltando)}. "
                    "Esta rota não assume valor por campo faltante: um default aqui vira prazo "
                    "regulatório errado sem ninguém perceber."
                ),
            )

        try:
            entrada = spec.montar_estado(payload)
        except ValueError as exc:
            # O construtor estrito recusa chave desconhecida. 422 com a mensagem dele: ela
            # nomeia exatamente quais chaves sobraram, que é o que quem chamou precisa saber.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        tenant = str(entrada.get("tenant_id", ""))
        guia = str(entrada.get("numero_guia_tiss", ""))
        business_key = f"AUTH-{tenant}-{guia}"
        thread_id = scrub_key_value(business_key, egress_pseudonymizer())

        # `numero_guia_tiss` NÃO entra no log: liga ao beneficiário. O que entra é o
        # thread_id já pseudonimizado, que é o suficiente para correlacionar dois turnos da
        # mesma guia sem carregar a guia.
        logger.info(
            "agent_ingress_turn_starting",
            agent_id=state.settings.agent_id,
            tenant_id=tenant,
            thread_id=thread_id,
            canal=entrada.get("canal") or spec.canal,
        )

        inicio = time.monotonic()
        try:
            resultado = await harness.invoke(dict(entrada), thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001 - a borda traduz qualquer falha em 502
            duracao_ms = int((time.monotonic() - inicio) * 1000)
            logger.error(
                "agent_ingress_turn_failed",
                agent_id=state.settings.agent_id,
                thread_id=thread_id,
                duracao_ms=duracao_ms,
                erro=str(exc)[:400],
                exc_info=True,
            )
            # 502 e não 500: o turno falhou dentro do grafo/dependências do agente, não no
            # tratamento da requisição. A distinção importa para quem depura pelo status.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"o turno do agente falhou: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc

        duracao_ms = int((time.monotonic() - inicio) * 1000)
        saida = {campo: resultado.get(campo) for campo in spec.campos_de_resposta if campo in resultado}

        logger.info(
            "agent_ingress_turn_completed",
            agent_id=state.settings.agent_id,
            thread_id=thread_id,
            duracao_ms=duracao_ms,
            route=saida.get("route"),
            desfecho=saida.get("desfecho"),
            process_started=saida.get("process_started"),
        )

        return JSONResponse(
            content={
                "agent_id": state.settings.agent_id,
                "thread_id": thread_id,
                "duracao_ms": duracao_ms,
                "resultado": saida,
            }
        )

    return router

"""Todo falso de inferencia em `tests/` acompanha o Protocol real (CC-12 x integracao lote3).

DE ONDE VEM ESTE ARQUIVO (fleet audit, WP lote3-integration-fakes-task-kind, 04/09/2026)

O job CI `integration tests (real engine)` do PR #319 (run 33927690960, branch
`fleet/train-w3-lote3`) deu `5 failed, 499 passed`, as 5 falhas TODAS em
`tests/integration/agents/test_helena_escalation.py` (dois falsos: `_FakeInference:96`,
`_RaisingInference:323`) --
`TypeError: _FakeInference.generate() got an unexpected keyword argument 'task_kind'`.
Os falsos identicos em `test_marina_contas_dossier.py:55` e `test_rafael_auth_dossier.py:54`
(mesmos arquivos que o CI de fato coletou e rodou nesse run) NAO falharam nesta execucao
especifica -- seus fluxos naquele run nao exercitam `.generate(task_kind=...)` -- mas
compartilham a mesma classe de defeito e foram corrigidos preventivamente pela mesma cerca,
junto com os demais 15 falsos abaixo.

Causa-raiz: CC-12 acrescentou `task_kind` ao Protocol da facade externa
(`runtime/inference::InferenceProvider.generate`, hoje
`(self, prompt, *, phi=False, agent_id=None, tenant_id=None, task_kind=None)`) e toda chamada
REAL `self._llm.generate(...)` dentro de um `graph.py` passou a declarar
`task_kind=` (cerca irma `test_llm_calls_declare_task_kind.py`). Os falsos acima foram escritos
ANTES de CC-12 e nunca atualizados -- exatamente a mesma especie do defeito `f1bc87f`
(`tests/unit/agents/test_gather_notes_no_raw_exception.py::_RecordingInference`, corrigido no
lane UNITARIO pelo integration-engineer-lote3b, que registrou como risco nao ter auditado "os
demais falsos" do repo). Esta cerca fecha esse risco: varre `tests/` inteiro por AST, nao so'
`tests/integration/`.

O QUE ESTA CERCA COBRE E O QUE NAO COBRE

`InferenceProvider` (a FACADE externa que os grafos chamam via `self._llm`/`self._inference`) e'
UM protocolo. Mas `runtime/inference/providers.py::BaseInferenceProvider` (o `_impl` que a
facade delega DEPOIS de resolver `phi`/`task_kind` -- `InferenceProvider.generate` linha ~624,
`return await self._impl.generate(prompt, agent_id=agent_id, tenant_id=tenant_id)`) e' OUTRO
protocolo, deliberadamente mais estreito: seu `generate` nunca recebe `phi` nem `task_kind`
(ambos sao resolvidos e consumidos pela propria facade ANTES de delegar -- ver o comentario
"ORDER IS THE INVARIANT (I-6)" no modulo). `tests/unit/runtime/test_inference_capabilities.py`
tem varios falsos desse segundo protocolo (`_ProbeChild`, `_ProbeSiblingModule`,
`WellFormedProvider`) que corretamente NAO declaram `phi` nem `task_kind` -- exigir os dois ali
seria falso-positivo, cercando o protocolo ERRADO.

O discriminador usado aqui e' a presenca de `phi` nos kwonly de `generate`: e' o marcador da
facade externa (o `_impl` categoricamente nunca o recebe, por construcao do modulo -- ver acima),
e um controle ADR-0006 dificil de remover por acidente. So' esse discriminador e' fixo; O QUE E'
EXIGIDO de cada falso identificado como facade e' lido de `inspect.signature(
InferenceProvider.generate)` em tempo de execucao -- nao hardcoded -- entao um kwonly NOVO que o
dono acrescente amanha ao Protocol real (alem de `task_kind`) quebra esta cerca imediatamente
para todo falso que nao o aceitar, sem precisar editar este arquivo.

Um falso com `**kwargs` (`async def generate(self, prompt, **_kwargs)`) sempre passa --
absorve qualquer kwonly futuro por construcao, entao nao ha' o que exigir dele.

DECISAO DE ESCOPO (autorizada pelo brief do WP): a varredura aponta falsos em `tests/unit/` que
hoje PASSAM (nenhum no atualmente exercitado por eles chama `.generate(task_kind=...)`) mas
ficariam quebrados no dia em que passassem a chamar -- mesma deriva do `f1bc87f`. Foram
corrigidos mecanicamente (kwarg `task_kind: str | None = None` acrescentado, nenhuma asserção
de comportamento alterada) em vez de deixados como debito, porque o proprio ponto desta cerca e'
fechar essa classe de defeito de uma vez, nao so' os 4 do CI. Ver `docs/evidence-ledger.md`
(linha `LOTE3-INTEGRATION-FAKES-TASK-KIND`) para a contagem exata e a lista arquivo:linha.

GENERALIZACAO P-17 (fleet hardening ciclo 2, WP PROTOCOL-FAKE-FENCES, 05/09/2026) -- NEW-12 /
REG-04 (citacao corrigida pelo §Delta -- ver "CORRECAO DE CITACAO (§Delta F4/F6)" no final desta
docstring; os 4 commits ja' landados e a linha do ledger antes desta correcao ainda citam
"NEW-C1-2" no texto/subject, id que nunca existiu em nenhuma fonte de evidencia)

ASSURANCE-F1-E.md (achado formalmente triado `NEW-12`) apontou que `grep -rn 'class .*Protocol'
src/maezo/runtime src/maezo/a2a` mostra Protocols alem de `InferenceProvider` sem NENHUMA
cerca de paridade de assinatura contra seus falsos em `tests/` -- so' `KafkaLike` tinha uma
(pino estreito de UMA classe em `test_outbox.py:267-274`, contra `PostgresOutboxFactProducer`,
uma classe de PRODUCAO, nao um falso de teste). ASSURANCE-F1-D.md (REG-04) confirmou DRIFT REAL,
hoje, em dois desses Protocols que o brief do WP tambem manda vigiar (`DmnTransport` e
`WhatsAppSender`, ambos fora do `runtime`/`a2a` da varredura acima -- vivem em
`tools/workers/dmn_transport.py` e em `agents/{helena,fernando,lucas}/graph.py`): `_FakeDmn.
evaluate(self, table, dmn_input)` contra o Protocol real `evaluate(self, decision_key,
variables, *, tenant=None)`, e `_RecordingWhatsApp.send(self, to, text)` contra o Protocol real
`send(self, to_hash, text)` -- os NOMES inteiros dos parametros divergem, nao so' um kwonly
faltando, entao o discriminador de `phi` da secao (A)/(B) acima (que so' funciona porque `phi`
SOBREVIVE ao drift) nao serve aqui: e' preciso comparar a assinatura INTEIRA (nomes posicionais
por posicao, nomes keyword-only por conjunto, presenca de default, sync/async), item por item.

A secao (C) abaixo generaliza para uma TABELA de 10 familias de Protocol (todas exceto
`InferenceProvider`, que fica com a cerca (A)/(B) porque o discriminador `phi` resolve um
problema que as outras 10 nao tem -- dois Protocols DIFERENTES compartilhando o MESMO nome de
metodo `generate`, um deliberadamente mais estreito que o outro): `DmnTransport`,
`WhatsAppSender`, `IdempotencyStore`, `TenantKeyset`, `FhirSummaryReader` (mesma FORMA de
`FhirReader`/`SummaryReader`/`PatientSummaryReader`-de-andre -- ver docstring de
`FhirSummaryReader` no proprio `a2a_composition.py`, que nomeia essas tres como "Protocols
DIFERENTES com membros IDENTICOS"), `BrRegionalTransport`, `OutboxClaimStore`,
`FactBrokerPublisher`, `AuditEmitter` (declarado duas vezes, `tools/workers/harness.py` e
`a2a/dispatcher.py` -- "estruturalmente identico" pelo proprio docstring do segundo) e
`KafkaLike`.

FORA DO ESCOPO, disclosed (nao e' o WP): as tres `PatientSummaryReader` de
`beatriz`/`marina`/`valentina/graph.py` sao uma FORMA DIFERENTE (metodo `read_patient_summary`,
nao `read_patient`) -- o brief fecha a lista como `FhirSummaryReader`/`FhirReader`, que e' a
forma `read_patient`; a outra forma tambem tem falsos em `tests/` (`test_dossier_admin_evals.
py::_LeakyPatientSummaryReader` etc.) mas fica para um WP futuro, nao renomeada nem fundida aqui
para nao expandir o escopo autorizado.

DESAMBIGUACAO DE NOME AMBIGUO (`send`): quatro Protocols da tabela declaram um metodo chamado
`send` (`WhatsAppSender`, `BrRegionalTransport`, `KafkaLike`, `FactBrokerPublisher`) -- o NOME
sozinho nao decide de qual Protocol um falso e' copia, e usar os NOMES DOS PARAMETROS para
decidir seria circular (sao exatamente os nomes que podem ter driftado). A cerca desambigua pela
FORMA (aridade posicional + contagem kwonly + `*args`/`**kwargs`), lida em tempo de execucao dos
Protocols reais, nunca hardcoded: hoje sao tres formas distintas -- `(2 posicionais, 0 kwonly)` e'
`WhatsAppSender`, `(1 posicional, 0 kwonly)` e' `BrRegionalTransport`, `(2 posicionais, 1 kwonly)`
e' `KafkaLike`/`FactBrokerPublisher` (as duas, EXPRESSAMENTE identicas por design -- ver docstring
de `FactBrokerPublisher`). Uma forma que nao bate com NENHuma das tres e' uma FALHA DE
COMPLETUDE (nunca ignorada em silencio): a cerca aponta que a tabela precisa de uma entrada nova
antes de prosseguir -- e' o "registro" que o brief pede ao lado da heuristica de nome.

`start`/`stop` (metodos de `FactBrokerPublisher` alem de `send`) NAO sao usados como ancora
independente: sao nomes genericos demais (`grep` acha 6 ocorrencias de cada em `tests/`,
a maioria fakes de `BridgeDlqPublisher`/`BridgeKafkaConsumer`/`RawKafkaProducer` --
Protocols de `platform/integrations/*.py` FORA desta tabela, com metodos `publish_dlq`/
`commit`+`__aiter__`/`send_and_wait` que nao colidem de nome com `FactBrokerPublisher`, mas cujo
`start`/`stop` tem a MESMA forma trivial `(self) -> None`). Ancorar em `start`/`stop` sozinhos
cercaria Protocols que nao sao desta tabela. Em vez disso, `start`/`stop` so' sao checados numa
classe que JA foi classificada como `FactBrokerPublisher` pela FORMA do seu `send` -- nunca
como gatilho proprio.

CORRECAO DE CITACAO (§Delta F4/F6, VERIFY-PROTOCOL-FAKE-FENCES.md)

O texto original (herdado do brief do WP, reproduzido fielmente pelo autor) citava
"ASSURANCE-F1-C1.md (achado novo, nao triado)" como a FONTE UNICA da analise "8 Protocols alem de
`InferenceProvider` sem cerca" e usava um segundo id, `NEW-C1-2`, ao lado de `NEW-12`/`REG-04`.
Verificado nesta correcao: `ASSURANCE-F1-C1.md` REALMENTE contem uma analise equivalente (secao
"New findings (nao triado)", item 2, dentro do bloco do id `CC-12`: "Signature-parity fences exist
for only 1 of 8 Protocols with test fakes ... The other 7 have zero") -- entao o conteudo citado
nao esta' factualmente errado. Mas esse item e' PROSA NAO NUMERADA de um apendice "nao triado",
sem NENHUM esquema de id proprio (`ASSURANCE-F1-C1.md` so' atribui id aos achados FORMALMENTE
triados, ex.: `CC-04`, `CC-12`, `CC-13`, `RAF-03`) -- `NEW-C1-2` foi INVENTADO (pelo brief do
orquestrador do WP, propagado fielmente pelo autor e ate' pelo brief do verificador desta mesma
sessao) e nao existe em `ASSURANCE-F1-C1.md`, `ASSURANCE-F1-E.md` nem em
`$S/snap/GAP-REGISTER.yaml` (grep, zero hits nos tres, confirmado nesta correcao). A fonte
FORMALMENTE triada e citavel, que chega a' MESMA conclusao (com uma varredura mais ampla, 15
Protocols em vez de 8, incluindo `src/maezo/tools`) e' `$S/phase1/ASSURANCE-F1-E.md`, bloco
`- id: NEW-12`. Esta correcao (§Delta) ajusta APENAS esta docstring e a celula de Evidencia da
linha do ledger para citar `NEW-12`/`ASSURANCE-F1-E.md` como fonte primaria (com
`ASSURANCE-F1-C1.md` mencionada como corroboracao secundaria, nao mais como origem de id); os
QUATRO commits ja' landados (`340fb102`/`ba875684`/`4aabe133`/`9e94852e`) e o subject/body deles
nao podem ser reescritos (historico append-only) e continuam citando "NEW-C1-2" -- divulgado, nao
corrigido, no relatorio desta reparo.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maezo.a2a.dispatcher import AuditEmitter as _DispatcherAuditEmitter
from maezo.a2a.dispatcher import KafkaLike
from maezo.a2a.idempotency import IdempotencyStore
from maezo.a2a.keyset import TenantKeyset
from maezo.a2a.outbox_relay import FactBrokerPublisher, OutboxClaimStore
from maezo.agents.andre.graph import PatientSummaryReader as _AndrePatientSummaryReader
from maezo.agents.carolina.graph import SummaryReader as _CarolinaSummaryReader
from maezo.agents.fernando.graph import WhatsAppSender as _FernandoWhatsAppSender
from maezo.agents.gustavo.graph import FhirReader as _GustavoFhirReader
from maezo.agents.helena.graph import WhatsAppSender as _HelenaWhatsAppSender
from maezo.agents.lucas.graph import WhatsAppSender as _LucasWhatsAppSender
from maezo.agents.rafael.graph import FhirReader as _RafaelFhirReader
from maezo.runtime.agent_runtime.a2a_composition import FhirSummaryReader
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.inference.br_regional import BrRegionalTransport
from maezo.tools.workers.dmn_transport import DmnTransport
from maezo.tools.workers.harness import AuditEmitter as _HarnessAuditEmitter

_RAIZ = Path(__file__).resolve().parents[3]
_TESTS_ROOT = _RAIZ / "tests"

#: Marcador de que um `generate()` de teste esta' duck-typing a FACADE externa
#: (`InferenceProvider`), nao o `_impl` interno -- ver docstring do modulo acima.
_MARCADOR_FACADE = "phi"


def _protocol_kwonly_names() -> tuple[str, ...]:
    """Nomes dos parametros keyword-only do Protocol REAL, lidos por `inspect.signature` --
    nunca hardcoded, para que um kwonly novo amanha quebre esta cerca sem editar o arquivo."""
    sig = inspect.signature(InferenceProvider.generate)
    return tuple(
        nome for nome, param in sig.parameters.items() if param.kind is inspect.Parameter.KEYWORD_ONLY
    )


_PROTOCOL_KWONLY = _protocol_kwonly_names()


def _kwonly_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {a.arg for a in fn.args.kwonlyargs}


def _achados_em_fonte(fonte: str, rotulo: str) -> list[str]:
    """Toda classe com um metodo `generate` que declara `phi` (marcador da facade) e NAO cobre
    todo `_PROTOCOL_KWONLY` (nem via `**kwargs`) -- devolve uma linha de achado por ocorrencia."""
    try:
        arvore = ast.parse(fonte)
    except SyntaxError:
        return []
    achados: list[str] = []
    for classe in (n for n in ast.walk(arvore) if isinstance(n, ast.ClassDef)):
        for item in classe.body:
            if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if item.name != "generate":
                continue
            kwonly = _kwonly_names(item)
            if _MARCADOR_FACADE not in kwonly:
                continue  # nao e' um duplo da facade (ex.: `_impl`/`BaseInferenceProvider`) -- fora do escopo
            tem_kwargs_catchall = item.args.kwarg is not None
            faltando = sorted(set(_PROTOCOL_KWONLY) - kwonly)
            if faltando and not tem_kwargs_catchall:
                achados.append(
                    f"{rotulo}:{item.lineno} — classe `{classe.name}` aceita {sorted(kwonly)} "
                    f"mas falta {faltando} do Protocol real "
                    "(`runtime/inference::InferenceProvider.generate`)"
                )
    return achados


def _achados_em_arquivo(caminho: Path) -> list[str]:
    return _achados_em_fonte(caminho.read_text(encoding="utf-8"), str(caminho.relative_to(_RAIZ)))


# =================================================================================================
# (A) Fence estrutural — AST sobre tests/ inteiro
# =================================================================================================


def test_falsos_de_inferencia_em_tests_aceitam_todo_kwonly_do_protocol_real() -> None:
    """Nenhum falso que duck-typa a facade `InferenceProvider` pode ficar sem um kwonly do
    Protocol real -- e' a mesma especie do defeito `f1bc87f`: uma chamada real
    `self._llm.generate(..., task_kind=...)` levanta `TypeError` contra um falso desatualizado,
    o `except Exception` do no engole, e o sintoma vira "resposta vazia"/"prompt vazio" em vez de
    um erro de teste claro (ver `INTEGRATION-LOTE3B.md` §"Defeito de INTEGRACAO encontrado").
    """
    arquivos = sorted(_TESTS_ROOT.rglob("*.py"))
    assert arquivos, f"nenhum arquivo em {_TESTS_ROOT} — cerca sem alvo"

    ofensores: list[str] = []
    for caminho in arquivos:
        ofensores.extend(_achados_em_arquivo(caminho))

    assert not ofensores, (
        "falso de inferencia em tests/ desalinhado do Protocol real "
        "(`runtime/inference::InferenceProvider.generate`) — mesma especie do defeito f1bc87f:\n"
        + "\n".join(ofensores)
    )


# =================================================================================================
# (B) Controles do proprio discriminador — prova de que a cerca faz o que afirma
# =================================================================================================


def test_protocolo_real_tem_phi_e_task_kind_hoje() -> None:
    """Premissa: o Protocol real declara `phi` (o marcador usado para identificar um falso da
    facade) e `task_kind` (o kwonly cuja falta causou o defeito do PR #319) — se algum dia
    deixar de declarar um dos dois, esta premissa quebra ANTES da cerca principal, apontando
    direto para a causa (Protocol mudou), nao para "todo falso do repo esta errado"."""
    assert "phi" in _PROTOCOL_KWONLY
    assert "task_kind" in _PROTOCOL_KWONLY


def test_achados_recusa_falso_sem_task_kind_da_facade() -> None:
    """Sondagem direta do helper (sem depender de nenhum arquivo real de tests/ continuar
    quebrado no futuro): um falso com `phi` mas sem `task_kind` E' apontado."""
    fonte = (
        "class F:\n"
        "    async def generate(self, prompt, *, phi=False, agent_id=None, tenant_id=None):\n"
        "        return ''\n"
    )
    achados = _achados_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "sintetico.py:2" in achados[0]
    assert "task_kind" in achados[0]


def test_achados_aceita_falso_com_task_kind_nomeado_ou_kwargs_catchall() -> None:
    """As duas formas aceitas pelo brief: o kwarg `task_kind` nomeado, OU um `**kwargs`
    catch-all (o padrao de `tests/unit/gateway/seams/test_live_dispatch_wiring.py`)."""
    fonte_nomeado = (
        "class F:\n"
        "    async def generate(self, prompt, *, phi=False, agent_id=None, tenant_id=None,"
        " task_kind=None):\n"
        "        return ''\n"
    )
    fonte_kwargs = (
        "class F:\n    async def generate(self, prompt, *, phi=False, **_kwargs):\n        return ''\n"
    )
    assert _achados_em_fonte(fonte_nomeado, "sintetico.py") == []
    assert _achados_em_fonte(fonte_kwargs, "sintetico.py") == []


def test_achados_ignora_falso_do_impl_layer_sem_phi() -> None:
    """NEGATIVO: um falso do protocolo `_impl` (`BaseInferenceProvider`-style, sem `phi` nem
    `task_kind` — ex.: `test_inference_capabilities.py::_ProbeChild`) NAO e' exigido a declarar
    `task_kind`, porque nao e' a facade externa. Sem este controle, a cerca principal
    marcaria falso-positivo sobre um protocolo diferente por design (ver docstring do modulo)."""
    fonte = (
        "class ImplFalso:\n"
        "    async def generate(self, prompt, *, agent_id=None, tenant_id=None):\n"
        "        return ''\n"
    )
    assert _achados_em_fonte(fonte, "sintetico.py") == []


# =================================================================================================
# (C) Cerca generalizada P-17 -- tabela de 10 familias de Protocol (NEW-12 / NEW-C1-2 / REG-04)
# =================================================================================================


def _metodos_proprios(cls: type) -> dict[str, Any]:
    """(nome -> funcao) de cada metodo declarado DIRETAMENTE no corpo de `cls` -- nunca herdado
    (`vars`, nao `dir`/`getmembers`), excluindo dunders. Funciona igual para um Protocol real e
    para uma classe concreta: ambos guardam funcoes simples em `__dict__`."""
    return {
        nome: membro
        for nome, membro in vars(cls).items()
        if not nome.startswith("__") and inspect.isfunction(membro)
    }


def _forma_assinatura(sig: inspect.Signature) -> tuple[int, int, bool, bool]:
    """(nº de parametros posicionais exigiveis por POSICAO -- exclui `self`, nº de keyword-only,
    tem `*args`, tem `**kwargs`) -- a FORMA de uma assinatura `inspect.Signature`, usada para
    desambiguar `send` sem depender dos NOMES dos parametros (ver docstring do modulo, secao
    "DESAMBIGUACAO DE NOME AMBIGUO")."""
    params = [p for nome, p in sig.parameters.items() if nome != "self"]
    posicionais = sum(
        1
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    kwonly = sum(1 for p in params if p.kind is inspect.Parameter.KEYWORD_ONLY)
    tem_varpos = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)
    tem_varkw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    return (posicionais, kwonly, tem_varpos, tem_varkw)


@dataclass(frozen=True)
class _Familia:
    """§Delta-F7: um Protocol real ESPECIFICO -- UM (modulo, classe) por entrada, NUNCA um nome
    compartilhado entre modulos. Antes desta correcao, Protocols "identicos por design" em
    modulos diferentes (ex.: `WhatsAppSender` de helena/fernando/lucas) eram agrupados numa UNICA
    entrada com um `assert` de auto-consistencia entre membros; quando um deles diverge
    legitimamente (ex.: LUC-08 acrescenta um `idempotency_key` obrigatorio so' ao Protocol de
    lucas), esse `assert` -- chamado transitivamente por quase todo teste do arquivo via
    `.metodos()`/`_nomes_unicos()` -- derrubava a colecao inteira (18 das 25 provas), nao so' o
    falso realmente desatualizado. Family membership agora e' MODULE-SCOPED: cada entrada tem
    exatamente um `membro` real; `agente` identifica o agente dono (p.ex. "lucas") quando o
    Protocol vive no `graph.py` de um agente especifico, ou e' `None` para um Protocol nao-ligado
    a um agente (ex.: `KafkaLike`, `FhirSummaryReader` base). Uma divergencia futura entre
    entradas historicamente identicas agora vira uma ofensa PONTUAL contra o falso que a
    exercita, nunca mais um crash de colecao."""

    nome: str
    achados: tuple[str, ...]
    membro: type
    agente: str | None = None

    def metodos(self) -> dict[str, tuple[inspect.Signature, bool]]:
        """nome -> (assinatura, e' coroutine) dos metodos PROPRIOS do UNICO membro real desta
        familia -- leitura direta, sem uniao nem auto-teste entre membros (nao ha' mais grupo)."""
        return {
            nome: (inspect.signature(fn), inspect.iscoroutinefunction(fn))
            for nome, fn in _metodos_proprios(self.membro).items()
        }


_FAMILIAS: tuple[_Familia, ...] = (
    _Familia("DmnTransport", ("NEW-12", "NEW-C1-2", "REG-04"), DmnTransport),
    _Familia(
        "WhatsAppSender:helena", ("NEW-12", "NEW-C1-2", "REG-04"), _HelenaWhatsAppSender, agente="helena"
    ),
    _Familia(
        "WhatsAppSender:fernando",
        ("NEW-12", "NEW-C1-2", "REG-04"),
        _FernandoWhatsAppSender,
        agente="fernando",
    ),
    _Familia("WhatsAppSender:lucas", ("NEW-12", "NEW-C1-2", "REG-04"), _LucasWhatsAppSender, agente="lucas"),
    _Familia("IdempotencyStore", ("NEW-12", "NEW-C1-2"), IdempotencyStore),
    _Familia("TenantKeyset", ("NEW-12", "NEW-C1-2"), TenantKeyset),
    _Familia("FhirSummaryReader", ("NEW-12", "NEW-C1-2"), FhirSummaryReader),
    _Familia("FhirSummaryReader:gustavo", ("NEW-12", "NEW-C1-2"), _GustavoFhirReader, agente="gustavo"),
    _Familia("FhirSummaryReader:rafael", ("NEW-12", "NEW-C1-2"), _RafaelFhirReader, agente="rafael"),
    _Familia("FhirSummaryReader:carolina", ("NEW-12", "NEW-C1-2"), _CarolinaSummaryReader, agente="carolina"),
    _Familia("FhirSummaryReader:andre", ("NEW-12", "NEW-C1-2"), _AndrePatientSummaryReader, agente="andre"),
    _Familia("BrRegionalTransport", ("NEW-12", "NEW-C1-2"), BrRegionalTransport),
    _Familia("OutboxClaimStore", ("NEW-12", "NEW-C1-2"), OutboxClaimStore),
    _Familia("FactBrokerPublisher", ("NEW-12", "NEW-C1-2"), FactBrokerPublisher),
    _Familia("AuditEmitter:harness", ("NEW-12", "NEW-C1-2"), _HarnessAuditEmitter),
    _Familia("AuditEmitter:dispatcher", ("NEW-12", "NEW-C1-2"), _DispatcherAuditEmitter),
    _Familia("KafkaLike", ("NEW-12", "NEW-C1-2"), KafkaLike),
)

_FAMILIA_POR_NOME: dict[str, _Familia] = {familia.nome: familia for familia in _FAMILIAS}

#: Todo agent id que e' DONO de alguma familia desta tabela -- usado para reconhecer "este
#: arquivo menciona o agente X" (§Delta-F7 item 2), nunca hardcoded.
_AGENTES_CONHECIDOS: frozenset[str] = frozenset(
    familia.agente for familia in _FAMILIAS if familia.agente is not None
)

#: Nomes de metodo genericos demais para servir de ANCORA INDEPENDENTE -- cada um colide, em
#: `tests/`, com um Protocol DIFERENTE fora desta tabela que tambem usa o mesmo nome:
#: `start`/`stop` com `BridgeDlqPublisher`/`BridgeKafkaConsumer`/`RawKafkaProducer`
#: (`platform/integrations/*.py`), `complete` com `WorkerTransport.complete(task_id, worker_id,
#: variables)` (`tools/workers/harness.py`, os falsos de `_harness_emit_killtest_writer.py`/
#: `test_harness_audit_emit.py`). So' sao checados quando a classe JA foi classificada em UMA
#: familia por outra via (`send` para `FactBrokerPublisher`; `claim_or_get` para
#: `IdempotencyStore`) -- ver `_SECUNDARIOS_POR_FAMILIA` abaixo.
_SECUNDARIOS_POR_FAMILIA: dict[str, tuple[str, ...]] = {
    "IdempotencyStore": ("complete",),
    "FactBrokerPublisher": ("start", "stop"),
}
_NOMES_SOMENTE_SECUNDARIOS = frozenset(nome for nomes in _SECUNDARIOS_POR_FAMILIA.values() for nome in nomes)

#: `generate` tem discriminador proprio (marcador `phi`, secoes A/B acima) porque o MESMO nome
#: serve dois Protocols DELIBERADAMENTE diferentes (a facade `InferenceProvider` e o `_impl`
#: `BaseInferenceProvider`) -- a tabela desta secao nunca reexamina `generate`.
_NOME_COM_DISCRIMINADOR_PROPRIO = "generate"


def _e_candidata_da_familia(no: ast.FunctionDef | ast.AsyncFunctionDef, async_real: bool) -> bool:
    """Filtro DURO de sync/async: um `def` nunca pode satisfazer um Protocol `async def` (nem
    o inverso) -- chamar `await x.metodo(...)` num retorno sincrono explode em runtime, entao um
    metodo cujo sync/async nao bate NUNCA e' um falso deste Protocol, e sim de outro Protocol que
    por acaso usa o MESMO nome de metodo (ex.: `tests/support/dmn_first_hit.py::LinearSub.
    evaluate`, um simulador de tabela SINCRONO, nada a ver com `DmnTransport`; ou `test_effect_pep.
    py::_StubPep.evaluate`, que duck-typa `PepEvaluator`, nao `DmnTransport`). Por isso este check
    exclui a classe da familia (nenhum achado), em vez de virar mais uma ofensa de paridade --
    e' selecao de FAMILIA, nao comparacao de assinatura dentro da familia ja' decidida."""
    return isinstance(no, ast.AsyncFunctionDef) == async_real


def _nomes_por_familia() -> dict[str, list[str]]:
    """nome de metodo -> nomes de TODAS as familias desta tabela que o declaram -- exclui os
    genericos-demais de `_NOMES_SOMENTE_SECUNDARIOS`. Recalculado a cada chamada (nunca cacheado
    em modulo) por simetria com o resto do arquivo, embora nao dependa mais de nenhum auto-teste
    (§Delta-F7: `familia.metodos()` agora e' leitura direta de UM membro)."""
    contagem: dict[str, list[str]] = {}
    for familia in _FAMILIAS:
        for nome in familia.metodos():
            if nome in _NOMES_SOMENTE_SECUNDARIOS:
                continue
            contagem.setdefault(nome, []).append(familia.nome)
    return contagem


def _nomes_unicos() -> dict[str, str]:
    """nome de metodo -> nome da UNICA familia que o declara, entre as desta tabela -- exclui
    nomes ambiguos (compartilhados por 2+ familias, ex.: `send`, `read_patient`, `emit_once` --
    ver `_nomes_ambiguos`) e os genericos-demais de `_NOMES_SOMENTE_SECUNDARIOS`."""
    return {nome: familias[0] for nome, familias in _nomes_por_familia().items() if len(familias) == 1}


def _nomes_ambiguos() -> dict[str, list[str]]:
    """nome de metodo -> nomes de TODAS as familias que o declaram, so' para nomes compartilhados
    por 2+ familias desta tabela -- complemento de `_nomes_unicos` (§Delta-F7). Antes da
    atomizacao module-scoped, so' `send` caia aqui (4 familias); atomizar `WhatsAppSender`/
    `FhirSummaryReader`/`AuditEmitter` por (modulo, classe) tambem tornou `read_patient`/
    `search_coverage`/`emit_once` ambiguos ENTRE as familias que antes eram um unico grupo
    "identico por design" -- resolvidos pelo contexto do arquivo (`_candidatas_por_contexto`),
    nunca por forma, exceto `send` quando nenhum contexto se aplica (compatibilidade regressiva,
    ver essa funcao)."""
    return {nome: familias for nome, familias in _nomes_por_familia().items() if len(familias) >= 2}


def _familias_por_forma_de_send() -> dict[tuple[int, int, bool, bool], list[str]]:
    """FORMA real de `send` -> familias que a declaram -- montado em tempo de execucao a partir
    dos Protocols reais, nunca hardcoded. Usado APENAS como respaldo quando nenhum contexto de
    arquivo resolve `send` (§Delta-F7 item 2: "forma so' como criterio de desempate SECUNDARIO,
    nunca como chave PRIMARIA") -- preserva o mecanismo original para as sondagens sinteticas
    (sem nenhum arquivo/import real por tras) do §Delta anterior."""
    mapa: dict[tuple[int, int, bool, bool], list[str]] = {}
    for familia in _FAMILIAS:
        metodos = familia.metodos()
        if "send" not in metodos:
            continue
        sig, _ = metodos["send"]
        mapa.setdefault(_forma_assinatura(sig), []).append(familia.nome)
    return mapa


def _arvore_de_fonte(fonte: str) -> ast.Module | None:
    try:
        return ast.parse(fonte)
    except SyntaxError:
        return None


def _classes_da_arvore(arvore: ast.Module | None) -> list[ast.ClassDef]:
    if arvore is None:
        return []
    return [n for n in ast.walk(arvore) if isinstance(n, ast.ClassDef)]


def _classes_de_fonte(fonte: str) -> list[ast.ClassDef]:
    return _classes_da_arvore(_arvore_de_fonte(fonte))


def _modulos_importados_ast(arvore: ast.Module | None) -> set[str]:
    """Modulos dotted-path referenciados por import ESTATICO no NIVEL DO MODULO (`import x.y` ou
    `from x.y import ...`, incluindo dentro de `if`/`try` de topo -- ex.: guardas de
    `TYPE_CHECKING`) -- primeiro sinal de "por qual Protocol este arquivo constroi o grafo"
    (§Delta-F7 item 2). NUNCA desce no corpo de uma `def`/`async def`: um import LOCAL dentro de
    uma funcao de teste (lazy-import, comum no repo) nao diz nada sobre o que o ARQUIVO inteiro
    testa -- contá-lo produzia falso-positivo real (`test_seam_proofs.py`'s `FakeWhatsApp`
    apontado contra `KafkaLike` so' porque uma OUTRA funcao do mesmo arquivo importa
    `maezo.a2a.dispatcher` localmente, achado ao rodar a varredura completa antes de commitar)."""
    modulos: set[str] = set()
    if arvore is None:
        return modulos

    def _visita(corpo: list[ast.stmt]) -> None:
        for no in corpo:
            if isinstance(no, ast.ImportFrom) and no.module:
                modulos.add(no.module)
            elif isinstance(no, ast.Import):
                for alias in no.names:
                    modulos.add(alias.name)
            elif isinstance(no, ast.If):
                _visita(no.body)
                _visita(no.orelse)
            elif isinstance(no, ast.Try):
                _visita(no.body)
                for handler in no.handlers:
                    _visita(handler.body)
                _visita(no.orelse)
                _visita(no.finalbody)
            # nunca desce em FunctionDef/AsyncFunctionDef/ClassDef/Lambda.

    _visita(arvore.body)
    return modulos


def _constroi_grafo_de_agente_dinamicamente_ast(arvore: ast.Module | None) -> bool:
    """Detecta o padrao real `importlib.import_module(f"maezo.agents.{agent_id}.graph")` (ou
    equivalente) -- um arquivo que CONSTROI o grafo de um agente dinamicamente, parametrizado por
    uma string de agent_id (a "OU o grafo que ele constroi" do item 2 do fix; ex.:
    `tests/unit/agents/test_start_failure_routing.py::_graph_module`). Procura por uma f-string
    (`ast.JoinedStr`) cujas partes CONSTANTES (fora do `{...}` interpolado) contenham tanto
    `maezo.agents.` quanto `.graph` -- robusto ao nome da variavel interpolada, nunca hardcoded a
    `agent_id`. So' quando este padrao existe e' que uma string-literal solta batendo com um
    agent id conhecido conta como mencao (`_agentes_mencionados_ast`) -- sem ele, um agent id
    aparecendo a' toa no arquivo por outro motivo (ex.: `origin="helena"` num teste de A2A que
    nada tem a ver com o `WhatsAppSender` dela) NAO deve virar contexto de atribuicao -- achado
    real ao rodar a varredura completa (`tests/unit/a2a/test_a2a_edge_live_pg.py` e outros dois
    apontavam contra `WhatsAppSender:helena` so' por citarem a string `"helena"`)."""
    if arvore is None:
        return False
    for no in ast.walk(arvore):
        if not isinstance(no, ast.JoinedStr):
            continue
        partes_constantes = "".join(
            v.value for v in no.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
        if "maezo.agents." in partes_constantes and ".graph" in partes_constantes:
            return True
    return False


def _agentes_mencionados_ast(arvore: ast.Module | None, modulos: set[str]) -> set[str]:
    """Agent ids "mencionados" por este arquivo -- (1) import ESTATICO, no nivel do modulo,
    ESPECIFICAMENTE de `maezo.agents.<agente>.graph` (o modulo onde `WhatsAppSender`/`FhirReader`
    vivem -- nunca `.delegation`/`.adapters`/outro submodulo, que nao carrega esses Protocols),
    OU (2) uma string-literal EXATAMENTE igual a um agent id CONHECIDO, mas SO' quando o arquivo
    tambem contem o padrao de construcao dinamica de grafo (`_constroi_grafo_de_agente_
    dinamicamente_ast`) -- sem esse padrao, (2) fica desligado (ver essa funcao para o
    falso-positivo real que motivou o gate)."""
    agentes: set[str] = set()
    for modulo in modulos:
        partes = modulo.split(".")
        if (
            len(partes) == 4
            and partes[0] == "maezo"
            and partes[1] == "agents"
            and partes[2] in _AGENTES_CONHECIDOS
            and partes[3] == "graph"
        ):
            agentes.add(partes[2])
    if arvore is not None and _constroi_grafo_de_agente_dinamicamente_ast(arvore):
        for no in ast.walk(arvore):
            if isinstance(no, ast.Constant) and isinstance(no.value, str) and no.value in _AGENTES_CONHECIDOS:
                agentes.add(no.value)
    return agentes


def _familia_mencionada_no_arquivo(nome_familia: str, agentes: set[str], modulos: set[str]) -> bool:
    """Uma familia esta' "no contexto" de um arquivo se (a) e' dona-de-agente e esse agente e'
    mencionado, ou (b) nao e' dona-de-agente e o MODULO do seu Protocol real e' importado
    estaticamente."""
    familia = _FAMILIA_POR_NOME[nome_familia]
    if familia.agente is not None:
        return familia.agente in agentes
    return familia.membro.__module__ in modulos


def _candidatas_por_contexto(
    todas_as_familias: list[str], agentes_arquivo: set[str], modulos_arquivo: set[str]
) -> list[str]:
    """Restringe a lista de familias que declaram um nome ambiguo as que o CONTEXTO do arquivo
    (imports + agent ids mencionados) torna plausiveis -- a atribuicao PRIMARIA de §Delta-F7 item
    2. Pode devolver 0 (nenhum contexto detectado -- o chamador decide o respaldo), 1 (resolvido
    sem ambiguidade, mesmo que a FORMA nao bata -- e' exatamente o caso de um falso de lucas sem
    `idempotency_key`) ou 2+ (o contexto nao decide sozinho -- o chamador verifica contra cada
    uma)."""
    return [
        nome
        for nome in todas_as_familias
        if _familia_mencionada_no_arquivo(nome, agentes_arquivo, modulos_arquivo)
    ]


def _metodos_proprios_ast(
    classe: ast.ClassDef,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        item.name: item
        for item in classe.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and not item.name.startswith("__")
    }


def _marcador_fixture_negativa(classe: ast.ClassDef) -> str | None:
    """§Delta F3: le o valor do atributo de classe `__fence_negative_fixture__`, se declarado
    DIRETAMENTE no corpo da classe como uma atribuicao literal de STRING NAO VAZIA -- a exempcao
    ESTRUTURAL e explicita para um duplo NEGATIVO deliberado (ex.: DOSSIER-BROAD-EXCEPT's
    `_DriftedReader`/`_DriftedSender`, que existem para provar que um `TypeError` de uma chamada
    mal formada propaga em vez de ser engolido por um `except Exception` -- ver
    `tests/unit/agents/test_dossier_propagates_programming_errors.py`). So' reconhece um
    `ast.Constant` de tipo `str` (nunca uma expressao computada, F-string ou referencia a nome) --
    a exempcao tem de ser LITERAL e legivel por AST, nunca um bypass indireto. Retorna `None`
    (ausencia de marcador) para uma string vazia, para que um atributo declarado mas esquecido em
    branco nao vire exempcao por acidente."""
    for item in classe.body:
        if not isinstance(item, ast.Assign):
            continue
        if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
            continue
        if item.targets[0].id != "__fence_negative_fixture__":
            continue
        if isinstance(item.value, ast.Constant) and isinstance(item.value.value, str) and item.value.value:
            return item.value.value
    return None


def _nomes_posicionais_ast(no: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Nomes posicionais (posonly + args), na ordem, SEM `self`."""
    todos = [*no.args.posonlyargs, *no.args.args]
    return [a.arg for a in todos[1:]]


def _posicional_tem_default_ast(no: ast.FunctionDef | ast.AsyncFunctionDef, indice_sem_self: int) -> bool:
    """`indice_sem_self` conta a partir de 0, ja' SEM `self`. `ast.arguments.defaults` se aplica
    aos ULTIMOS `len(defaults)` posicionais incluindo `self` (que nunca tem default)."""
    total_com_self = len(no.args.posonlyargs) + len(no.args.args)
    primeiro_indice_com_default = total_com_self - len(no.args.defaults)
    return (indice_sem_self + 1) >= primeiro_indice_com_default


def _kwonly_com_default_ast(no: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, bool]:
    """nome keyword-only -> tem default? `kw_defaults[i]` e' o `None` do PROPRIO Python (nao um
    `ast.Constant(value=None)`) quando aquele kwonly nao tem default -- `is not None` distingue
    isso de um default EXPLICITO cujo valor literal e' `None`."""
    return {
        arg.arg: (default is not None)
        for arg, default in zip(no.args.kwonlyargs, no.args.kw_defaults, strict=True)
    }


def _ofensas_forma_vs_real(
    sig_real: inspect.Signature,
    no: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Paridade de assinatura (nomes posicionais por POSICAO, nomes keyword-only por CONJUNTO,
    presenca de default -- nao o VALOR do default, porque um Protocol usa `= ...` como placeholder
    de stub) entre a assinatura REAL (`inspect.Signature`, lida em runtime) e um metodo de falso
    (`ast.FunctionDef`/`ast.AsyncFunctionDef`, lido por AST). Sync/async e' filtro de SELECAO de
    familia (`_e_candidata_da_familia`), aplicado pelo chamador ANTES desta funcao -- nao entra
    aqui como ofensa."""
    ofensas: list[str] = []

    params_reais = [p for nome, p in sig_real.parameters.items() if nome != "self"]
    pos_reais = [
        p
        for p in params_reais
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    kwonly_reais = {p.name: p for p in params_reais if p.kind is inspect.Parameter.KEYWORD_ONLY}

    nomes_fake = _nomes_posicionais_ast(no)
    tem_varpos_fake = no.args.vararg is not None
    tem_varkw_fake = no.args.kwarg is not None
    kwonly_fake = _kwonly_com_default_ast(no)

    for i, p_real in enumerate(pos_reais):
        if i >= len(nomes_fake):
            if not tem_varpos_fake:
                ofensas.append(f"falta o parametro posicional `{p_real.name}` (#{i + 1})")
            continue
        nome_fake = nomes_fake[i]
        if nome_fake != p_real.name:
            ofensas.append(
                f"parametro posicional #{i + 1}: Protocol chama `{p_real.name}`, falso chama `{nome_fake}`"
            )
        real_tem_default = p_real.default is not inspect.Parameter.empty
        fake_tem_default = _posicional_tem_default_ast(no, i)
        if real_tem_default != fake_tem_default:
            ofensas.append(
                f"parametro posicional `{p_real.name}`: Protocol "
                f"{'tem' if real_tem_default else 'nao tem'} default, falso "
                f"{'tem' if fake_tem_default else 'nao tem'}"
            )

    # o falso tem um posicional A MAIS do que o Protocol declara -- §Delta F2: uma assinatura com
    # um extra NAO acompanha o Protocol real mesmo quando o extra tem default (o que nao quebra
    # um CALLER que so' passa o que o Protocol promete, mas ainda diverge item por item, que e' o
    # que a docstring do modulo promete comparar). SEM default, todo CALLER real quebraria com
    # `TypeError: missing 1 required positional argument` -- os dois casos sao ofensa, com
    # mensagens distintas; nenhum e' coberto pelo loop acima, que so' anda pelos posicionais REAIS.
    for i in range(len(pos_reais), len(nomes_fake)):
        if _posicional_tem_default_ast(no, i):
            ofensas.append(
                f"falso tem o parametro posicional extra `{nomes_fake[i]}` (#{i + 1}, com "
                "default) que o Protocol real nao declara"
            )
        else:
            ofensas.append(
                f"falso exige o parametro posicional extra `{nomes_fake[i]}` (#{i + 1}), que o "
                "Protocol real nao declara"
            )

    if not tem_varkw_fake:
        faltando = sorted(set(kwonly_reais) - set(kwonly_fake))
        for nome in faltando:
            ofensas.append(f"falta o parametro keyword-only `{nome}`")
        # §Delta F2: o falso tem um keyword-only EXTRA que o Protocol real nao declara -- antes so'
        # o caso INVERSO (faltando) era apontado; sem esta checagem, um `**kwargs`-catchall FORA
        # (tem_varkw_fake=False) mas com um kwonly extra nomeado passava sem nenhum achado.
        extras = sorted(set(kwonly_fake) - set(kwonly_reais))
        for nome in extras:
            ofensas.append(
                f"falso tem o parametro keyword-only extra `{nome}` "
                f"({'com' if kwonly_fake[nome] else 'sem'} default) que o Protocol real nao declara"
            )
        for nome in sorted(set(kwonly_reais) & set(kwonly_fake)):
            real_tem_default = kwonly_reais[nome].default is not inspect.Parameter.empty
            if real_tem_default != kwonly_fake[nome]:
                ofensas.append(
                    f"parametro keyword-only `{nome}`: Protocol "
                    f"{'tem' if real_tem_default else 'nao tem'} default, falso "
                    f"{'tem' if kwonly_fake[nome] else 'nao tem'}"
                )

    return ofensas


def _achado_sync_async(
    rotulo: str,
    classe: ast.ClassDef,
    nome_metodo: str,
    no: ast.FunctionDef | ast.AsyncFunctionDef,
    rotulo_familia: str,
    async_real: bool,
) -> str:
    """Mensagem de ofensa para um sync/async ERRADO numa familia ja' decidida (por ancora unica,
    por contexto de arquivo, ou pela forma) -- §Delta F1: antes virava exclusao silenciosa da
    familia inteira (comportamento de `_e_candidata_da_familia`, que so' e' correto quando existe
    colisao REAL com outro Protocol -- fora desta tabela, ou dentro dela via forma GENUINAMENTE
    ambigua de `send` que o contexto do arquivo tambem nao resolveu)."""
    real = "`async def`" if async_real else "`def` (sincrono)"
    falso = "`async def`" if isinstance(no, ast.AsyncFunctionDef) else "`def` (sincrono)"
    return (
        f"{rotulo}:{no.lineno} — classe `{classe.name}`.{nome_metodo} diverge do Protocol real "
        f"`{rotulo_familia}.{nome_metodo}`: Protocol e' {real}, falso e' {falso}"
    )


def _achado_de_metodo(
    rotulo: str,
    classe: ast.ClassDef,
    nome_metodo: str,
    no: ast.FunctionDef | ast.AsyncFunctionDef,
    familia: _Familia,
    rotulo_familia: str,
) -> str | None:
    """Verificacao ORIGINAL (pre-§Delta F1): sync/async errado vira exclusao SILENCIOSA, sem
    ofensa. Preservada, sem alteracao de comportamento, EXCLUSIVAMENTE para o unico caso que
    ainda precisa dela -- `send` cuja FORMA bate com DUAS OU MAIS familias e nenhum contexto de
    arquivo desambiguou nenhuma delas (ex.: `KafkaLike`/`FactBrokerPublisher`, identicas por
    design) -- ali' a colisao e' genuina e nao ha' base para apontar UMA das duas como "a"
    divergente. Todo outro caminho usa `_achado_de_metodo_para_familia` (com o gate de forma do
    F1)."""
    sig_real, async_real = familia.metodos()[nome_metodo]
    if not _e_candidata_da_familia(no, async_real):
        return None
    ofensas = _ofensas_forma_vs_real(sig_real, no)
    if not ofensas:
        return None
    return (
        f"{rotulo}:{no.lineno} — classe `{classe.name}`.{nome_metodo} diverge do Protocol real "
        f"`{rotulo_familia}.{nome_metodo}`: {'; '.join(ofensas)}"
    )


def _achado_de_metodo_para_familia(
    rotulo: str,
    classe: ast.ClassDef,
    nome_metodo: str,
    no: ast.FunctionDef | ast.AsyncFunctionDef,
    nome_familia: str,
    *,
    exige_forma_igual_para_sync_async: bool = True,
) -> str | None:
    """§Delta-F7: verifica um metodo de falso contra UMA familia especifica ja' decidida -- por
    ancora unica (`_nomes_unicos`), por CONTEXTO de arquivo (`_candidatas_por_contexto`, item 2 do
    fix), ou (para nomes ainda ambiguos sem nenhum contexto, fora de `send`) contra qualquer uma
    das familias historicamente identicas. Sync/async errado vira ofensa -- gated pela FORMA
    batendo (mesmo gate do §Delta F1) SO' quando `exige_forma_igual_para_sync_async=True` (o caso
    de `_nomes_unicos`, onde o NOME por si so' pode colidir com um Protocol de fora da tabela).
    Quando a familia foi resolvida por CONTEXTO de arquivo (import/agent id, nao por nome nem por
    forma), o gate e' DESLIGADO -- o contexto ja' identificou POSITIVAMENTE o Protocol real, entao
    uma FORMA diferente (alem do sync/async) e' so' MAIS um sinal do mesmo drift, nunca motivo
    para excluir (e' o que faz um falso de lucas sem `idempotency_key`, sincrono OU nao, ser
    apontado NOMEANDO `WhatsAppSender:lucas` especificamente, sem afetar helena/fernando)."""
    familia = _FAMILIA_POR_NOME[nome_familia]
    sig_real, async_real = familia.metodos()[nome_metodo]
    if not _e_candidata_da_familia(no, async_real):
        if not exige_forma_igual_para_sync_async or _forma_assinatura(sig_real) == _forma_assinatura_ast(no):
            return _achado_sync_async(rotulo, classe, nome_metodo, no, nome_familia, async_real)
        return None
    ofensas = _ofensas_forma_vs_real(sig_real, no)
    if not ofensas:
        return None
    return (
        f"{rotulo}:{no.lineno} — classe `{classe.name}`.{nome_metodo} diverge do Protocol real "
        f"`{nome_familia}.{nome_metodo}`: {'; '.join(ofensas)}"
    )


def _formas_de_send_sao_realmente_intercambiaveis(candidatas: list[str]) -> bool:
    """As candidatas partilham a MESMA forma (por construcao, via `_familias_por_forma_de_send`)
    -- mas so' sao verdadeiramente INTERCAMBIAVEIS (escolher QUALQUER uma da' o MESMO veredito
    contra um falso qualquer) se tambem concordarem nos NOMES dos parametros (nao so' na
    aridade). `KafkaLike`/`FactBrokerPublisher` sao identicas por design (mesmos nomes). Um
    Protocol de agente que passe a COMPARTILHAR a forma delas por coincidencia de aridade (ex.:
    `WhatsAppSender:lucas` pos-LUC-08, `(2 posicionais, 1 kwonly)` igual a Kafka) tem nomes
    DIFERENTES -- ali' a escolha arbitraria de uma representante ficaria ERRADA (a ofensa
    reportada dependeria de qual a tabela lista primeiro), entao NENHUMA e' escolhida (ver
    chamador)."""
    referencia: tuple[str, ...] | None = None
    for nome in candidatas:
        sig, _ = _FAMILIA_POR_NOME[nome].metodos()["send"]
        chave = tuple(p.name for p in sig.parameters.values() if p.name != "self")
        if referencia is None:
            referencia = chave
        elif chave != referencia:
            return False
    return True


def _checa_metodo_ambiguo(
    rotulo: str,
    classe: ast.ClassDef,
    nome_metodo: str,
    no: ast.FunctionDef | ast.AsyncFunctionDef,
    todas_as_familias: list[str],
    agentes_arquivo: set[str],
    modulos_arquivo: set[str],
    formas_send: dict[tuple[int, int, bool, bool], list[str]],
) -> tuple[list[str], set[str]]:
    """§Delta-F7 item 2: resolve um metodo AMBIGUO (nome compartilhado por 2+ familias) contra a
    familia certa -- CONTEXTO do arquivo primeiro (imports/agent ids mencionados), forma so'
    como respaldo SECUNDARIO quando nenhum contexto se aplica. Devolve (achados, nomes de
    familia ancorados -- para o laco de metodos SECUNDARIOS do chamador)."""
    achados: list[str] = []
    ancoradas: set[str] = set()

    candidatas_ctx = _candidatas_por_contexto(todas_as_familias, agentes_arquivo, modulos_arquivo)
    if candidatas_ctx:
        # Contexto decide -- NUNCA gateado por forma (item 2 do fix: contexto e' a chave
        # PRIMARIA; uma forma diferente da esperada e' so' mais um sinal do mesmo drift).
        ancoradas.update(candidatas_ctx)
        for nome_familia in candidatas_ctx:
            achado = _achado_de_metodo_para_familia(
                rotulo, classe, nome_metodo, no, nome_familia, exige_forma_igual_para_sync_async=False
            )
            if achado and achado not in achados:
                achados.append(achado)
        return achados, ancoradas

    if nome_metodo != "send":
        # Sem contexto e sem ser o nome ambiguo classico: as familias que sobram sao
        # historicamente IDENTICAS por design (ex.: `read_patient` dos 5 leitores FHIR,
        # `emit_once` dos 2 AuditEmitters) -- verifica contra todas (o resultado e' o mesmo,
        # ja' que concordam; se um dia divergirem, cada uma passa a ser apontada por si). Aqui o
        # gate de forma FICA LIGADO (default) -- sem contexto, o NOME sozinho ainda pode colidir
        # com um Protocol de fora da tabela.
        ancoradas.update(todas_as_familias)
        for nome_familia in todas_as_familias:
            achado = _achado_de_metodo_para_familia(rotulo, classe, nome_metodo, no, nome_familia)
            if achado and achado not in achados:
                achados.append(achado)
        return achados, ancoradas

    # `send` sem NENHUM contexto de arquivo -- respaldo por FORMA, mecanismo original preservado
    # (compatibilidade regressiva total com as sondagens sinteticas do §Delta anterior, que nao
    # tem nenhum import/agent id por tras).
    forma_fake = _forma_assinatura_ast(no)
    candidatas_forma = formas_send.get(forma_fake)
    if not candidatas_forma:
        achados.append(
            f"{rotulo}:{no.lineno} — classe `{classe.name}`.send tem forma "
            f"(posicionais={forma_fake[0]}, kwonly={forma_fake[1]}, "
            f"*args={forma_fake[2]}, **kwargs={forma_fake[3]}) que nao bate com "
            "NENHUM Protocol `send` conhecido (WhatsAppSender, BrRegionalTransport, "
            "KafkaLike, FactBrokerPublisher) — cerca de COMPLETUDE (P-17): registre "
            "a forma nova em `_familias_por_forma_de_send` antes de prosseguir, nunca "
            "ignore em silencio um falso ambiguo novo"
        )
        return achados, ancoradas
    ancoradas.update(candidatas_forma)  # sempre ancora TODAS (checagens SECUNDARIAS, ex. start/stop,
    # independem de qual delas e' "a" duck-typada pelo `send`)
    if len(candidatas_forma) == 1:
        # A forma resolveu para UMA UNICA familia -- mesmo gate do F1 (moot aqui: a forma ja'
        # bate por construcao da propria busca em `formas_send`).
        achado = _achado_de_metodo_para_familia(rotulo, classe, "send", no, candidatas_forma[0])
        if achado:
            achados.append(achado)
        return achados, ancoradas
    if _formas_de_send_sao_realmente_intercambiaveis(candidatas_forma):
        # 2+ familias com a MESMA forma E OS MESMOS NOMES (Kafka/FactBrokerPublisher hoje) --
        # ambiguidade GENUINA sem nenhum contexto para desempatar, mas a escolha de QUAL delas e'
        # inocua (dao o mesmo veredito); preserva o comportamento ORIGINAL (`_achado_de_metodo`,
        # exclusao silenciosa em sync/async errado, SEM o gate do F1).
        familia = _FAMILIA_POR_NOME[candidatas_forma[0]]
        achado = _achado_de_metodo(rotulo, classe, "send", no, familia, " / ".join(candidatas_forma))
        if achado:
            achados.append(achado)
        return achados, ancoradas
    # 2+ familias com a MESMA forma mas NOMES DIFERENTES (ex.: `WhatsAppSender:lucas` pos-LUC-08
    # colidindo em forma com Kafka/FactBrokerPublisher) -- nao ha' pick seguro (qual delas
    # escolher mudaria a ofensa reportada) e nenhum contexto para desempatar; a unica opcao
    # honesta e' nao apontar NADA para `send` aqui (`ancoradas` ja' foi atualizado acima, entao um
    # `stop`/`complete` secundario ainda e' checado normalmente).
    return achados, ancoradas


# Colaboradores externos reais cuja SUBSTITUIÇÃO por monkeypatch prova o papel da classe: a
# identidade é do objeto substituído (módulo de origem + qualname), nunca do nome da classe do
# teste. `http.client.HTTPConnection` expõe só `close` nesta família; `botocore.httpsession.
# URLLib3Session` expõe `send(request)` síncrono e `close()` — o par exato que colide com os
# Protocols do projeto (`BrRegionalTransport.send`, `DmnTransport.close`).
_COLABORADORES_EXTERNOS_SUBSTITUIVEIS: dict[tuple[str, str], frozenset[str]] = {
    ("http.client", "HTTPConnection"): frozenset({"close"}),
    ("botocore.httpsession", "URLLib3Session"): frozenset({"send", "close"}),
}


def _metodos_do_colaborador_substituido(alvo: str, atributo: str) -> frozenset[str]:
    """Métodos externos provados por `monkeypatch.setattr(<alvo>, "<atributo>", Classe)`.

    O alvo lexical pode ser o módulo de origem (`http.client`) ou um módulo do projeto que
    RE-EXPORTA o colaborador (`maezo.gateway.kafka_client.URLLib3Session`): neste caso a
    identidade é resolvida importando SÓ módulos `maezo.*` e comparando `__module__`/`__qualname__`
    do objeto substituído com a tabela acima. Falha de import, atributo ausente ou objeto fora da
    tabela ⇒ nada é provado e a classe mantém o fallback F1/F7 (fail-closed).
    """
    direto = _COLABORADORES_EXTERNOS_SUBSTITUIVEIS.get((alvo, atributo))
    if direto is not None:
        return direto
    if not alvo.startswith("maezo."):
        return frozenset()
    try:
        objeto = getattr(importlib.import_module(alvo), atributo)
    except Exception:  # qualquer falha de resolução prova nada (fail-closed)
        return frozenset()
    identidade = (getattr(objeto, "__module__", None), getattr(objeto, "__qualname__", None))
    return _COLABORADORES_EXTERNOS_SUBSTITUIVEIS.get(identidade, frozenset())


def _metodos_de_colaborador_externo(arvore: ast.Module | None) -> dict[ast.ClassDef, set[str]]:
    """Resolve colisões de `close`/`send` por USO da classe, não pelo seu nome ou caminho.

    HTTPConnection substituído por monkeypatch e socket passado diretamente a SyncStream
    (também via subclasse) são colaboradores externos reais. Imports sozinhos, classes irmãs
    e coincidência de assinatura não provam essa relação. Resolução estática e lexical de
    imports/aliases; não importa nem executa o módulo de teste. Outros usos não reconhecidos
    mantêm o fallback F1/F7. A prova externa só afeta métodos compartilhados, nunca a classe
    inteira; outra âncora da família ou herança explícita conserva a checagem de drift.
    """
    if arvore is None:
        return {}
    bindings: dict[str, str | ast.ClassDef] = {}
    bases: dict[ast.ClassDef, set[str]] = {}
    externos: dict[ast.ClassDef, set[str]] = {}
    marcador_monkeypatch = "pytest.MonkeyPatch.fixture"
    atributos_sombreados: set[str] = set()
    prefixos_sombreados: set[str] = set()

    def nomes_vinculados(no: ast.expr) -> set[str]:
        if isinstance(no, ast.Name):
            return {no.id}
        if isinstance(no, ast.Starred):
            return nomes_vinculados(no.value)
        if isinstance(no, (ast.Tuple, ast.List)):
            return set().union(*(nomes_vinculados(item) for item in no.elts))
        return set()

    def resolve(no: ast.expr, scope: dict[str, str | ast.ClassDef]) -> str | ast.ClassDef | None:
        if isinstance(no, ast.Name):
            return scope.get(no.id)
        if isinstance(no, ast.Attribute):
            parent = resolve(no.value, scope)
            if isinstance(parent, str):
                member = parent + "." + no.attr
                return (
                    None
                    if member in atributos_sombreados
                    or any(
                        member == prefixo or member.startswith(prefixo + ".")
                        for prefixo in prefixos_sombreados
                    )
                    else member
                )
        if isinstance(no, ast.Call) and resolve(no.func, scope) == "pytest.MonkeyPatch":
            return marcador_monkeypatch
        return None

    def instrucoes_do_modulo(bloco: list[ast.stmt]) -> list[ast.stmt]:
        instrucoes: list[ast.stmt] = []
        for no in bloco:
            instrucoes.append(no)
            filhos: list[ast.stmt] = []
            if isinstance(no, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                filhos.extend([*no.body, *no.orelse])
            elif isinstance(no, (ast.With, ast.AsyncWith)):
                filhos.extend(no.body)
            elif isinstance(no, (ast.Try, ast.TryStar)):
                filhos.extend([*no.body, *no.orelse, *no.finalbody])
                for handler in no.handlers:
                    filhos.extend(handler.body)
            elif isinstance(no, ast.Match):
                for caso in no.cases:
                    filhos.extend(caso.body)
            if filhos:
                instrucoes.extend(instrucoes_do_modulo(filhos))
        return instrucoes

    funcoes = {no.name: no for no in arvore.body if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))}
    instrucoes_modulo = instrucoes_do_modulo(arvore.body)
    importacoes_pytest = [
        alias
        for no in arvore.body
        if isinstance(no, ast.Import)
        for alias in no.names
        if alias.name == "pytest" and (alias.asname is None or alias.asname == "pytest")
    ]

    def vincula_nome_no_modulo(no: ast.stmt, nome: str) -> bool:
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return no.name == nome
        if isinstance(no, (ast.Import, ast.ImportFrom)):
            return any((alias.asname or alias.name.split(".")[0]) == nome for alias in no.names)
        if isinstance(no, ast.Assign):
            return any(nome in nomes_vinculados(target) for target in no.targets)
        if isinstance(no, ast.AnnAssign):
            return no.value is not None and nome in nomes_vinculados(no.target)
        if isinstance(no, (ast.AugAssign, ast.For, ast.AsyncFor)):
            return nome in nomes_vinculados(no.target)
        if isinstance(no, (ast.With, ast.AsyncWith)):
            return any(
                item.optional_vars is not None and nome in nomes_vinculados(item.optional_vars)
                for item in no.items
            )
        if isinstance(no, ast.Delete):
            return any(nome in nomes_vinculados(target) for target in no.targets)
        return False

    pytest_sombreado_no_modulo = len(importacoes_pytest) != 1 or any(
        vincula_nome_no_modulo(no, "pytest")
        and not (
            isinstance(no, ast.Import)
            and any(alias is importacoes_pytest[0] for alias in no.names)
            and all(
                (alias.asname or alias.name.split(".")[0]) != "pytest" or alias is importacoes_pytest[0]
                for alias in no.names
            )
        )
        for no in instrucoes_modulo
    )

    def fixture_nomeada_monkeypatch(no: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for decorador in no.decorator_list:
            if not (
                isinstance(decorador, ast.Call)
                and isinstance(decorador.func, ast.Attribute)
                and isinstance(decorador.func.value, ast.Name)
                and decorador.func.value.id == "pytest"
                and decorador.func.attr == "fixture"
            ):
                continue
            nome = next((keyword.value for keyword in decorador.keywords if keyword.arg == "name"), None)
            if isinstance(nome, ast.Constant) and nome.value == "monkeypatch":
                return True
        return False

    monkeypatch_sombreado_no_modulo = any(
        vincula_nome_no_modulo(no, "monkeypatch")
        or (isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and fixture_nomeada_monkeypatch(no))
        for no in instrucoes_modulo
    )

    def chamada_parametriza_monkeypatch(decorador: ast.expr) -> bool:
        if not (
            isinstance(decorador, ast.Call)
            and isinstance(decorador.func, ast.Attribute)
            and decorador.func.attr == "parametrize"
            and isinstance(decorador.func.value, ast.Attribute)
            and isinstance(decorador.func.value.value, ast.Name)
            and decorador.func.value.value.id == "pytest"
            and decorador.func.value.attr == "mark"
        ):
            return False
        nomes = (
            decorador.args[0]
            if decorador.args
            else next((keyword.value for keyword in decorador.keywords if keyword.arg == "argnames"), None)
        )
        if isinstance(nomes, ast.Constant) and isinstance(nomes.value, str):
            return "monkeypatch" in {parte.strip() for parte in nomes.value.split(",")}
        if isinstance(nomes, (ast.List, ast.Tuple)) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in nomes.elts
        ):
            return any(item.value == "monkeypatch" for item in nomes.elts)
        return True

    def marca_pytest_preserva_monkeypatch(expressao: ast.expr, *, permite_container: bool) -> bool:
        if isinstance(expressao, (ast.List, ast.Tuple)):
            return permite_container and all(
                marca_pytest_preserva_monkeypatch(item, permite_container=False) for item in expressao.elts
            )
        if isinstance(expressao, ast.Attribute):
            return (
                isinstance(expressao.value, ast.Attribute)
                and isinstance(expressao.value.value, ast.Name)
                and expressao.value.value.id == "pytest"
                and expressao.value.attr == "mark"
                and expressao.attr != "parametrize"
            )
        if isinstance(expressao, ast.Call):
            if chamada_parametriza_monkeypatch(expressao):
                return False
            if isinstance(expressao.func, ast.Attribute) and expressao.func.attr == "parametrize":
                return (
                    isinstance(expressao.func.value, ast.Attribute)
                    and isinstance(expressao.func.value.value, ast.Name)
                    and expressao.func.value.value.id == "pytest"
                    and expressao.func.value.attr == "mark"
                )
            return marca_pytest_preserva_monkeypatch(expressao.func, permite_container=False)
        return False

    vinculos_pytestmark = [no for no in instrucoes_modulo if vincula_nome_no_modulo(no, "pytestmark")]
    marcas_modulo_preservam_monkeypatch = all(
        isinstance(no, ast.Assign)
        and len(no.targets) == 1
        and isinstance(no.targets[0], ast.Name)
        and no.targets[0].id == "pytestmark"
        and marca_pytest_preserva_monkeypatch(no.value, permite_container=True)
        for no in vinculos_pytestmark
    )

    def decorador_fixture_pytest(decorador: ast.expr) -> bool:
        """`@pytest.fixture` / `@pytest.fixture(...)` sem `name=`: pytest injeta `monkeypatch` numa
        fixture exatamente como numa função `test_*`. Uma fixture NOMEADA `monkeypatch` é sombra
        (`fixture_nomeada_monkeypatch` acima) e nunca chega aqui como injeção."""
        if isinstance(decorador, ast.Call) and any(k.arg == "name" for k in decorador.keywords):
            return False
        alvo = decorador.func if isinstance(decorador, ast.Call) else decorador
        return (
            isinstance(alvo, ast.Attribute)
            and isinstance(alvo.value, ast.Name)
            and alvo.value.id == "pytest"
            and alvo.attr == "fixture"
        )

    def pytest_injeta_monkeypatch(no: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        if no.name.startswith("test_"):
            return all(
                marca_pytest_preserva_monkeypatch(decorador, permite_container=False)
                for decorador in no.decorator_list
            )
        fixtures = [decorador for decorador in no.decorator_list if decorador_fixture_pytest(decorador)]
        return len(fixtures) == 1 and all(
            decorador_fixture_pytest(decorador)
            or marca_pytest_preserva_monkeypatch(decorador, permite_container=False)
            for decorador in no.decorator_list
        )

    parametros_monkeypatch: dict[ast.FunctionDef | ast.AsyncFunctionDef, set[str]] = {
        no: (
            {"monkeypatch"}
            if not pytest_sombreado_no_modulo
            and not monkeypatch_sombreado_no_modulo
            and marcas_modulo_preservam_monkeypatch
            and pytest_injeta_monkeypatch(no)
            else set()
        )
        for no in funcoes.values()
        if any(argument.arg == "monkeypatch" for argument in [*no.args.posonlyargs, *no.args.args])
    }

    def chamadas_lexicais(no: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
        chamadas: list[ast.Call] = []

        def visita_filho(item: ast.AST) -> None:
            if item is not no and isinstance(
                item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                return
            if isinstance(item, ast.Call):
                chamadas.append(item)
            for filho in ast.iter_child_nodes(item):
                visita_filho(filho)

        for item in no.body:
            visita_filho(item)
        return chamadas

    def parametro_estavel(no: ast.FunctionDef | ast.AsyncFunctionDef, nome: str) -> bool:
        """A forwarded fixture parameter stops being proof if its function ever rebinds it."""
        estavel = True

        def visita_filho(item: ast.AST) -> None:
            nonlocal estavel
            if not estavel:
                return
            if item is not no and isinstance(
                item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                return
            alvos: list[ast.expr] = []
            if isinstance(item, ast.Assign):
                alvos.extend(item.targets)
            elif (
                isinstance(item, ast.AnnAssign)
                and item.value is not None
                or isinstance(item, (ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor, ast.comprehension))
            ):
                alvos.append(item.target)
            elif isinstance(item, ast.With | ast.AsyncWith):
                alvos.extend(value.optional_vars for value in item.items if value.optional_vars is not None)
            elif isinstance(item, ast.Delete):
                alvos.extend(item.targets)
            if any(nome in nomes_vinculados(alvo) for alvo in alvos):
                estavel = False
                return
            if isinstance(item, ast.ExceptHandler) and item.name == nome:
                estavel = False
                return
            if isinstance(item, (ast.Import, ast.ImportFrom)) and any(
                (alias.asname or alias.name.split(".")[0]) == nome for alias in item.names
            ):
                estavel = False
                return
            for filho in ast.iter_child_nodes(item):
                visita_filho(filho)

        for item in no.body:
            visita_filho(item)
        return estavel

    mudou = True
    while mudou:
        mudou = False
        for chamadora in funcoes.values():
            confiaveis = parametros_monkeypatch.get(chamadora, set())
            if not confiaveis:
                continue
            for chamada in chamadas_lexicais(chamadora):
                if not isinstance(chamada.func, ast.Name) or chamada.func.id not in funcoes:
                    continue
                chamada_alvo = funcoes[chamada.func.id]
                parametros = [*chamada_alvo.args.posonlyargs, *chamada_alvo.args.args]
                argumentos = {
                    parametros[indice].arg: argumento
                    for indice, argumento in enumerate(chamada.args)
                    if indice < len(parametros)
                }
                argumentos.update(
                    {palavra.arg: palavra.value for palavra in chamada.keywords if palavra.arg is not None}
                )
                destino = parametros_monkeypatch.setdefault(chamada_alvo, set())
                for parametro, argumento in argumentos.items():
                    if (
                        isinstance(argumento, ast.Name)
                        and argumento.id in confiaveis
                        and parametro_estavel(chamadora, argumento.id)
                        and parametro not in destino
                    ):
                        destino.add(parametro)
                        mudou = True

    def marca(classe: ast.ClassDef, metodos: set[str]) -> None:
        externos.setdefault(classe, set()).update(metodos)

    def invalida_atributo(no: ast.expr, scope: dict[str, str | ast.ClassDef]) -> None:
        if isinstance(no, ast.Starred):
            invalida_atributo(no.value, scope)
            return
        if isinstance(no, (ast.Tuple, ast.List)):
            for item in no.elts:
                invalida_atributo(item, scope)
            return
        if isinstance(no, ast.Subscript):
            container = resolve(no.value, scope)
            if isinstance(container, str):
                prefixos_sombreados.add(container.partition(".")[0])
            return
        if not isinstance(no, ast.Attribute):
            return
        parent = resolve(no.value, scope)
        if isinstance(parent, str):
            atributos_sombreados.add(parent + "." + no.attr)

    def visita(no: ast.AST, scope: dict[str, str | ast.ClassDef]) -> None:
        if isinstance(no, ast.Import):
            for alias in no.names:
                scope[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
            return
        if isinstance(no, ast.ImportFrom):
            if no.module and not no.level:
                for alias in no.names:
                    scope[alias.asname or alias.name] = no.module + "." + alias.name
            return
        if isinstance(no, ast.ClassDef):
            ancestors: set[str] = set()
            for base in no.bases:
                resolved = resolve(base, scope)
                if isinstance(resolved, str):
                    ancestors.add(resolved)
                elif isinstance(resolved, ast.ClassDef):
                    ancestors.update(bases.get(resolved, set()))
            bases[no] = ancestors
            scope[no.name] = no
            local = dict(scope)
            for item in no.body:
                visita(item, local)
            return
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            if not isinstance(no, ast.Lambda):
                scope.pop(no.name, None)
            local = dict(scope)
            for arg in [*no.args.posonlyargs, *no.args.args, *no.args.kwonlyargs]:
                local.pop(arg.arg, None)
            for arg in (no.args.vararg, no.args.kwarg):
                if arg is not None:
                    local.pop(arg.arg, None)
            if not isinstance(no, ast.Lambda):
                for nome in parametros_monkeypatch.get(no, set()):
                    local[nome] = marcador_monkeypatch
            body = [no.body] if isinstance(no, ast.Lambda) else no.body
            for item in body:
                visita(item, local)
            return
        if isinstance(no, ast.Assign):
            value = resolve(no.value, scope)
            for target in no.targets:
                invalida_atributo(target, scope)
                for nome in nomes_vinculados(target):
                    scope.pop(nome, None)
                if isinstance(target, ast.Name) and value is not None:
                    scope[target.id] = value
        if isinstance(no, ast.AnnAssign) and no.value is not None:
            value = resolve(no.value, scope)
            invalida_atributo(no.target, scope)
            for nome in nomes_vinculados(no.target):
                scope.pop(nome, None)
            if isinstance(no.target, ast.Name) and value is not None:
                scope[no.target.id] = value
        if isinstance(no, (ast.AugAssign, ast.NamedExpr)):
            invalida_atributo(no.target, scope)
            for nome in nomes_vinculados(no.target):
                scope.pop(nome, None)
        if isinstance(no, (ast.For, ast.AsyncFor, ast.comprehension)):
            invalida_atributo(no.target, scope)
            for nome in nomes_vinculados(no.target):
                scope.pop(nome, None)
        if isinstance(no, ast.With | ast.AsyncWith):
            for item in no.items:
                if item.optional_vars is not None:
                    invalida_atributo(item.optional_vars, scope)
                    for nome in nomes_vinculados(item.optional_vars):
                        scope.pop(nome, None)
        if isinstance(no, ast.ExceptHandler) and no.name:
            scope.pop(no.name, None)
        if isinstance(no, ast.Delete):
            for target in no.targets:
                invalida_atributo(target, scope)
                for nome in nomes_vinculados(target):
                    scope.pop(nome, None)
        if isinstance(no, ast.Call):
            if (
                isinstance(no.func, ast.Name)
                and no.func.id == "setattr"
                and len(no.args) >= 2
                and isinstance(no.args[1], ast.Constant)
                and isinstance(no.args[1].value, str)
            ):
                parent = resolve(no.args[0], scope)
                if isinstance(parent, str):
                    atributos_sombreados.add(parent + "." + no.args[1].value)
            # API monkeypatch.setattr(module, "HTTPConnection", replacement_class).
            if isinstance(no.func, ast.Attribute) and no.func.attr == "setattr" and len(no.args) >= 3:
                target, attribute, replacement = no.args[:3]
                classe = resolve(replacement, scope)
                receiver = resolve(no.func.value, scope)
                target_identity = resolve(target, scope)
                if (
                    isinstance(target_identity, str)
                    and isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                ):
                    atributos_sombreados.add(target_identity + "." + attribute.value)
                if (
                    receiver == marcador_monkeypatch
                    and isinstance(target_identity, str)
                    and isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                    and isinstance(classe, ast.ClassDef)
                ):
                    metodos = _metodos_do_colaborador_substituido(target_identity, attribute.value)
                    if metodos:
                        marca(classe, set(metodos))
            stream = resolve(no.func, scope)
            stream_bases = bases.get(stream, set()) if isinstance(stream, ast.ClassDef) else {stream}
            if "httpcore._backends.sync.SyncStream" in stream_bases and no.args:
                socket = no.args[0]
                classe = resolve(socket.func, scope) if isinstance(socket, ast.Call) else None
                if isinstance(classe, ast.ClassDef):
                    marca(classe, {"send", "close"})
        for child in ast.iter_child_nodes(no):
            visita(child, scope)

    visita(arvore, bindings)
    for classe, metodos in externos.items():
        proprios = _metodos_proprios_ast(classe)
        for familia in _FAMILIAS:
            metodos_familia = familia.metodos()
            identidade = familia.membro.__module__ + "." + familia.membro.__name__
            outras_ancoras = set(proprios) & (set(metodos_familia) - {"close", "send"})
            if identidade in bases.get(classe, set()) or outras_ancoras:
                metodos.difference_update(metodos_familia)
    return externos


def _achados_estruturais_em_fonte(fonte: str, rotulo: str) -> list[str]:
    achados: list[str] = []
    unicos = _nomes_unicos()
    ambiguos = _nomes_ambiguos()
    formas_send = _familias_por_forma_de_send()
    arvore = _arvore_de_fonte(fonte)
    modulos_arquivo = _modulos_importados_ast(arvore)
    agentes_arquivo = _agentes_mencionados_ast(arvore, modulos_arquivo)
    metodos_externos = _metodos_de_colaborador_externo(arvore)

    for classe in _classes_da_arvore(arvore):
        metodos_classe = _metodos_proprios_ast(classe)
        familias_ancoradas: set[str] = set()
        achados_da_classe: list[str] = []

        for nome_metodo, no in metodos_classe.items():
            if nome_metodo in metodos_externos.get(classe, set()):
                continue  # uso concreto identifica outro colaborador, não esta família
            if nome_metodo == _NOME_COM_DISCRIMINADOR_PROPRIO:
                continue  # coberto pelas secoes (A)/(B) acima (marcador `phi`)
            if nome_metodo in _NOMES_SOMENTE_SECUNDARIOS:
                continue  # so' checados no laco de bonus abaixo, nunca como gatilho proprio
            if nome_metodo in unicos:
                nome_familia = unicos[nome_metodo]
                familias_ancoradas.add(nome_familia)
                achado = _achado_de_metodo_para_familia(rotulo, classe, nome_metodo, no, nome_familia)
                if achado:
                    achados_da_classe.append(achado)
                continue
            if nome_metodo in ambiguos:
                novos_achados, novas_ancoras = _checa_metodo_ambiguo(
                    rotulo,
                    classe,
                    nome_metodo,
                    no,
                    ambiguos[nome_metodo],
                    agentes_arquivo,
                    modulos_arquivo,
                    formas_send,
                )
                achados_da_classe.extend(novos_achados)
                familias_ancoradas.update(novas_ancoras)

        for nome_familia in familias_ancoradas:
            for nome_secundario in _SECUNDARIOS_POR_FAMILIA.get(nome_familia, ()):
                if nome_secundario not in metodos_classe:
                    continue
                familia = _FAMILIA_POR_NOME[nome_familia]
                if nome_secundario not in familia.metodos():
                    continue
                achado = _achado_de_metodo(
                    rotulo, classe, nome_secundario, metodos_classe[nome_secundario], familia, nome_familia
                )
                if achado:
                    achados_da_classe.append(achado)

        marcador = _marcador_fixture_negativa(classe)
        if marcador is not None:
            # §Delta F3: `__fence_negative_fixture__` exime uma classe deliberadamente
            # malformada (ex.: DOSSIER-BROAD-EXCEPT's `_DriftedReader`/`_DriftedSender`, que
            # existem para provar que um `TypeError` de uma chamada mal formada PROPAGA em vez
            # de ser engolido por um `except Exception`). Mas o marcador so' exime a classe se
            # ela REALMENTE divergir (ao menos 1 ofensa) -- senao a propria presenca do marcador
            # vira achado, para que nunca sirva de bypass de texto livre contra uma classe que na
            # verdade acompanha o Protocol real.
            if not achados_da_classe:
                achados.append(
                    f"{rotulo}:{classe.lineno} — classe `{classe.name}` declara "
                    f"`__fence_negative_fixture__` ({marcador!r}) mas nao diverge do Protocol "
                    "real em NADA (nenhuma ofensa encontrada) -- marcador sobre um falso "
                    "CONFORME: o marcador so' exime um duplo deliberadamente malformado, nunca "
                    "use-o para silenciar uma classe que acompanha o Protocol de verdade"
                )
            continue
        achados.extend(achados_da_classe)
    return achados


def _forma_assinatura_ast(no: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int, bool, bool]:
    posicionais = len(no.args.posonlyargs) + len(no.args.args) - 1  # -1 exclui `self`
    kwonly = len(no.args.kwonlyargs)
    return (posicionais, kwonly, no.args.vararg is not None, no.args.kwarg is not None)


def _achados_estruturais_em_arquivo(caminho: Path) -> list[str]:
    return _achados_estruturais_em_fonte(caminho.read_text(encoding="utf-8"), str(caminho.relative_to(_RAIZ)))


def test_lista_fechada_de_protocols_ainda_existe_em_src() -> None:
    """Fecha o contrato do WP: a lista de familias vigiadas por esta secao (alem de
    `InferenceProvider`, coberto por (A)/(B)) e' EXATAMENTE a que o brief fechou, apontada pelo
    achado novo de ASSURANCE-F1-E.md (`NEW-12`) -- se uma classe for renomeada/removida de
    `src/`, o IMPORT do topo do arquivo ja' quebra a colecao (falha alta, nao silenciosa); este
    teste torna a lista fechada tambem uma asserção viva, nao so' um comentario. §Delta-F7:
    17 entradas module-scoped (nao mais 10 agrupadas) -- `WhatsAppSender`/`FhirSummaryReader`/
    `AuditEmitter` viraram 3/5/2 entradas por (modulo, classe), uma por agente/modulo dono."""
    esperado = {
        "DmnTransport",
        "WhatsAppSender:helena",
        "WhatsAppSender:fernando",
        "WhatsAppSender:lucas",
        "IdempotencyStore",
        "TenantKeyset",
        "FhirSummaryReader",
        "FhirSummaryReader:gustavo",
        "FhirSummaryReader:rafael",
        "FhirSummaryReader:carolina",
        "FhirSummaryReader:andre",
        "BrRegionalTransport",
        "OutboxClaimStore",
        "FactBrokerPublisher",
        "AuditEmitter:harness",
        "AuditEmitter:dispatcher",
        "KafkaLike",
    }
    assert {familia.nome for familia in _FAMILIAS} == esperado


def test_familias_tem_ao_menos_um_metodo_proprio_cada() -> None:
    """Sanidade: nenhuma familia da tabela e' um Protocol vazio (o que tornaria a cerca vacua
    para ela sem nenhum sinal)."""
    for familia in _FAMILIAS:
        assert familia.metodos(), f"familia {familia.nome!r} nao declara nenhum metodo proprio"


def test_familias_de_agente_tem_o_campo_agente_preenchido() -> None:
    """§Delta-F7 item 1: toda familia cujo sufixo `Protocol:<x>` e' um AGENT ID conhecido declara
    o campo `agente` correspondente -- e' o que `_agentes_mencionados_ast` usa para atribuir um
    falso ao Protocol certo pelo CONTEXTO do arquivo (item 2), nunca pela forma. `AuditEmitter:
    harness`/`AuditEmitter:dispatcher` sao module-scoped (o sufixo e' o MODULO dono, nao um
    agente) -- `agente is None`, atribuidos por import do modulo, nao por agent id. Familias sem
    `:` no nome (ex.: `KafkaLike`) tambem tem `agente is None`."""
    for familia in _FAMILIAS:
        if ":" in familia.nome:
            _, sufixo = familia.nome.split(":", 1)
            if sufixo in _AGENTES_CONHECIDOS:
                assert familia.agente == sufixo, familia.nome
            else:
                assert familia.agente is None, familia.nome
        else:
            assert familia.agente is None, familia.nome


def test_fhirsummaryreader_atomizada_continua_concordando_hoje() -> None:
    """§Delta-F7: substitui o antigo `test_membros_de_uma_familia_concordam_entre_si` (que
    exercitava o `assert` de auto-consistencia de um grupo multi-membro, removido junto com o
    agrupamento). As entradas atomizadas de `FhirSummaryReader` continuam concordando HOJE, cada
    uma no metodo que DECLARA (verificado diretamente, sem nenhum `assert` interno bloqueante):
    4 delas em `read_patient` e 2 (a generica e a de carolina, apos NEW-04) em
    `read_patient_summary` -- `search_coverage` so' em `FhirSummaryReader:rafael`, a mesma
    assimetria unica-por-design de sempre. Se uma delas divergir amanha, este teste aponta a
    causa aqui, e a cerca principal
    (`_checa_metodo_ambiguo`) passa a apontar cada falso real contra a familia especifica que ele
    deixou de acompanhar -- nunca mais um crash de colecao para as outras 16 familias."""
    # TREM train-b: a premissa original dizia "as 5 entradas concordam em `read_patient`". O WP
    # FHIR-TOOL-SURFACE-PARITY (NEW-04, P1) desceu o CODIGO de carolina ate a superficie que o
    # `spec/agents/carolina/agent.yaml` declara (`mcp-fhir.read_patient_summary`), entao a entrada
    # `FhirSummaryReader:carolina` deixou de declarar `read_patient`. A premissa foi atualizada a
    # paisagem REAL da uniao, nao relaxada: as 4 entradas que declaram `read_patient` continuam
    # obrigadas a concordar entre si, e as 2 que declaram `read_patient_summary` (a generica e a de
    # carolina) ganharam a MESMA obrigacao, que antes ninguem checava.
    com_read_patient = (
        "FhirSummaryReader",
        "FhirSummaryReader:gustavo",
        "FhirSummaryReader:rafael",
        "FhirSummaryReader:andre",
    )
    formas = set()
    for nome in com_read_patient:
        metodos = _FAMILIA_POR_NOME[nome].metodos()
        assert "read_patient" in metodos, nome
        sig, e_coroutine = metodos["read_patient"]
        formas.add((_forma_assinatura(sig), e_coroutine))
    assert len(formas) == 1, f"read_patient diverge entre as 4 entradas FhirSummaryReader: {formas}"
    assert "read_patient" not in _FAMILIA_POR_NOME["FhirSummaryReader:carolina"].metodos()

    formas_resumo = set()
    for nome in ("FhirSummaryReader", "FhirSummaryReader:carolina"):
        metodos = _FAMILIA_POR_NOME[nome].metodos()
        assert "read_patient_summary" in metodos, nome
        sig, e_coroutine = metodos["read_patient_summary"]
        formas_resumo.add((_forma_assinatura(sig), e_coroutine))
    assert len(formas_resumo) == 1, f"read_patient_summary diverge entre as 2 entradas: {formas_resumo}"

    assert "search_coverage" in _FAMILIA_POR_NOME["FhirSummaryReader:rafael"].metodos()
    for nome in (
        "FhirSummaryReader",
        "FhirSummaryReader:gustavo",
        "FhirSummaryReader:carolina",
        "FhirSummaryReader:andre",
    ):
        assert "search_coverage" not in _FAMILIA_POR_NOME[nome].metodos(), nome


def test_formas_de_send_sao_tres_e_distintas_hoje() -> None:
    """Premissa da desambiguacao por FORMA (respaldo SECUNDARIO, §Delta-F7 item 2): hoje ha'
    exatamente 3 formas distintas de `send` entre as 6 familias atomizadas que o declaram
    (`WhatsAppSender:helena`/`:fernando` colapsam numa forma so'; `KafkaLike`/`FactBrokerPublisher`
    noutra, por design, com `WhatsAppSender:lucas` junto desde LUC-08) -- se esta premissa mudar
    (uma forma nova aparecer,
    ou duas formas hoje distintas colidirem), este teste aponta a causa antes da cerca de
    completude virar ruidosa demais para o proximo falso legitimo."""
    formas = _familias_por_forma_de_send()
    assert len(formas) == 3
    achatado = {familia for familias in formas.values() for familia in familias}
    assert achatado == {
        "WhatsAppSender:helena",
        "WhatsAppSender:fernando",
        "WhatsAppSender:lucas",
        "BrRegionalTransport",
        "KafkaLike",
        "FactBrokerPublisher",
    }
    # TREM train-b: continuam sendo 3 formas, mas a PERTINENCIA mudou. O WP W4-HYGIENE (LUC-08)
    # acrescentou `*, idempotency_key: str` ao `WhatsAppSender` de lucas, entao a forma dele
    # (2 posicionais + 1 keyword-only) passou a COLIDIR com a de `KafkaLike`/`FactBrokerPublisher`
    # e saiu da forma de helena/fernando (2 posicionais). E' exatamente a mudanca de premissa que
    # este teste existe para apontar; a colisao e' inofensiva porque
    # `_formas_de_send_sao_realmente_intercambiaveis` compara NOMES, nao so' aridade, e portanto
    # nao deixa o desempate por forma escolher arbitrariamente entre lucas e as duas familias
    # Kafka-shaped.
    kafka_shaped = [familias for familias in formas.values() if "KafkaLike" in familias][0]
    assert set(kafka_shaped) == {"KafkaLike", "FactBrokerPublisher", "WhatsAppSender:lucas"}
    whatsapp_shaped = [familias for familias in formas.values() if "WhatsAppSender:helena" in familias][0]
    assert set(whatsapp_shaped) == {
        "WhatsAppSender:helena",
        "WhatsAppSender:fernando",
    }
    assert not _formas_de_send_sao_realmente_intercambiaveis(kafka_shaped)


def test_achados_estruturais_recusa_fakedmn_com_nomes_da_evidencia_reg04() -> None:
    """Sondagem sintetica com os NOMES EXATOS de REG-04 (`table`/`dmn_input`) -- prova que o
    helper aponta o defeito sem depender de nenhum arquivo real continuar quebrado."""
    fonte = "class _FakeDmn:\n    async def evaluate(self, table, dmn_input):\n        return ([], None)\n"
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "sintetico.py:2" in achados[0]
    assert "decision_key" in achados[0] and "table" in achados[0]


def test_achados_estruturais_aceita_fakedmn_com_nomes_certos() -> None:
    fonte = (
        "class _FakeDmn:\n"
        "    async def evaluate(self, decision_key, variables, *, tenant=None):\n"
        "        return ([], None)\n"
    )
    assert _achados_estruturais_em_fonte(fonte, "sintetico.py") == []


def test_achados_estruturais_recusa_whatsapp_com_nome_da_evidencia_reg04() -> None:
    """Sondagem sintetica com o NOME EXATO de REG-04 (`to` em vez de `to_hash`)."""
    fonte = "class _RecordingWhatsApp:\n    async def send(self, to, text):\n        return {}\n"
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "to_hash" in achados[0] and "`to`" in achados[0]


def test_achados_estruturais_aceita_whatsapp_com_nome_certo() -> None:
    fonte = "class _RecordingWhatsApp:\n    async def send(self, to_hash, text):\n        return {}\n"
    assert _achados_estruturais_em_fonte(fonte, "sintetico.py") == []


def test_achados_estruturais_distingue_kafkalike_de_br_regional_pela_forma() -> None:
    """Dois falsos `send` CORRETOS, formas diferentes -- nenhum e' apontado, e cada um bate com
    a familia certa (prova indireta: se a desambiguacao por forma estivesse comparando o
    `send` de BrRegionalTransport contra o Protocol errado, `request` teria virado ofensa)."""
    fonte_kafka = (
        "class _RecordingKafka:\n    async def send(self, topic, value, *, key=None):\n        return None\n"
    )
    fonte_br = "class _LyingUsageTransport:\n    async def send(self, request):\n        return None\n"
    assert _achados_estruturais_em_fonte(fonte_kafka, "sintetico.py") == []
    assert _achados_estruturais_em_fonte(fonte_br, "sintetico.py") == []


def test_achados_estruturais_recusa_send_com_forma_desconhecida() -> None:
    """Cerca de COMPLETUDE (brief, item 2): um falso `send` com uma forma que NAO bate com
    nenhum Protocol conhecido e' apontado — nunca ignorado em silencio, nem confundido com
    qualquer uma das 3 formas existentes."""
    fonte = "class _AmbiguoNaoRegistrado:\n    async def send(self, a, b, c, d):\n        return None\n"
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "COMPLETUDE" in achados[0]


def test_achados_estruturais_checa_start_stop_so_apos_ancorar_em_factbrokerpublisher() -> None:
    """`start`/`stop` sozinhos (sem `send`) NUNCA sao apontados (nao sao ancora independente —
    ver docstring do modulo); com um `send` de forma Kafka JUNTO, um `stop` com assinatura
    errada (parametro a mais) E' apontado."""
    fonte_isolado = (
        "class _OutraCoisaComStartStop:\n"
        "    async def start(self):\n        return None\n"
        "    async def stop(self, motivo):\n        return None\n"
    )
    assert _achados_estruturais_em_fonte(fonte_isolado, "sintetico.py") == []

    fonte_ancorado = (
        "class _RecordingPublisher:\n"
        "    async def start(self):\n        return None\n"
        "    async def stop(self, motivo):\n        return None\n"
        "    async def send(self, topic, value, *, key=None):\n        return None\n"
    )
    achados = _achados_estruturais_em_fonte(fonte_ancorado, "sintetico.py")
    assert len(achados) == 1
    assert ".stop diverge" in achados[0]
    assert "motivo" in achados[0]


# =================================================================================================
# (D) §Delta -- correcoes de verificacao independente (F1 sync/async em ancora sem colisao real,
# F2 parametro extra do falso, F3 exempcao estrutural para fixture NEGATIVA deliberada)
# =================================================================================================


def test_achados_estruturais_recusa_ancora_unica_com_sync_async_trocado() -> None:
    """§Delta F1: uma ancora UNICA (nome de metodo que so' uma familia desta tabela declara, ex.:
    `evaluate` de `DmnTransport`) com sync/async ERRADO e' uma ofensa -- NAO uma exclusao
    silenciosa da familia. A exclusao silenciosa de `_e_candidata_da_familia` so' faz sentido
    contra um Protocol FORA da tabela que colide de NOME (ex.: `LinearSub.evaluate`, sincrono,
    nada a ver com `DmnTransport`); aqui os nomes dos parametros estao certos e so' o sync/async
    esta' errado, que e' o proprio defeito P-17 que esta cerca existe para pegar."""
    fonte = (
        "class _FakeDmnSincrono:\n"
        "    def evaluate(self, decision_key, variables, *, tenant=None):\n"
        "        return ([], None)\n"
    )
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "sintetico.py:2" in achados[0]
    assert "`async def`" in achados[0] and "sincrono" in achados[0]


def test_achados_estruturais_recusa_whatsapp_send_sincrono_com_contexto_de_agente() -> None:
    """§Delta-F7 (substitui o antigo teste "...forma_unica", cuja premissa mudou com a atomizacao
    module-scoped): sem NENHUM contexto de arquivo, a forma de `send` (2 posicionais, 0 kwonly)
    hoje bate com TRES familias identicas (`WhatsAppSender:helena/fernando/lucas`) -- genuinamente
    ambiguo por forma sozinha, exclusao silenciosa (ver o teste de controle logo abaixo). Mas
    quando o arquivo constroi o grafo de um agente ESPECIFICO dinamicamente (o mesmo padrao real
    de `test_start_failure_routing.py::_graph_module`) e menciona esse agent id como
    string-literal, o CONTEXTO resolve para UMA UNICA familia -- o sync/async errado vira ofensa
    NOMEANDO-A, mesmo sem nenhuma diferenca de FORMA (item 2 do fix: contexto e' PRIMARIO, forma
    e' so' desempate SECUNDARIO)."""
    fonte = (
        "import importlib\n"
        "def _graph_module(agent_id):\n"
        "    return importlib.import_module(f'maezo.agents.{agent_id}.graph')\n"
        "AGENTE = 'lucas'\n"
        "class _RecordingWhatsAppSincrono:\n"
        "    def send(self, to_hash, text):\n        return {}\n"
    )
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "WhatsAppSender:lucas" in achados[0]


def test_achados_estruturais_ainda_exclui_em_silencio_whatsapp_sincrono_sem_contexto() -> None:
    """Controle NEGATIVO complementar ao teste acima: a MESMA classe sincrona, mas SEM o padrao
    de construcao dinamica de grafo nem nenhum agent id mencionado -- genuinamente ambigua entre
    as 3 familias `WhatsAppSender:*` (forma identica, nenhum sinal de qual agente), exclusao
    silenciosa, mesmo comportamento do caso Kafka/FactBrokerPublisher (proximo teste)."""
    fonte = "class _RecordingWhatsAppSincrono:\n    def send(self, to_hash, text):\n        return {}\n"
    assert _achados_estruturais_em_fonte(fonte, "sintetico.py") == []


def test_achados_estruturais_ainda_exclui_em_silencio_forma_ambigua_de_send_sincrona() -> None:
    """Controle NEGATIVO do §Delta F1: quando a forma de `send` bate com DUAS familias
    (Kafka/FactBrokerPublisher, ambiguidade REAL), um sync/async errado ainda e' exclusao
    silenciosa -- nao vira ofensa, porque a colisao aqui e' genuina (nao ha' como saber, so'
    pela forma, qual das duas o autor da fixture pretendia duck-typar). Preserva o comportamento
    que o brief do §Delta manda MANTER, nao so' o que manda consertar."""
    fonte = "class _AlgumaOutraCoisa:\n    def send(self, topic, value, *, key=None):\n        return None\n"
    assert _achados_estruturais_em_fonte(fonte, "sintetico.py") == []


def test_achados_estruturais_recusa_parametro_extra_posicional_com_default() -> None:
    """§Delta F2: um parametro posicional EXTRA (alem dos que o Protocol real declara), mesmo
    COM default, e' uma ofensa. Antes so' o caso SEM default (que quebraria um caller real com
    `TypeError: missing required positional argument`) era apontado; um extra COM default nao
    quebra um caller que so' passa o que o Protocol promete, mas ainda e' uma assinatura que NAO
    acompanha o Protocol real -- exatamente o que a docstring do modulo promete comparar 'item
    por item'."""
    fonte = (
        "class _FakeDmnComExtra:\n"
        "    async def evaluate(self, decision_key, variables, modo_debug: bool = False, *,"
        " tenant=None):\n"
        "        return ([], None)\n"
    )
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "modo_debug" in achados[0]


def test_achados_estruturais_recusa_parametro_extra_kwonly_com_default() -> None:
    """§Delta F2: variante keyword-only do mesmo caso (`modo_debug` extra, com default, ao lado
    do `tenant` correto)."""
    fonte = (
        "class _FakeDmnComExtraKwonly:\n"
        "    async def evaluate(self, decision_key, variables, *, tenant=None,"
        " modo_debug: bool = False):\n"
        "        return ([], None)\n"
    )
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "modo_debug" in achados[0]


def test_achados_estruturais_marcador_de_fixture_negativa_exime_classe_malformada() -> None:
    """§Delta F3: `__fence_negative_fixture__` exime uma classe DELIBERADAMENTE malformada (ex.:
    o par `_DriftedReader`/`_DriftedSender` de DOSSIER-BROAD-EXCEPT
    `tests/unit/agents/test_dossier_propagates_programming_errors.py`, que existe para provar que
    um `TypeError` de uma chamada mal formada PROPAGA em vez de ser engolido por um `except
    Exception`). A classe abaixo tem a MESMA especie de drift do teste
    `test_achados_estruturais_recusa_fakedmn_com_nomes_da_evidencia_reg04` (nomes trocados de
    `table`/`dmn_input`), mas o marcador a exime porque ela e' uma fixture negativa, nao um falso
    desatualizado por acidente."""
    fonte = (
        "class _DriftedDmnDeliberado:\n"
        "    __fence_negative_fixture__ = 'NEW-12: prova que TypeError propaga'\n"
        "    async def evaluate(self, table, dmn_input):\n"
        "        raise AssertionError('inalcancavel')\n"
    )
    assert _achados_estruturais_em_fonte(fonte, "sintetico.py") == []


def test_achados_estruturais_marcador_sobre_falso_conforme_e_ofensa() -> None:
    """§Delta F3: o marcador so' pode exceptionar um duplo que REALMENTE diverge -- numa classe
    CONFORME (nomes certos, sync/async certo), o proprio marcador vira ofensa, para que nunca
    sirva de bypass de texto livre contra uma classe que na verdade acompanha o Protocol real
    (mutation do brief: 'marcador sobre um falso conforme')."""
    fonte = (
        "class _FakeDmnComMarcadorIndevido:\n"
        "    __fence_negative_fixture__ = 'X: nao deveria estar aqui'\n"
        "    async def evaluate(self, decision_key, variables, *, tenant=None):\n"
        "        return ([], None)\n"
    )
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "marcador" in achados[0].lower()
    assert "_FakeDmnComMarcadorIndevido" in achados[0]


def test_achados_estruturais_marcador_vazio_nao_exime() -> None:
    """`__fence_negative_fixture__` precisa ser uma string NAO VAZIA para valer como exempcao
    estrutural -- uma string vazia e' tratada como ausencia de marcador, entao a classe
    malformada abaixo continua sendo apontada normalmente (nunca um bypass por acidente de um
    atributo declarado mas esquecido em branco)."""
    fonte = (
        "class _FakeDmnComMarcadorVazio:\n"
        "    __fence_negative_fixture__ = ''\n"
        "    async def evaluate(self, table, dmn_input):\n"
        "        return ([], None)\n"
    )
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1


def test_falsos_estruturais_em_tests_acompanham_o_protocol_real() -> None:
    """P-17: todo falso em `tests/` que duck-typa um dos 10 Protocols da tabela `_FAMILIAS`
    acompanha a assinatura REAL — mesma especie do defeito `f1bc87f`/CC-12 (secoes A/B acima),
    generalizada pelo achado novo NEW-12/NEW-C1-2 (ASSURANCE-F1-C1.md) e concretizada em REG-04
    (ASSURANCE-F1-D.md): `_FakeDmn`/`_RecordingWhatsApp`/`_FakeWhatsApp` com nomes de parametro
    inteiramente diferentes do Protocol real, em 4 modulos de teste."""
    arquivos = sorted(_TESTS_ROOT.rglob("*.py"))
    assert arquivos, f"nenhum arquivo em {_TESTS_ROOT} — cerca sem alvo"

    ofensores: list[str] = []
    for caminho in arquivos:
        ofensores.extend(_achados_estruturais_em_arquivo(caminho))

    assert not ofensores, (
        "falso estrutural em tests/ desalinhado do Protocol real que finge implementar (P-17, "
        "NEW-12/NEW-C1-2/REG-04):\n" + "\n".join(ofensores)
    )

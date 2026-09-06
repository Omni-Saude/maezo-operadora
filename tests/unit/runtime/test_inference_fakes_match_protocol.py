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
    """Um Protocol real (ou um grupo de Protocols DIFERENTES, IDENTICOS por design -- ex.:
    `FhirSummaryReader`/`FhirReader`/`SummaryReader`/`PatientSummaryReader`-de-andre, todos
    `async def read_patient(patient_id) -> dict`) mais os achados que motivam vigia-lo."""

    nome: str
    achados: tuple[str, ...]
    membros: tuple[type, ...]

    def metodos(self) -> dict[str, tuple[inspect.Signature, bool]]:
        """nome -> (assinatura canonica, e' coroutine). Uniao dos metodos PROPRIOS de cada
        membro; se dois membros declararem o MESMO nome com formas diferentes, a premissa
        "sao identicos por design" quebrou -- o `assert` abaixo aponta a causa direto, antes de
        esta familia virar uma fonte de falso-positivo generalizado para todo falso real."""
        canonico: dict[str, tuple[inspect.Signature, bool]] = {}
        for membro in self.membros:
            for nome, fn in _metodos_proprios(membro).items():
                entrada = (inspect.signature(fn), inspect.iscoroutinefunction(fn))
                if nome in canonico:
                    sig_antiga, async_antigo = canonico[nome]
                    assert _forma_assinatura(sig_antiga) == _forma_assinatura(entrada[0]) and (
                        async_antigo == entrada[1]
                    ), (
                        f"familia {self.nome!r}: membros divergem em `{nome}` -- "
                        f"{sig_antiga} (async={async_antigo}) vs {entrada[0]} (async={entrada[1]}) "
                        f"em {membro.__module__}.{membro.__qualname__} -- a premissa de que estes "
                        "Protocols sao identicos por design quebrou; corrija a tabela `_FAMILIAS` "
                        "ou o Protocol que divergiu, nunca ignore esta divergencia"
                    )
                else:
                    canonico[nome] = entrada
        return canonico


_FAMILIAS: tuple[_Familia, ...] = (
    _Familia("DmnTransport", ("NEW-12", "NEW-C1-2", "REG-04"), (DmnTransport,)),
    _Familia(
        "WhatsAppSender",
        ("NEW-12", "NEW-C1-2", "REG-04"),
        (_HelenaWhatsAppSender, _FernandoWhatsAppSender, _LucasWhatsAppSender),
    ),
    _Familia("IdempotencyStore", ("NEW-12", "NEW-C1-2"), (IdempotencyStore,)),
    _Familia("TenantKeyset", ("NEW-12", "NEW-C1-2"), (TenantKeyset,)),
    _Familia(
        "FhirSummaryReader",
        ("NEW-12", "NEW-C1-2"),
        (
            FhirSummaryReader,
            _GustavoFhirReader,
            _RafaelFhirReader,
            _CarolinaSummaryReader,
            _AndrePatientSummaryReader,
        ),
    ),
    _Familia("BrRegionalTransport", ("NEW-12", "NEW-C1-2"), (BrRegionalTransport,)),
    _Familia("OutboxClaimStore", ("NEW-12", "NEW-C1-2"), (OutboxClaimStore,)),
    _Familia("FactBrokerPublisher", ("NEW-12", "NEW-C1-2"), (FactBrokerPublisher,)),
    _Familia("AuditEmitter", ("NEW-12", "NEW-C1-2"), (_HarnessAuditEmitter, _DispatcherAuditEmitter)),
    _Familia("KafkaLike", ("NEW-12", "NEW-C1-2"), (KafkaLike,)),
)

_FAMILIA_POR_NOME: dict[str, _Familia] = {familia.nome: familia for familia in _FAMILIAS}

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


def _nomes_unicos() -> dict[str, str]:
    """nome de metodo -> nome da UNICA familia que o declara, entre as 10 desta tabela --
    exclui nomes ambiguos (`send`, hoje compartilhado por 4 familias) e os genericos-demais de
    `_NOMES_SOMENTE_SECUNDARIOS`. Recalculado a cada chamada (nunca cacheado em modulo) porque
    depende de `familia.metodos()`, que roda o AUTO-TESTE de consistencia entre membros."""
    contagem: dict[str, list[str]] = {}
    for familia in _FAMILIAS:
        for nome in familia.metodos():
            if nome in _NOMES_SOMENTE_SECUNDARIOS:
                continue
            contagem.setdefault(nome, []).append(familia.nome)
    return {nome: familias[0] for nome, familias in contagem.items() if len(familias) == 1}


def _familias_por_forma_de_send() -> dict[tuple[int, int, bool, bool], list[str]]:
    """FORMA real de `send` (o unico nome ambiguo) -> familias que a declaram -- montado em
    tempo de execucao a partir dos Protocols reais, nunca hardcoded, para que uma mudanca futura
    na aridade de qualquer um mude este mapa sozinha."""
    mapa: dict[tuple[int, int, bool, bool], list[str]] = {}
    for familia in _FAMILIAS:
        metodos = familia.metodos()
        if "send" not in metodos:
            continue
        sig, _ = metodos["send"]
        mapa.setdefault(_forma_assinatura(sig), []).append(familia.nome)
    return mapa


def _classes_de_fonte(fonte: str) -> list[ast.ClassDef]:
    try:
        arvore = ast.parse(fonte)
    except SyntaxError:
        return []
    return [n for n in ast.walk(arvore) if isinstance(n, ast.ClassDef)]


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
    """Mensagem de ofensa para um sync/async ERRADO numa ancora SEM risco de colisao real (nome
    unico na tabela via `_nomes_unicos`, OU forma de `send` que resolve para uma UNICA familia) --
    §Delta F1: antes deste caso virava exclusao silenciosa da familia inteira (comportamento de
    `_e_candidata_da_familia`, que so' e' correto quando existe colisao real com outro Protocol --
    fora desta tabela, ou dentro dela via forma GENUINAMENTE ambigua de `send`, compartilhada por
    DUAS OU MAIS familias)."""
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


def _achados_estruturais_em_fonte(fonte: str, rotulo: str) -> list[str]:
    achados: list[str] = []
    unicos = _nomes_unicos()
    formas_send = _familias_por_forma_de_send()
    for classe in _classes_de_fonte(fonte):
        metodos_classe = _metodos_proprios_ast(classe)
        familias_ancoradas: set[str] = set()
        achados_da_classe: list[str] = []

        for nome_metodo, no in metodos_classe.items():
            if nome_metodo == _NOME_COM_DISCRIMINADOR_PROPRIO:
                continue  # coberto pelas secoes (A)/(B) acima (marcador `phi`)
            if nome_metodo in _NOMES_SOMENTE_SECUNDARIOS:
                continue  # so' checados no laco de bonus abaixo, nunca como gatilho proprio
            if nome_metodo in unicos:
                nome_familia = unicos[nome_metodo]
                familia = _FAMILIA_POR_NOME[nome_familia]
                sig_real, async_real = familia.metodos()[nome_metodo]
                if not _e_candidata_da_familia(no, async_real):
                    # §Delta F1: um nome de metodo UNICO na tabela (nenhuma das 10 familias
                    # colide) ainda pode colidir com um Protocol de FORA da tabela (ex.:
                    # `tests/support/dmn_first_hit.py::LinearSub.evaluate`, um simulador de
                    # tabela sincrono com 1 posicional e 0 kwonly; `test_effect_pep.py::
                    # _StubPep.evaluate`, que duck-typa `PepEvaluator`, 2 posicionais e 0
                    # kwonly -- ver docstring do modulo). So' tratar o sync/async errado como
                    # ofensa (em vez de exclusao de familia) quando a FORMA do falso (contagem
                    # posicional/kwonly, *args/**kwargs -- tudo MENOS o proprio sync/async, que
                    # e' exatamente o que esta errado) bate com a do Protocol real: aí' nao ha'
                    # Protocol plausivel fora da tabela com a MESMA forma E o MESMO nome de
                    # metodo por coincidencia -- e' o proprio defeito P-17 na MESMA familia.
                    if _forma_assinatura(sig_real) == _forma_assinatura_ast(no):
                        achados_da_classe.append(
                            _achado_sync_async(rotulo, classe, nome_metodo, no, nome_familia, async_real)
                        )
                    continue
                familias_ancoradas.add(nome_familia)
                achado = _achado_de_metodo(rotulo, classe, nome_metodo, no, familia, nome_familia)
                if achado:
                    achados_da_classe.append(achado)
                continue
            if nome_metodo == "send":
                forma_fake = _forma_assinatura_ast(no)
                candidatas = formas_send.get(forma_fake)
                if not candidatas:
                    achados_da_classe.append(
                        f"{rotulo}:{no.lineno} — classe `{classe.name}`.send tem forma "
                        f"(posicionais={forma_fake[0]}, kwonly={forma_fake[1]}, "
                        f"*args={forma_fake[2]}, **kwargs={forma_fake[3]}) que nao bate com "
                        "NENHUM Protocol `send` conhecido (WhatsAppSender, BrRegionalTransport, "
                        "KafkaLike, FactBrokerPublisher) — cerca de COMPLETUDE (P-17): registre "
                        "a forma nova em `_familias_por_forma_de_send` antes de prosseguir, nunca "
                        "ignore em silencio um falso ambiguo novo"
                    )
                    continue
                familias_ancoradas.update(candidatas)
                familia = _FAMILIA_POR_NOME[candidatas[0]]
                if len(candidatas) == 1:
                    # §Delta F1: a forma resolveu para UMA UNICA familia -- mesmo raciocinio do
                    # ramo `unicos` acima, so' que a ancora aqui e' por FORMA em vez de por nome.
                    # A ambiguidade genuina (a que continua justificando a exclusao silenciosa)
                    # so' existe quando DUAS OU MAIS familias compartilham a MESMA forma (Kafka/
                    # FactBrokerPublisher hoje).
                    _, async_real = familia.metodos()["send"]
                    if not _e_candidata_da_familia(no, async_real):
                        achados_da_classe.append(
                            _achado_sync_async(rotulo, classe, "send", no, candidatas[0], async_real)
                        )
                        continue
                achado = _achado_de_metodo(rotulo, classe, "send", no, familia, " / ".join(candidatas))
                if achado:
                    achados_da_classe.append(achado)

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
    achado novo de ASSURANCE-F1-C1.md (NEW-12/NEW-C1-2) -- se uma classe for renomeada/removida
    de `src/`, o IMPORT do topo do arquivo ja' quebra a colecao (falha alta, nao silenciosa);
    este teste torna a lista fechada tambem uma asserção viva, nao so' um comentario."""
    esperado = {
        "DmnTransport",
        "WhatsAppSender",
        "IdempotencyStore",
        "TenantKeyset",
        "FhirSummaryReader",
        "BrRegionalTransport",
        "OutboxClaimStore",
        "FactBrokerPublisher",
        "AuditEmitter",
        "KafkaLike",
    }
    assert {familia.nome for familia in _FAMILIAS} == esperado


def test_familias_tem_ao_menos_um_metodo_proprio_cada() -> None:
    """Sanidade: nenhuma familia da tabela e' um Protocol vazio (o que tornaria a cerca vacua
    para ela sem nenhum sinal)."""
    for familia in _FAMILIAS:
        assert familia.metodos(), f"familia {familia.nome!r} nao declara nenhum metodo proprio"


def test_membros_de_uma_familia_concordam_entre_si() -> None:
    """Exercita o auto-teste de `_Familia.metodos()` num caso REAL (nao precisa quebrar nada
    hoje): `FhirSummaryReader`/`FhirReader`(gustavo)/`FhirReader`(rafael)/`SummaryReader`/
    `PatientSummaryReader`(andre) devem concordar em `read_patient` (mesma forma, mesmo
    sync/async) -- e' a premissa citada no proprio docstring de `FhirSummaryReader`."""
    familia = _FAMILIA_POR_NOME["FhirSummaryReader"]
    metodos = familia.metodos()
    assert "read_patient" in metodos
    assert "search_coverage" in metodos  # so' `FhirReader` de rafael declara -- uniao, nao intersecao


def test_formas_de_send_sao_tres_e_distintas_hoje() -> None:
    """Premissa da desambiguacao por FORMA: hoje ha' exatamente 3 formas distintas de `send`
    entre as 4 familias que o declaram (`KafkaLike` e `FactBrokerPublisher` colapsam na MESMA
    forma, por design) -- se esta premissa mudar (uma quinta familia aparecer, ou duas formas
    hoje distintas colidirem), este teste aponta a causa antes da cerca de completude virar
    ruidosa demais para o proximo falso legitimo."""
    formas = _familias_por_forma_de_send()
    assert len(formas) == 3
    achatado = {familia for familias in formas.values() for familia in familias}
    assert achatado == {"WhatsAppSender", "BrRegionalTransport", "KafkaLike", "FactBrokerPublisher"}
    kafka_shaped = [familias for familias in formas.values() if "KafkaLike" in familias][0]
    assert set(kafka_shaped) == {"KafkaLike", "FactBrokerPublisher"}


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


def test_achados_estruturais_recusa_whatsapp_send_sincrono_forma_unica() -> None:
    """§Delta F1: `send` cuja FORMA resolve para uma UNICA familia (WhatsAppSender hoje, forma
    (2 posicionais, 0 kwonly) nao compartilhada por nenhuma outra familia desta tabela) tambem
    nao tem colisao real -- sync/async errado e' ofensa, nao exclusao silenciosa. A ambiguidade
    genuina so' existe quando DUAS OU MAIS familias compartilham a MESMA forma (Kafka/
    FactBrokerPublisher hoje), caso em que a exclusao silenciosa continua correta (ver proximo
    teste, controle negativo)."""
    fonte = "class _RecordingWhatsAppSincrono:\n    def send(self, to_hash, text):\n        return {}\n"
    achados = _achados_estruturais_em_fonte(fonte, "sintetico.py")
    assert len(achados) == 1
    assert "WhatsAppSender" in achados[0]


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

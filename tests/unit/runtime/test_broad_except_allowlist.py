"""Cerca AST: `except Exception` em `agents/**` e `runtime/**` e' um CONJUNTO FECHADO (NEW-12/REG-03).

POR QUE UMA CERCA, E NAO SO' O CONSERTO
----------------------------------------
O conserto de NEW-12 troca 25 `except Exception:` por
`except PROGRAMMING_ERRORS: raise` + `except EXTERNAL_DEPENDENCY_FAILURES:`
(`maezo.runtime.dependency_failures`). Sem cerca, o proximo no que precisar de um `try` volta a
escrever o `except Exception:` largo — foi assim que os 25 sitios apareceram — e o `TypeError` de
uma assinatura derivada volta a virar "dossie indisponivel" em silencio.

As tres afirmacoes desta cerca:

1. :func:`test_inventario_de_except_largo_bate_com_a_allowlist` — o conjunto de sitios largos que
   AINDA existem em `src/maezo/agents/**` + `src/maezo/runtime/**` e' EXATAMENTE
   :data:`BROAD_EXCEPT_ALLOWLIST`, chave a chave e CONTAGEM a contagem. Um `except Exception:`
   novo (ou um a mais dentro de um simbolo ja' listado) fica VERMELHO.
2. :func:`test_todo_item_da_allowlist_declara_um_motivo` — nenhuma entrada entra so' com o nome:
   a allowlist e' uma lista de EXCECOES JUSTIFICADAS, nao um `# noqa` distribuido.
3. :func:`test_todo_try_de_graph_py_re_levanta_erro_de_programacao` — em `agents/*/graph.py`,
   TODO `try` que (i) chama uma DEPENDENCIA INJETADA no corpo e (ii) tem alguma clausula que
   ABSORVERIA uma classe de bug tem, como PRIMEIRA clausula, `except PROGRAMMING_ERRORS: raise`.
   Isto e' o que fecha os quatro sitios que continuam largos por contrato (os envios WhatsApp
   best-effort): eles seguem absorvendo o fornecedor e passaram a NAO absorver o bug.

   §Delta-F3 — A AFIRMACAO ANTES NAO ERA O QUE SE MEDIA. A versao original dizia "largo OU
   narrow" e implementava um predicado que so' reconhecia (a) um handler largo e (b) o Name
   literal `EXTERNAL_DEPENDENCY_FAILURES`. QUALQUER outro handler estreito era invisivel para as
   DUAS asserçoes — nao era "largo", entao o inventario o ignorava; e nao "absorvia", entao a
   guarda nao era exigida. Medido: trocar as duas clausulas de `carolina::_build_dossier` por um
   unico `except RuntimeError:` deixava as 57 provas VERDES enquanto reabria exatamente o
   defeito de NEW-12 (um `NotImplementedError` de porto stub voltando a virar "dossie
   indisponivel"), e `except RuntimeError:` e' a coisa mais natural que o proximo desenvolvedor
   escreve. Agora a pergunta e' resolvida por MRO sobre as classes REAIS que o handler captura
   (nomes resolvidos nos globals do proprio modulo do grafo + builtins, com FALHA FECHADA em
   qualquer nome que nao resolva), e nao por reconhecimento de um literal.

   A conjuncao com "chama uma dependencia injetada" e' o que mantem a regra HONESTA em vez de
   apenas severa: `andre::_metric_value_is_admissible` (`except (ArithmeticError, TypeError,
   ValueError)` em volta de `math.isfinite`) e `helena::_parse_json_object` (`except (ValueError,
   TypeError)` em volta de `json.loads`) capturam classes de bug DE PROPOSITO, sobre computacao
   local e pura — nao ha' porto ali para um bug de porto se esconder atras. Exigir a guarda
   deles seria ruido, e ruido e' como uma cerca vira `# noqa` coletivo.

Ler `src/` por AST, nunca por `grep`: `except Exception` aparece em docstring e comentario deste
proprio repositorio (o docstring de `gateway/seams/_base.py` cita a frase), e um grep contaria
prosa como codigo.
"""

from __future__ import annotations

import ast
import builtins
import importlib
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from maezo.runtime.dependency_failures import (
    DECLARED_INFERENCE_FAILURES,
    EXTERNAL_DEPENDENCY_FAILURES,
    PROGRAMMING_ERRORS,
)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SCANNED_ROOTS: Final[tuple[str, ...]] = ("src/maezo/agents", "src/maezo/runtime")

#: Quantos `try` de `agents/*/graph.py` a afirmacao 3 cobre HOJE (§Delta-F3): 25 sitios
#: estreitados + os 4 envios WhatsApp que continuam largos por contrato. Pino de NAO-VACUIDADE —
#: ver `test_a_afirmacao_3_cobre_os_sitios_de_fronteira_esperados`.
#: 09/09/2026: 30 — `HelenaGraph.collect` (passo 4 da triagem) ganhou o mesmo `try` estreitado
#: dos outros sitios de LLM da Helena (`PROGRAMMING_ERRORS` re-levanta, `EXTERNAL_DEPENDENCY_FAILURES`
#: cai para uma PERGUNTA de fallback — nunca resposta clinica).
_SITIOS_COM_GUARDA_EXIGIDA: Final[int] = 30

#: `file::simbolo` -> (quantos `except` largos aquele simbolo ainda tem, POR QUE).
#:
#: Fechado de proposito. Cada entrada foi lida uma a uma nesta sessao; o motivo e' o do proprio
#: comentario do sitio, nao uma racionalizacao escrita aqui. Duas familias, e nenhuma delas
#: absorve um erro de programacao para dentro de um resultado de negocio:
#:
#:  (a) CONTA-E-RE-LEVANTA — o `except` existe para incrementar `maezo_agent_errors_total` e
#:      levanta de novo na linha seguinte (ALERTS-WITHOUT-METRICS-a). Ja' pinado por
#:      `tests/unit/platform/test_alert_metrics_fence.py::
#:      test_every_graph_invocation_in_src_counts_agent_errors`.
#:  (b) BRING-UP / FIREWALL DE SDK / TELEMETRIA — codigo que roda FORA de um turno de negocio:
#:      isolamento de dependencia no arranque do daemon (uma dependencia quebrada deixa o
#:      health-check vermelho, nunca derruba as outras), o firewall que impede um tipo de SDK de
#:      vazar do modulo de inferencia, e emissores de metrica best-effort.
#:
#:  (c) ENVIO BEST-EFFORT (os quatro sitios de `graph.py` que sobraram largos): a superficie de
#:      falha DECLARADA de `WhatsAppServer.send_message` inclui um `ValueError` cru
#:      ("phone_number_id is not configured — refusing to send"), e estreitar para
#:      `EXTERNAL_DEPENDENCY_FAILURES` trocaria um bug engolido por um TURNO DERRUBADO num
#:      ambiente mal configurado — pior para o beneficiario e para a escalacao ja' aberta. Ficam
#:      largos, e a afirmacao 3 desta cerca garante que o bug propaga mesmo assim.
BROAD_EXCEPT_ALLOWLIST: Final[dict[str, tuple[int, str]]] = {
    # -- (a) conta-e-re-levanta -----------------------------------------------------------
    "src/maezo/agents/andre/delegation.py::make_andre_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/beatriz/delegation.py::make_beatriz_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/carolina/delegation.py::make_carolina_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/fernando/delegation.py::make_fernando_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/gustavo/delegation.py::make_gustavo_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/marina/delegation.py::make_marina_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/rafael/delegation.py::make_rafael_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/agents/valentina/delegation.py::make_valentina_handler::handler": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    "src/maezo/runtime/harness.py::Harness::invoke": (
        1,
        "conta maezo_agent_errors_total no seam ainvoke e RE-LEVANTA (ALERTS-WITHOUT-METRICS-a)",
    ),
    # -- (b) bring-up / firewall de SDK / telemetria ---------------------------------------
    "src/maezo/runtime/agent_runtime/ingress.py::build_ingress_router::receber_solicitacao": (
        1,
        "borda HTTP: traduz QUALQUER falha do turno em 502 depois de logar; nada continua degradado",
    ),
    "src/maezo/runtime/agent_runtime/service.py::_probe_a2a_audit_sink": (
        1,
        "sonda de prontidao: qualquer falha significa 'not ready' (False), nunca propaga",
    ),
    "src/maezo/runtime/agent_runtime/service.py::_bring_up_dependencies": (
        8,
        "isolamento de bring-up (design 10/Q-6): cada dependencia que falha deixa o SEU health-check "
        "vermelho sem derrubar as outras; nenhum turno de negocio roda aqui",
    ),
    "src/maezo/runtime/checkpoint.py::Checkpointer::connect_and_setup": (
        1,
        "BaseException de proposito: fecha o pool no caminho de erro (inclui CancelledError) e "
        "RE-LEVANTA na linha seguinte",
    ),
    "src/maezo/runtime/checkpoint.py::provision_checkpointer": (
        1,
        "captura o erro de setup do saver para a decisao fail-closed-em-prod / fallback-em-dev",
    ),
    "src/maezo/runtime/inference/__init__.py::InferenceProvider::_resolve_task_model": (
        1,
        "emissor de metrica de tier: telemetria nunca pode quebrar uma geracao",
    ),
    "src/maezo/runtime/inference/br_regional.py::BedrockBrRegionalTransport::send": (
        1,
        "firewall de SDK: converte a excecao do botocore em BrRegionalTransportUnavailableError e "
        "RE-LEVANTA tipada (nenhum tipo de SDK escapa deste modulo)",
    ),
    "src/maezo/runtime/inference/br_resident_provider.py::BrResidentInferenceProvider::_send": (
        1,
        "firewall de transporte: converte em BrRegionalTransportUnavailableError e RE-LEVANTA tipada",
    ),
    "src/maezo/runtime/inference/providers.py::_emit_llm_token_usage": (
        1,
        "medicao de tokens best-effort: nunca pode quebrar nem travar uma chamada de LLM",
    ),
    "src/maezo/runtime/inference/providers.py::BedrockInferenceProvider::generate": (
        1,
        "firewall de SDK: a hierarquia do botocore (NoCredentialsError, ProfileNotFound, ...) vira "
        "InferenceProviderError e RE-LEVANTA tipada",
    ),
    "src/maezo/runtime/worker_runtime/service.py::_probe_audit_sink": (
        1,
        "sonda de prontidao: qualquer falha significa 'not ready' (False), nunca propaga",
    ),
    "src/maezo/runtime/worker_runtime/service.py::_bring_up_dependencies": (
        10,
        "isolamento de bring-up do daemon de workers: cada construcao que falha deixa o SEU "
        "health-check vermelho; nenhum turno de negocio roda aqui",
    ),
    # -- (c) envio best-effort (ver a nota da allowlist) ------------------------------------
    "src/maezo/agents/fernando/graph.py::FernandoGraph::notify": (
        1,
        "envio WhatsApp best-effort: a falha declarada do porto inclui ValueError cru de credencial "
        "nao configurada; estreitar derrubaria o turno num ambiente mal configurado",
    ),
    "src/maezo/agents/helena/graph.py::HelenaGraph::respond": (
        1,
        "envio WhatsApp best-effort: idem; a falha ja' e' SUPERFICIADA em state['error'] (HEL-05)",
    ),
    "src/maezo/agents/lucas/graph.py::LucasGraph::send_escalation_ack": (
        1,
        "ack best-effort: uma falha de ack nunca pode desfazer uma escalacao ja' viva (ack_pending)",
    ),
    "src/maezo/agents/lucas/graph.py::LucasGraph::respond_member": (
        1,
        "envio WhatsApp best-effort: idem fernando/notify",
    ),
}


def _qualified_symbol_map(tree: ast.Module) -> dict[int, str]:
    """`id(no)` -> `A::B::C` do escopo (classe/funcao) que CONTEM o no."""
    mapping: dict[int, str] = {}

    def walk(node: ast.AST, chain: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            mapping[id(child)] = "::".join(chain)
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, (*chain, child.name))
            else:
                walk(child, chain)

    walk(tree, ())
    return mapping


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """`except:`, `except Exception/BaseException:` ou uma tupla que contenha uma das duas."""
    declared = handler.type
    if declared is None:
        return True
    if isinstance(declared, ast.Name):
        return declared.id in {"Exception", "BaseException"}
    if isinstance(declared, ast.Tuple):
        return any(
            isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"} for item in declared.elts
        )
    return False


def _python_sources() -> list[Path]:
    files: list[Path] = []
    for root in _SCANNED_ROOTS:
        files.extend(sorted((_REPO_ROOT / root).rglob("*.py")))
    assert files, f"nenhum arquivo varrido sob {_SCANNED_ROOTS} — a cerca estaria vazia"
    return files


def _parse(path: Path) -> ast.Module:
    # Binario + decode explicito: ha' arquivos CRLF na arvore e `ast.parse` normaliza sozinho.
    return ast.parse(path.read_bytes().decode("utf-8"))


def _broad_except_inventory() -> dict[str, int]:
    inventory: dict[str, int] = {}
    for path in _python_sources():
        tree = _parse(path)
        scopes = _qualified_symbol_map(tree)
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            broad = sum(1 for handler in node.handlers if _is_broad(handler))
            if not broad:
                continue
            key = f"{rel}::{scopes.get(id(node), '') or '<module>'}"
            inventory[key] = inventory.get(key, 0) + broad
    return inventory


def test_inventario_de_except_largo_bate_com_a_allowlist() -> None:
    """O conjunto de `except` largos que sobrou e' EXATAMENTE o declarado — chave e contagem."""
    measured = _broad_except_inventory()
    expected = {key: count for key, (count, _) in BROAD_EXCEPT_ALLOWLIST.items()}

    novos = sorted(set(measured) - set(expected))
    sumidos = sorted(set(expected) - set(measured))
    divergentes = sorted(
        f"{key}: allowlist={expected[key]} medido={measured[key]}"
        for key in set(expected) & set(measured)
        if expected[key] != measured[key]
    )

    assert not novos, (
        "`except Exception`/`except:` NOVO fora da allowlist. Use "
        "`except PROGRAMMING_ERRORS: raise` + `except EXTERNAL_DEPENDENCY_FAILURES:` "
        f"(maezo.runtime.dependency_failures), ou justifique aqui: {novos}"
    )
    assert not sumidos, f"entrada da allowlist que nao existe mais no codigo (remova-a): {sumidos}"
    assert not divergentes, f"contagem de `except` largo mudou dentro do simbolo: {divergentes}"


def test_todo_item_da_allowlist_declara_um_motivo() -> None:
    """Uma allowlist sem motivo e' um `# noqa` distribuido; cada entrada declara POR QUE."""
    sem_motivo = sorted(
        key for key, (_, motivo) in BROAD_EXCEPT_ALLOWLIST.items() if len(motivo.strip()) < 40
    )
    assert not sem_motivo, f"entrada da allowlist sem motivo utilizavel: {sem_motivo}"


def _guard_is_programming_errors_reraise(handler: ast.ExceptHandler) -> bool:
    declared = handler.type
    if not (isinstance(declared, ast.Name) and declared.id == "PROGRAMMING_ERRORS"):
        return False
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None


def _chama_dependencia_injetada(node: ast.Try) -> bool:
    """O CORPO do `try` chama uma dependencia injetada — `self._<porto>.<metodo>(...)`?

    E' a forma unica de toda chamada de porto nestes grafos (`self._llm.generate`,
    `self._fhir.read_patient*`, `self._population.*`, `self._whatsapp.send`) e a linha que separa
    "este `try` protege uma fronteira" de "este `try` protege uma conta local".
    """
    for stmt in node.body:
        for inner in ast.walk(stmt):
            if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Attribute):
                continue
            alvo = inner.func.value
            if (
                isinstance(alvo, ast.Attribute)
                and isinstance(alvo.value, ast.Name)
                and alvo.value.id == "self"
                and alvo.attr.startswith("_")
            ):
                return True
    return False


def _classes_capturadas(
    handler: ast.ExceptHandler, modulo: ModuleType
) -> tuple[type[BaseException], ...] | None:
    """As classes REAIS que este handler captura, ou `None` se algum nome nao resolver.

    `None` e' FALHA FECHADA de proposito: um handler cuja forma esta cerca nao consegue resolver
    (uma expressao computada, um alias importado que sumiu) e' tratado como absorvente, e portanto
    passa a EXIGIR a guarda. Uma cerca que "assume que esta tudo bem" no que nao entende e' uma
    cerca que nao vale o arquivo em que esta escrita.
    """
    declared = handler.type
    if declared is None:
        return (BaseException,)
    if isinstance(declared, ast.Name):
        nomes = [declared.id]
    elif isinstance(declared, ast.Tuple) and all(isinstance(e, ast.Name) for e in declared.elts):
        nomes = [e.id for e in declared.elts if isinstance(e, ast.Name)]
    else:
        return None

    resolvidas: list[type[BaseException]] = []
    for nome in nomes:
        obj: Any = getattr(modulo, nome, None)
        if obj is None:
            obj = getattr(builtins, nome, None)
        if obj is None:
            return None
        candidatas = obj if isinstance(obj, tuple) else (obj,)
        for candidata in candidatas:
            if not (isinstance(candidata, type) and issubclass(candidata, BaseException)):
                return None
            resolvidas.append(candidata)
    return tuple(resolvidas)


def _e_re_levantamento_nu(handler: ast.ExceptHandler) -> bool:
    """Um handler cujo corpo e' SO' `raise` nao absorve nada — ele apenas re-levanta."""
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None


def _absorve_classe_de_bug(handler: ast.ExceptHandler, modulo: ModuleType) -> bool:
    """Este handler ENGOLIRIA algum item de :data:`PROGRAMMING_ERRORS`?"""
    if _e_re_levantamento_nu(handler):
        return False
    capturadas = _classes_capturadas(handler, modulo)
    if capturadas is None:
        return True  # fail-closed: ver `_classes_capturadas`
    return any(issubclass(bug, capturadas) for bug in PROGRAMMING_ERRORS)


def _exige_clausula_de_guarda(node: ast.Try, modulo: ModuleType) -> bool:
    """A regra da afirmacao 3, inteira: fronteira injetada E alguma clausula que engole bug."""
    if not _chama_dependencia_injetada(node):
        return False
    return any(_absorve_classe_de_bug(handler, modulo) for handler in node.handlers)


def _modulo_do_grafo(graph_path: Path) -> ModuleType:
    return importlib.import_module(f"maezo.agents.{graph_path.parent.name}.graph")


def _sitios_que_exigem_guarda(graph_path: Path) -> list[tuple[str, ast.Try]]:
    tree = _parse(graph_path)
    scopes = _qualified_symbol_map(tree)
    modulo = _modulo_do_grafo(graph_path)
    rel = graph_path.relative_to(_REPO_ROOT).as_posix()
    return [
        (f"{rel}::{scopes.get(id(node), '') or '<module>'} (try na linha {node.lineno})", node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Try) and _exige_clausula_de_guarda(node, modulo)
    ]


@pytest.mark.parametrize(
    "graph_path",
    sorted((_REPO_ROOT / "src/maezo/agents").glob("*/graph.py")),
    ids=lambda p: p.parent.name,
)
def test_todo_try_de_graph_py_re_levanta_erro_de_programacao(graph_path: Path) -> None:
    """Em `graph.py`, todo `try` que absorve falha de dependencia re-levanta o BUG primeiro.

    Cobre os dois lados do conserto de uma vez: os 25 sitios estreitados (onde a primeira clausula
    e' o que impede `NotImplementedError`/`RecursionError` — subclasses de `RuntimeError` — de
    serem absorvidas) e os quatro que continuam largos por contrato (onde ela e' o UNICO motivo
    pelo qual um `TypeError` de assinatura derivada nao vira mais 'envio indisponivel').
    """
    faltando = [
        rotulo
        for rotulo, node in _sitios_que_exigem_guarda(graph_path)
        if not (node.handlers and _guard_is_programming_errors_reraise(node.handlers[0]))
    ]
    assert not faltando, (
        "`try` que chama uma dependencia injetada e absorve alguma classe de bug, sem "
        f"`except PROGRAMMING_ERRORS: raise` como PRIMEIRA clausula: {faltando}"
    )


def test_a_afirmacao_3_cobre_os_sitios_de_fronteira_esperados() -> None:
    """NAO-VACUIDADE da afirmacao 3: a regra vale sobre um numero PINADO de sitios reais.

    Sem este pino, um predicado que parasse de casar (um refactor que renomeie os atributos de
    porto, uma resolucao de nome que passe a devolver `None`) deixaria a afirmacao 3 verde por
    VACUIDADE — o modo de falha que esta cerca inteira existe para tornar impossivel. O numero e'
    a contagem MEDIDA hoje: 25 sitios estreitados + os 4 envios que continuam largos por
    contrato.
    """
    medidos = {
        rotulo
        for graph_path in sorted((_REPO_ROOT / "src/maezo/agents").glob("*/graph.py"))
        for rotulo, _ in _sitios_que_exigem_guarda(graph_path)
    }
    assert len(medidos) == _SITIOS_COM_GUARDA_EXIGIDA, (
        f"a afirmacao 3 passou a cobrir {len(medidos)} sitios (pino: "
        f"{_SITIOS_COM_GUARDA_EXIGIDA}). Se o conserto foi legitimo, mova o pino no MESMO commit "
        f"e diga por que: {sorted(medidos)}"
    )


def test_falhas_de_inferencia_declaradas_estao_cobertas_pelas_bases() -> None:
    """A cobertura por MRO das bases e' um FATO CHECAVEL, nao uma leitura de hierarquia.

    Se `InferenceProviderError` deixar de ser `RuntimeError`, ou `PhiZoneRoutingError` deixar de
    ser `PermissionError`, os 15 sitios de LLM parariam de degradar e passariam a estourar o
    turno. Esta afirmacao fica VERMELHA antes disso chegar a producao.
    """
    descobertas = [
        tipo.__name__
        for tipo in DECLARED_INFERENCE_FAILURES
        if not issubclass(tipo, EXTERNAL_DEPENDENCY_FAILURES)
    ]
    assert not descobertas, f"falha de inferencia DECLARADA fora das bases absorvidas: {descobertas}"


def test_as_duas_classes_de_bug_load_bearing_sao_subclasses_das_bases() -> None:
    """Prova que a clausula `except PROGRAMMING_ERRORS: raise` NAO e' decorativa.

    `NotImplementedError` e `RecursionError` sao subclasses de `RuntimeError`: sem a primeira
    clausula, um porto stub ou uma recursao infinita seriam absorvidos como "fornecedor
    indisponivel". Se algum dia deixarem de ser, esta afirmacao avisa que o motivo documentado da
    clausula mudou.
    """
    cobertas = {
        tipo.__name__ for tipo in PROGRAMMING_ERRORS if issubclass(tipo, EXTERNAL_DEPENDENCY_FAILURES)
    }
    assert cobertas == {"NotImplementedError", "RecursionError"}, (
        "o conjunto de erros de programacao que as bases absorveriam mudou; reveja o motivo "
        f"documentado da clausula de guarda: {sorted(cobertas)}"
    )

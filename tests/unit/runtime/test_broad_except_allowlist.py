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
   TODO `try` que absorve falha de dependencia (largo OU narrow) tem, como PRIMEIRA clausula,
   `except PROGRAMMING_ERRORS: raise`. Isto e' o que fecha os quatro sitios que continuam largos
   por contrato (os envios WhatsApp best-effort): eles seguem absorvendo o fornecedor e passaram a
   NAO absorver o bug.

Ler `src/` por AST, nunca por `grep`: `except Exception` aparece em docstring e comentario deste
proprio repositorio (o docstring de `gateway/seams/_base.py` cita a frase), e um grep contaria
prosa como codigo.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SCANNED_ROOTS: Final[tuple[str, ...]] = ("src/maezo/agents", "src/maezo/runtime")

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
            isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"}
            for item in declared.elts
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
    return (
        len(handler.body) == 1
        and isinstance(handler.body[0], ast.Raise)
        and handler.body[0].exc is None
    )


def _absorbs_dependency_failure(node: ast.Try) -> bool:
    for handler in node.handlers:
        if _is_broad(handler):
            return True
        declared = handler.type
        if isinstance(declared, ast.Name) and declared.id == "EXTERNAL_DEPENDENCY_FAILURES":
            return True
    return False


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
    tree = _parse(graph_path)
    scopes = _qualified_symbol_map(tree)
    rel = graph_path.relative_to(_REPO_ROOT).as_posix()

    faltando = [
        f"{rel}::{scopes.get(id(node), '') or '<module>'} (try na linha {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and _absorbs_dependency_failure(node)
        and not (node.handlers and _guard_is_programming_errors_reraise(node.handlers[0]))
    ]
    assert not faltando, (
        "`try` que absorve falha de dependencia sem `except PROGRAMMING_ERRORS: raise` como "
        f"PRIMEIRA clausula: {faltando}"
    )

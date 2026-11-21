"""E-SF-17r, parte de agente: estado FECHADO do envio ao prospect, só com cercas NOVAS.

A parte de agente da 17r não tem entrada própria na pasta de autonomia (CD-153/233): o que ela
exerceria é `send_prospect_message` (L3) por um despachante comercial, e a classe de efeito
`comunicacao_prospect` está ADIADA (sem superfície, fora de `effect_classes`, sem despachante). Nada
em `src/` muda; estes testes pinam o que ainda não estava pinado:

(i)   `comunicacao_prospect` não é classe de efeito e `send_prospect_message` não é operação (nem
      ação de autonomia de nenhuma operação) em `gateway/effect_classes.py`;
(ii)  nenhum módulo de `src/` fora do próprio pacote importa `maezo.agents.comercial`;
(iii) o grafo comercial não chama envio, cliente MCP nem gateway, e não importa nada disso; a ação
      de envio volta só como DADO, também fora do primeiro turno.

Cada cerca tem controle positivo do detector. Ficaram de fora, por já estarem pinadas: a rota
comercial do WhatsApp com a chave desligada (`tests/unit/platform/webhooks/whatsapp/
test_gtme12b_rota_comercial.py`) e o `build` desligado (`test_esf17_comercial.py::
test_flag_off_build_recusa`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from maezo.agents.comercial import graph
from maezo.agents.comercial.graph import (
    ACAO_ENVIO_PROSPECT,
    ROTA_FILA_HUMANA,
    build,
    new_comercial_state,
)
from maezo.gateway import effect_classes

_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO / "src" / "maezo"
_PACOTE = _SRC / "agents" / "comercial"
_PASSAGEM = {
    "schema_version": "esf17-passagem-comercial.proposed.v1",
    "para": "comercial",
    "motivo": "intencao_contratar",
    "message_ref": "hk1_" + "b" * 64,
}

_CLASSE_ADIADA = "comunicacao_prospect"


# --- (i) classe e operação do envio ao prospect ausentes do catálogo de efeitos -------------------


def _catalogo_conhece(classe: str | None = None, acao: str | None = None) -> bool:
    if classe is not None and (
        classe in effect_classes.ACTION_CLASSES
        or any(op.action_class == classe for op in effect_classes.OPERATIONS.values())
    ):
        return True
    return acao is not None and (
        acao in effect_classes.OPERATIONS
        or any(op.autonomy_action == acao for op in effect_classes.OPERATIONS.values())
    )


def test_envio_ao_prospect_fora_do_catalogo_de_efeitos() -> None:
    assert not _catalogo_conhece(classe=_CLASSE_ADIADA)
    assert not _catalogo_conhece(acao=ACAO_ENVIO_PROSPECT)
    # Controle: o detector reconhece uma classe e uma ação que o catálogo tem.
    uma_operacao = next(iter(effect_classes.OPERATIONS.values()))
    assert _catalogo_conhece(classe=uma_operacao.action_class)
    assert _catalogo_conhece(acao=uma_operacao.autonomy_action)


# --- (ii) ninguém em src importa o agente comercial --------------------------------------------------


def _pacote_de(path: Path) -> str:
    partes = path.resolve().relative_to(_SRC.parent).with_suffix("").parts
    return ".".join(partes[:-1])


def _fonte_importa_comercial(fonte: str, pacote: str = "") -> bool:
    """Import estático do agente comercial, absoluto OU relativo (resolvido contra `pacote`).

    Limite declarado: import dinâmico (`importlib.import_module`) não é visto por uma cerca de AST.
    """
    for n in ast.walk(ast.parse(fonte)):
        if isinstance(n, ast.ImportFrom):
            modulo = n.module or ""
            if n.level:
                base = pacote.split(".") if pacote else []
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                modulo = ".".join([*base, *([modulo] if modulo else [])])
            if modulo == "maezo.agents.comercial" or modulo.startswith("maezo.agents.comercial."):
                return True
            if modulo == "maezo.agents" and any(a.name == "comercial" for a in n.names):
                return True
        if isinstance(n, ast.Import) and any(
            a.name == "maezo.agents.comercial" or a.name.startswith("maezo.agents.comercial.")
            for a in n.names
        ):
            return True
    return False


def test_nenhum_modulo_de_src_importa_o_agente_comercial() -> None:
    """Nenhum despachante, raiz de composição, runtime ou webhook monta o agente comercial."""
    importadores = {
        p.relative_to(_REPO).as_posix()
        for p in sorted(_SRC.rglob("*.py"))
        if _PACOTE not in p.parents and _fonte_importa_comercial(p.read_text(encoding="utf-8"), _pacote_de(p))
    }
    assert importadores == set()
    # Controle: o detector acha as formas de importar.
    assert _fonte_importa_comercial("from maezo.agents.comercial.graph import build")
    assert _fonte_importa_comercial("from maezo.agents import comercial")
    assert _fonte_importa_comercial("import maezo.agents.comercial.graph")
    assert not _fonte_importa_comercial("from maezo.agents.helena import graph")
    # Relativos, como se escritos em `maezo.agents` ou num agente vizinho.
    assert _fonte_importa_comercial("from . import comercial", "maezo.agents")
    assert _fonte_importa_comercial("from .comercial.graph import build", "maezo.agents")
    assert _fonte_importa_comercial("from ..comercial import graph", "maezo.agents.helena")
    assert not _fonte_importa_comercial("from . import graph", "maezo.agents.helena")


# --- (iii) o grafo não envia; a ação de envio é só dado -------------------------------------------------

_CHAMADA_DE_ENVIO = re.compile(r"(?i)send|enviar|dispatch|despach|call_tool|invoke_tool|publish|post")
_IMPORT_VEDADO = ("maezo.gateway", "maezo.tools", "maezo.platform.webhooks", "httpx", "requests", "aiohttp")


def _envios_no_fonte(fonte: str) -> list[str]:
    arvore = ast.parse(fonte)
    chamados = [
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call)
    ]
    importados = [n.module or "" for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)]
    importados += [a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names]
    return [c for c in chamados if _CHAMADA_DE_ENVIO.search(c)] + [
        m for m in importados if m.startswith(_IMPORT_VEDADO)
    ]


def test_o_grafo_comercial_nao_chama_envio_nem_importa_gateway() -> None:
    for modulo in sorted(_PACOTE.glob("*.py")):
        assert _envios_no_fonte(modulo.read_text(encoding="utf-8")) == [], modulo.name
    # Controle: o detector acha uma chamada de envio e um import de gateway plantados.
    assert _envios_no_fonte("client.send_message(x)")
    assert _envios_no_fonte("from maezo.gateway.communications import whatsapp_templates")
    assert _envios_no_fonte("import httpx")


async def _turno(**entrada: Any) -> dict[str, Any]:
    compilado = build({"comercial_ligado": True}).compile()
    return dict(await compilado.ainvoke(new_comercial_state(entrada)))


async def test_a_acao_de_envio_e_so_dado_tambem_fora_do_primeiro_turno() -> None:
    for entrada in (
        {"modo": "prospect", "passagem": _PASSAGEM},
        {"modo": "prospect", "passagem": _PASSAGEM, "pedido_humano": True},
    ):
        saida = await _turno(**entrada)
        assert saida["rota"] == ROTA_FILA_HUMANA
        # A ação volta como nome (dado) para um despachante que não existe; nada foi enviado.
        assert saida["acao_de_envio"] == ACAO_ENVIO_PROSPECT
        assert isinstance(saida["acao_de_envio"], str)
        assert saida["envio_pelo_humano"] is False
    assert graph.ACAO_ENVIO_PROSPECT == "send_prospect_message"

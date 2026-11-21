"""E-SF-RT-03 (variante completa, parte construível): fila "Relacionamento e renovação" e o pedido
de rascunho do copiloto, num módulo puro, NÃO montado e sem modelo.

Testes do plano: `test_assistente_retencao_sem_ferramenta_decisoria`,
`test_assistente_retencao_sem_zona_phi` e o reuso de `test_contract_termination_never_autonomous`.
Condições da coordenação: nenhum importador do módulo (igualdade com o vazio); o cancelamento a
pedido não o importa; nada de retenção chega ao Lucas nem ao beneficiário; vencimento None como GAP;
nenhuma oferta, desconto, prazo ou promessa; pasta de autonomia e classes de efeito só lidas.
Fixtures sintéticas; nenhum dado real.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import typing
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml

from maezo.gateway.human import retencao_relacionamento as rr
from maezo.gateway.human.retencao_relacionamento import (
    ACAO_RASCUNHO_RETENCAO,
    CAMPOS_DO_RASCUNHO_RETENCAO,
    FILA_RELACIONAMENTO,
    ItemDeRelacionamento,
    ProtocoloRegistrado,
    RetencaoRelacionamentoError,
    abrir_item_de_relacionamento,
    pedir_rascunho,
)
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep

_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO / "src" / "maezo"
_MODULO = Path(rr.__file__).resolve()
_NOME_DO_MODULO = "maezo.gateway.human.retencao_relacionamento"
_AUTONOMIA = _REPO / "spec" / "policies" / "autonomy"

_AUTO = "spec/policies/autonomy/"
#: A pasta de autonomia e as classes de efeito na main `dcfedd29`, que esta linha só LÊ.
SHA256_SO_LIDOS = {
    _AUTO + "L0-core.yaml": "515ab01027f2caefb3414b3bc262340aca918b8feafb41b9761edc81607fb280",
    _AUTO + "_hard_frozen.yaml": "1c6ce210730d475d3782ff9d1853ef03db5373894e8a5fd14b7730a216098dc6",
    _AUTO + "action-approvals.yaml": "fcaa430c7921700cc088e3bb618237cbbd1c1f0bcafa7e5131e6c33ab31c217e",
    _AUTO + "tenants-amh.yaml": "d78168bddb6362c0556028e0cb29cd69c43743963ccab4c16698f15ded72baa0",
    "src/maezo/gateway/effect_classes.py": "ac307b0c781020fbd65c8c216f7a43047efd1c94b9643efcc12c1a9da92d4f21",
}

#: Fatos sintéticos que NÃO podem chegar a um rascunho: clínico, CPT, inadimplência, texto livre.
_FATOS_VEDADOS: dict[str, Any] = {
    "cid": "E11",
    "diagnostico": "diabetes tipo 2",
    "cpt": "cobertura parcial temporaria ate 2027",
    "inadimplencia": "3 parcelas em atraso",
    "texto_livre_do_titular": "quero cancelar porque estou doente",
    "valor_mensalidade": "R$ 899,00",
}


class _FonteSintetica:
    def __init__(self, registro: object) -> None:
        self.registro = registro
        self.chamadas: list[dict[str, str]] = []

    async def registrar(self, *, tenant_ref: str, titular_ref: str, fila_ref: str) -> Any:
        self.chamadas.append({"tenant_ref": tenant_ref, "titular_ref": titular_ref, "fila_ref": fila_ref})
        return self.registro


def _registro() -> ProtocoloRegistrado:
    return ProtocoloRegistrado(
        protocolo_ref="PROT-SINTETICO-0001", registrado_em=datetime(2026, 11, 21, tzinfo=UTC)
    )


async def _item() -> ItemDeRelacionamento:
    return await abrir_item_de_relacionamento(
        _FonteSintetica(_registro()), tenant_ref="tenant-sintetico", titular_ref="titular-sintetico"
    )


def _arvore() -> ast.Module:
    return ast.parse(_MODULO.read_text(encoding="utf-8"))


def _docstrings(arvore: ast.AST) -> set[int]:
    return {
        id(n.body[0].value)
        for n in ast.walk(arvore)
        if isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }


def _constantes_de_texto() -> list[str]:
    arvore = _arvore()
    docs = _docstrings(arvore)
    return [
        n.value
        for n in ast.walk(arvore)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]


def _pacote_de(path: Path) -> str:
    """Pacote Python do arquivo, para resolver imports relativos (`maezo.gateway.human`, ...)."""
    partes = path.resolve().relative_to(_SRC.parent).with_suffix("").parts
    return ".".join(partes[:-1]) if partes[-1] != "__init__" else ".".join(partes[:-1])


def _importa_o_modulo(path: Path) -> bool:
    return _fonte_importa_o_modulo(path.read_text(encoding="utf-8"), _pacote_de(path))


def _fonte_importa_o_modulo(fonte: str, pacote: str = "") -> bool:
    """Import estático do módulo, absoluto OU relativo (resolvido contra `pacote`).

    Limite declarado: import dinâmico (`importlib.import_module`) não é visto por uma cerca de AST.
    """
    pai, _, nome = _NOME_DO_MODULO.rpartition(".")
    for n in ast.walk(ast.parse(fonte)):
        if isinstance(n, ast.ImportFrom):
            modulo = n.module or ""
            if n.level:
                base = pacote.split(".") if pacote else []
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                modulo = ".".join([*base, *([modulo] if modulo else [])])
            if modulo == _NOME_DO_MODULO or (modulo == pai and any(a.name == nome for a in n.names)):
                return True
        if isinstance(n, ast.Import) and any(a.name == _NOME_DO_MODULO for a in n.names):
            return True
    return False


# --- Testes do plano --------------------------------------------------------------------------------


def test_assistente_retencao_sem_ferramenta_decisoria() -> None:
    """O módulo não chama tool, envio, processo nem o PEP; a ação é L1, sem parâmetros, não `hard`."""
    chamados = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(_arvore())
        if isinstance(n, ast.Call)
    }
    proibidos = {"send", "send_message", "dispatch", "start_process", "evaluate", "call_tool", "invoke"}
    assert not chamados & proibidos, chamados & proibidos
    # Controle: a mesma extração acha uma chamada de envio plantada.
    plantado = ast.parse("cliente.send_message(x)\npep.evaluate(a)")
    achados = {
        n.func.attr
        for n in ast.walk(plantado)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert achados & proibidos == {"send_message", "evaluate"}
    # Nenhum nome de ação decisória (valor, elegibilidade, rescisão, contestação, negativa) no módulo.
    texto = " ".join(_constantes_de_texto())
    assert not [h for h in HARD_ACTIONS if h in texto]
    assert ACAO_RASCUNHO_RETENCAO not in HARD_ACTIONS
    l0 = yaml.safe_load((_AUTONOMIA / "L0-core.yaml").read_text(encoding="utf-8"))["actions"]
    assert l0[ACAO_RASCUNHO_RETENCAO] == {"level": "L1"}
    decisao = build_pep().evaluate(
        ACAO_RASCUNHO_RETENCAO, agent_context={"agent_id": "retencao", "level": "L3"}
    )
    assert decisao is Decision.REQUIRE_HUMAN


async def test_assistente_retencao_sem_zona_phi() -> None:
    """Lista fechada VAZIA: nenhum fato (clínico, CPT, inadimplência, texto livre) chega ao rascunho."""
    assert frozenset() == CAMPOS_DO_RASCUNHO_RETENCAO
    pedido = pedir_rascunho(await _item(), dict(_FATOS_VEDADOS))
    # Lista vazia: nenhum fato passa, e o recorte é imutável.
    assert dict(pedido.fatos) == {}
    assert not [c for c in _FATOS_VEDADOS if c in pedido.fatos]
    assert isinstance(pedido.fatos, MappingProxyType)  # imutável: ninguém acrescenta fato depois
    # Nenhum import de módulo PHI/FHIR/clínico.
    importados = {n.module or "" for n in ast.walk(_arvore()) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(_arvore()) if isinstance(n, ast.Import) for a in n.names
    }
    assert not [m for m in importados if any(t in m for t in ("phi", "fhir", "clinic", "member_facts"))]


def test_contract_termination_never_autonomous() -> None:
    """Reuso (RT-03): rescisão nunca é ato autônomo, qualquer que seja o nível do agente."""
    pep = build_pep()
    for nivel in ("L0", "L1", "L2", "L3"):
        assert pep.evaluate("contract_termination", agent_context={"level": nivel}) is Decision.DENY
    assert "contract_termination" in HARD_ACTIONS


# --- Fila, protocolo e prazo ----------------------------------------------------------------------


async def test_o_protocolo_vem_da_custodia_e_o_vencimento_e_gap() -> None:
    fonte = _FonteSintetica(_registro())
    item = await abrir_item_de_relacionamento(
        fonte, tenant_ref="tenant-sintetico", titular_ref="titular-sintetico"
    )
    assert item.protocolo_ref == "PROT-SINTETICO-0001"
    assert item.fila_ref == FILA_RELACIONAMENTO
    assert fonte.chamadas == [
        {
            "tenant_ref": "tenant-sintetico",
            "titular_ref": "titular-sintetico",
            "fila_ref": FILA_RELACIONAMENTO,
        }
    ]
    # Vencimento None como tipo: nenhum prazo calculado, nenhum número de dias no módulo.
    assert item.vencimento is None
    assert typing.get_type_hints(ItemDeRelacionamento)["vencimento"] is type(None)
    numeros = [n.value for n in ast.walk(_arvore()) if isinstance(n, ast.Constant) and type(n.value) is int]
    assert 7 not in numeros
    assert not [t for t in _constantes_de_texto() if "dias" in t.lower()]


@pytest.mark.parametrize("registro", [None, "PROT-1", object()])
async def test_sem_protocolo_da_custodia_nada_e_aberto(registro: object) -> None:
    with pytest.raises(RetencaoRelacionamentoError) as recusa:
        await abrir_item_de_relacionamento(
            _FonteSintetica(registro), tenant_ref="tenant-sintetico", titular_ref="titular-sintetico"
        )
    assert recusa.value.code == "ERR_PROTOCOLO_AUSENTE"


def test_o_recorte_e_local_e_nao_passa_pelo_renderizador_de_prompt() -> None:
    """Sem agente nem modelo, o recorte é local; o renderizador de prompt (cerca DL-0191, sítios de
    agente) não é importado nem chamado aqui."""
    nomes = {n.id for n in ast.walk(_arvore()) if isinstance(n, ast.Name)}
    nomes |= {a.name for n in ast.walk(_arvore()) if isinstance(n, ast.ImportFrom) for a in n.names}
    modulos = {n.module or "" for n in ast.walk(_arvore()) if isinstance(n, ast.ImportFrom)}
    assert "render_fatos_para_prompt" not in nomes
    assert not [m for m in modulos if m.startswith("maezo.runtime")]


async def test_o_rascunho_e_so_dado_sem_texto_gerado() -> None:
    pedido = pedir_rascunho(await _item(), {})
    assert pedido.acao == ACAO_RASCUNHO_RETENCAO
    assert pedido.texto == ""
    assert {f.name for f in dataclasses.fields(pedido)} == {"protocolo_ref", "acao", "fatos", "texto"}


# --- Retenção sem oferta, sem fricção no cancelamento, nada ao Lucas ou ao beneficiário ----------------


def test_nenhuma_oferta_desconto_prazo_ou_promessa_no_modulo() -> None:
    vedados = (
        "desconto",
        "oferta",
        "promo",
        "brinde",
        "isen",
        "gratis",
        "grátis",
        "condição",
        "garant",
        "r$",
        "%",
    )
    for texto in _constantes_de_texto():
        baixo = texto.lower()
        assert not [v for v in vedados if v in baixo], texto
    # Nenhum texto ao cliente: o módulo não tem constante de mensagem ao beneficiário.
    assert not [nome for nome in vars(rr) if nome.startswith("TEXTO")]


def test_nenhum_importador_do_modulo_em_src() -> None:
    """Não montado: nenhum despachante, worker, rota, composição nem cancelamento importa o módulo."""
    importadores = {
        p.relative_to(_REPO).as_posix()
        for p in sorted(_SRC.rglob("*.py"))
        if p.resolve() != _MODULO and _importa_o_modulo(p)
    }
    assert importadores == set()
    # Controle: o detector acha um importador plantado.
    assert _fonte_importa_o_modulo(f"from {_NOME_DO_MODULO} import pedir_rascunho")
    assert _fonte_importa_o_modulo("from maezo.gateway.human import retencao_relacionamento")
    assert _fonte_importa_o_modulo(f"import {_NOME_DO_MODULO}")
    # Relativos, como se escritos num vizinho em `gateway/human` ou num filho de `gateway`.
    assert _fonte_importa_o_modulo(
        "from .retencao_relacionamento import pedir_rascunho", "maezo.gateway.human"
    )
    assert _fonte_importa_o_modulo("from . import retencao_relacionamento", "maezo.gateway.human")
    assert _fonte_importa_o_modulo(
        "from ..human.retencao_relacionamento import x", "maezo.gateway.staff_cases"
    )
    assert _fonte_importa_o_modulo("from .human import retencao_relacionamento", "maezo.gateway")
    assert not _fonte_importa_o_modulo("from .reajuste import x", "maezo.gateway.human")
    assert not _fonte_importa_o_modulo("from . import retencao_relacionamento", "maezo.platform")


def test_o_cancelamento_a_pedido_nao_importa_o_modulo() -> None:
    saida = _SRC / "platform" / "saida_a_pedido"
    arquivos = sorted(saida.rglob("*.py"))
    assert arquivos  # controle: a varredura vê o cancelamento a pedido
    assert not [p.name for p in arquivos if _importa_o_modulo(p)]
    # E a pergunta de motivo segue opcional e depois do protocolo (#1348): o cancelamento não muda.
    from maezo.platform.saida_a_pedido import cancelamento

    assert "pergunta_motivo" in Path(cancelamento.__file__).read_text(encoding="utf-8")


def test_nada_de_retencao_chega_ao_lucas() -> None:
    lucas = _SRC / "agents" / "lucas"
    arquivos = sorted(lucas.rglob("*.py"))
    assert arquivos
    for p in arquivos:
        fonte = p.read_text(encoding="utf-8")
        assert not _importa_o_modulo(p), p
        assert ACAO_RASCUNHO_RETENCAO not in fonte, p
        assert FILA_RELACIONAMENTO not in fonte, p
    spec = yaml.safe_load((_REPO / "spec" / "agents" / "lucas" / "agent.yaml").read_text(encoding="utf-8"))
    assert ACAO_RASCUNHO_RETENCAO not in spec.get("autonomy_actions", [])


def test_pasta_de_autonomia_e_classes_de_efeito_so_lidas() -> None:
    pasta = sorted(p.relative_to(_REPO).as_posix() for p in _AUTONOMIA.iterdir() if p.is_file())
    assert pasta == sorted(k for k in SHA256_SO_LIDOS if k.startswith(_AUTO))
    for rel, esperado in SHA256_SO_LIDOS.items():
        assert hashlib.sha256((_REPO / rel).read_bytes()).hexdigest() == esperado, rel

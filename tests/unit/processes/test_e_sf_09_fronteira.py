"""Fronteira de E-SF-09: sem mensagem/evento, sem prazo em Python, sem saude/dinheiro, portoes fechados."""

import ast
import dataclasses
import importlib
import pkgutil
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

import maezo.platform.saida_a_pedido as pacote
from maezo.platform.saida_a_pedido import arrependimento

RAIZ = Path(__file__).resolve().parents[3]
FONTES = sorted(Path(str(pacote.__file__)).parent.glob("*.py"))
POLITICAS = RAIZ / "spec" / "policies" / "saida-a-pedido"

IMPORTS_PROIBIDOS = (
    "maezo.gateway.communications",
    "maezo.tools.mcp_whatsapp",
    "maezo.platform.lembretes",
    "maezo.tools.workers.events",
    "kafka",
    "maezo.platform.integrations",
)
INTEIROS_DE_PRAZO = {3, 5, 7, 10, 25, 30}
SAUDE = re.compile(r"(?i)saude|cid|procedimento|uso_do_plano|diagnost|clinic|declaracao_de_saude|ds_")
DINHEIRO = re.compile(r"(?i)valor|amount|preco|cents")


def _arvores() -> Iterator[tuple[Path, ast.Module]]:
    for fonte in FONTES:
        yield fonte, ast.parse(fonte.read_text(encoding="utf-8"))


def _modulos() -> list[ModuleType]:
    return [
        importlib.import_module(f"{pacote.__name__}.{info.name}")
        for info in pkgutil.iter_modules(pacote.__path__)
    ]


def _dataclasses() -> list[type]:
    achadas: list[type] = []
    for modulo in _modulos():
        for objeto in vars(modulo).values():
            if (
                isinstance(objeto, type)
                and dataclasses.is_dataclass(objeto)
                and objeto.__module__ == modulo.__name__
            ):
                achadas.append(objeto)
    return achadas


def test_pacote_nao_esta_vazio() -> None:
    assert len(FONTES) == 6
    assert len(_dataclasses()) >= 10


def test_saida_a_pedido_nao_envia_mensagem_nem_emite_evento() -> None:
    for fonte, arvore in _arvores():
        for no in ast.walk(arvore):
            nomes: list[str] = []
            if isinstance(no, ast.Import):
                nomes = [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom) and no.module:
                nomes = [no.module, *(f"{no.module}.{a.name}" for a in no.names)]
            for nome in nomes:
                for proibido in IMPORTS_PROIBIDOS:
                    assert nome != proibido
                    assert not nome.startswith(proibido + "."), f"{fonte.name}: {nome}"
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute):
                assert no.func.attr != "publish", fonte.name
                assert not no.func.attr.startswith("send_"), fonte.name
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
                assert not no.func.id.startswith("send_"), fonte.name


def _dentro_de_post_init(arvore: ast.Module) -> set[int]:
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.FunctionDef) and no.name == "__post_init__":
            ids.update(id(filho) for filho in ast.walk(no))
    return ids


def test_prazo_nunca_literal_em_python() -> None:
    for fonte, arvore in _arvores():
        isentos = _dentro_de_post_init(arvore)
        for no in ast.walk(arvore):
            if isinstance(no, ast.Call):
                alvo = no.func.attr if isinstance(no.func, ast.Attribute) else getattr(no.func, "id", "")
                if alvo == "timedelta":
                    for argumento in (*no.args, *(k.value for k in no.keywords)):
                        assert not (
                            isinstance(argumento, ast.Constant) and isinstance(argumento.value, int | float)
                        ), f"{fonte.name}:{no.lineno} timedelta com literal"
            if (
                isinstance(no, ast.Constant)
                and isinstance(no.value, int)
                and not isinstance(no.value, bool)
                and id(no) not in isentos
            ):
                assert no.value not in INTEIROS_DE_PRAZO, f"{fonte.name}:{no.lineno} literal {no.value}"


def test_sem_insumo_de_saude() -> None:
    nomes = [f.name for classe in _dataclasses() for f in dataclasses.fields(classe)]
    nomes += list(arrependimento.CHAVES_DO_PAYLOAD) + list(arrependimento.CHAVES_DA_DECLARACAO)
    assert nomes
    for nome in nomes:
        assert not SAUDE.search(nome), nome


def test_sem_dinheiro_nem_float() -> None:
    for classe in _dataclasses():
        for campo in dataclasses.fields(classe):
            assert not DINHEIRO.search(campo.name), f"{classe.__name__}.{campo.name}"
            assert "float" not in str(campo.type), f"{classe.__name__}.{campo.name}"


def _yaml(nome: str) -> dict[str, Any]:
    dados = yaml.safe_load((POLITICAS / nome).read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def _pendente(bloco: dict[str, Any]) -> None:
    assert bloco["aprovado"] is False
    assert bloco["aprovador"] == bloco["data"] == bloco["evidencia_ref"] == "PENDENTE"


def test_portoes_fechados_no_estado_entregue() -> None:
    janela = _yaml("janela-arrependimento.yaml")
    assert janela["status"] == "NAO_RATIFICADO"
    assert janela["variante_ativa"] == "UNRATIFIED_UNION"
    assert janela["display_numeric_promise"] is False
    assert janela["fora_da_janela"] == "route_to_human"
    assert janela["declaracao_art8_par1"]["texto_ratificado"] is False
    _pendente(janela["ratificacao"]["juridico"])
    cancelamento = _yaml("pedido-cancelamento.yaml")
    assert cancelamento["bloco_art15"]["texto_ratificado"] is False
    assert cancelamento["prazo_comprovante_efetivo"]["exibir"] is False
    assert cancelamento["linha_alternativa_os08"]["exibir"] is False
    assert cancelamento["linha_alternativa_os08"]["redacao_ratificada"] is False
    _pendente(cancelamento["ratificacao"]["reg27_juridico"])
    _pendente(cancelamento["ratificacao"]["rtj3a_juridico"])


@pytest.mark.parametrize("fonte", FONTES, ids=lambda p: p.name)
def test_sem_import_de_agente(fonte: Path) -> None:
    arvore = ast.parse(fonte.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module == "maezo.agents":
            assert {a.name for a in no.names} <= {"resolve_spec_dir"}, fonte.name
        elif isinstance(no, ast.ImportFrom) and no.module:
            assert not no.module.startswith("maezo.agents."), f"{fonte.name}: {no.module}"
        elif isinstance(no, ast.Import):
            for alias in no.names:
                assert not alias.name.startswith("maezo.agents."), f"{fonte.name}: {alias.name}"

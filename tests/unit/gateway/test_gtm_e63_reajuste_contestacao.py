"""GTM-E-63: contestação de reajuste não assistencial, protocolo primeiro, caso humano."""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maezo.gateway.capabilities.journeys.topology import STAGES_BY_TASK
from maezo.gateway.human import reajuste_contestacao as mod
from maezo.gateway.human.reajuste_contestacao import (
    ContestacaoAberta,
    ContestacaoReajusteError,
    ProtocoloRegistrado,
    abrir_contestacao_reajuste,
    registrar_decisao,
)

AGORA = datetime(2026, 1, 1, tzinfo=UTC)
KW = {"tenant_ref": "t1", "titular_ref": "h1", "fila_ref": "q1"}


class FonteFalsa:
    def __init__(self, resultado: object = None, *, levanta: bool = False, ordem: list[str] | None = None):
        self.resultado = resultado
        self.levanta = levanta
        self.chamadas = 0
        self.ordem = ordem if ordem is not None else []

    async def registrar(
        self, *, tenant_ref: str, titular_ref: str, fila_ref: str
    ) -> ProtocoloRegistrado | None:
        self.chamadas += 1
        self.ordem.append("fonte")
        if self.levanta:
            raise RuntimeError("SEGREDO-123 titular")
        return self.resultado  # type: ignore[return-value]


def run(fonte: FonteFalsa, **over: str) -> ContestacaoAberta:
    return asyncio.run(abrir_contestacao_reajuste(fonte, **{**KW, **over}))


def code_of(fonte: FonteFalsa, **over: str) -> str:
    with pytest.raises(ContestacaoReajusteError) as ei:
        run(fonte, **over)
    return ei.value.code


def _ok(proto: str = "P2026000042", em: datetime = AGORA) -> ProtocoloRegistrado:
    return ProtocoloRegistrado(proto, em)


def test_contestacao_reajuste_protocolo_primeiro() -> None:
    assert code_of(FonteFalsa(None)) == "ERR_PROTOCOLO_AUSENTE"
    f = FonteFalsa(levanta=True)
    with pytest.raises(ContestacaoReajusteError) as ei:
        run(f)
    assert ei.value.code == "ERR_PROTOCOLO_AUSENTE"
    assert "SEGREDO" not in str(ei.value) and ei.value.__cause__ is None
    assert code_of(FonteFalsa("P1")) == "ERR_PROTOCOLO_AUSENTE"
    for proto in ("", "com espaco", "x" * 65):
        with pytest.raises(ContestacaoReajusteError):
            ProtocoloRegistrado(proto, AGORA)
    with pytest.raises(ContestacaoReajusteError):
        ProtocoloRegistrado("P1", datetime(2026, 1, 1))
    # protocolo invalido que escapa ao construtor (objeto adulterado) tambem recusa
    adulterado = _ok()
    object.__setattr__(adulterado, "protocolo_ref", "com espaco")
    assert code_of(FonteFalsa(adulterado)) == "ERR_PROTOCOLO_AUSENTE"

    ordem: list[str] = []
    f2 = FonteFalsa(_ok(), ordem=ordem)
    caso = run(f2)
    ordem.append("texto")
    assert f2.chamadas == 1 and ordem == ["fonte", "texto"]
    assert "P2026000042" in caso.texto_ao_cliente
    assert caso.protocolo_ref == "P2026000042"


def test_contestacao_reajuste_agente_nao_decide() -> None:
    caso = run(FonteFalsa(_ok()))
    for tipo in ("agente", "sistema", "dmn", "", None, 1):
        with pytest.raises(ContestacaoReajusteError) as ei:
            registrar_decisao(caso, decisor_tipo=tipo, decisor_ref="u1", decisao_ref="d1")  # type: ignore[arg-type]
        assert ei.value.code == "ERR_DECISAO_CONTESTACAO_HUMANA"
    d = registrar_decisao(caso, decisor_tipo="humano", decisor_ref="u1", decisao_ref="d1")
    assert d.protocolo_ref == caso.protocolo_ref and d.decisor_ref == "u1"


def test_texto_sem_prazo_sem_em_analise_sem_norma() -> None:
    proto = "P2026000042"
    texto = run(FonteFalsa(_ok(proto))).texto_ao_cliente.replace(proto, "")
    assert not re.search(r"\d", texto)
    baixo = texto.lower()
    for proibido in ("dias", "prazo", "em análise", "em analise"):
        assert proibido not in baixo
    assert not re.search(r"\b(rn|art)", baixo)


def test_nunca_pela_escalacao_clinica() -> None:
    assert mod.PROCESS_KEYS == ()
    assert "ESCALATION" not in Path(mod.__file__).read_text(encoding="utf-8")
    src = Path(mod.__file__).resolve().parents[2]
    proprio = Path(mod.__file__).resolve()
    for p in src.rglob("*.py"):
        if p.resolve() != proprio:
            assert "reajuste_contestacao" not in p.read_text(encoding="utf-8"), p


def test_nunca_recusada_por_janela() -> None:
    nomes = set(inspect.signature(abrir_contestacao_reajuste).parameters)
    assert nomes == {"fonte", "tenant_ref", "titular_ref", "fila_ref"}
    for n in nomes:
        assert not re.search(r"data|janela|prazo|dias|timer", n)
    for em in (datetime(1990, 1, 1, tzinfo=UTC), datetime(2999, 1, 1, tzinfo=UTC)):
        assert run(FonteFalsa(_ok(em=em))).registrado_em == em


def test_prazo_interno_nao_calculado() -> None:
    assert run(FonteFalsa(_ok())).prazo_interno is None


def test_suporte_abre_caso_antes_de_avisar() -> None:
    est = STAGES_BY_TASK["journey.suporte.step"]
    ini = est["S1_case"]
    assert "case.open_or_update" in ini.operations
    assert "notice.prepare_or_send" not in ini.operations
    vistos: set[str] = set()
    pilha = list(ini.successor_cursor_refs)
    while pilha:
        c = pilha.pop()
        if c in vistos or c not in est:
            continue
        vistos.add(c)
        pilha.extend(est[c].successor_cursor_refs)
    com_aviso = {c for c, s in est.items() if "notice.prepare_or_send" in s.operations}
    assert com_aviso and com_aviso <= vistos | {"S1_case"} and "S1_case" not in com_aviso


def test_entradas_invalidas() -> None:
    for campo in KW:
        for ruim in ("", "com espaco", "x" * 257, "ação", None, 5):
            f = FonteFalsa(_ok())
            assert code_of(f, **{campo: ruim}) == "ERR_CONTESTACAO_ENTRADA_INVALIDA"  # type: ignore[arg-type]
            assert f.chamadas == 0

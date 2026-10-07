"""Testes da telemetria presence-only da atividade vendor (VW5+, WAVES §2.9-i + §5).

A asserção de PHI aqui é ESTRUTURAL, não disciplinar: nenhum payload/conteúdo chega ao contador
porque NENHUMA superfície pública o aceita — e o teste tenta todas as rotas de contrabando antes
de afirmar isso. O KPI moldável do pacote é `phi_egress_violations == 0`, provado DEPOIS das
tentativas, e a varredura é provada VIVA (estado corrompido à mão ⇒ KPI > 0), para que
`== 0` nunca vire constante complacente.

Titular = vendedor como CATEGORIA (audiência `vendor`), nunca indivíduo: nenhuma superfície
aceita identificador de pessoa (G-PHI, VW0-D23). Nada clínico, nada nível-titular, nenhum
timer de SLA (RATIFY-LATER).
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from maezo.platform.telemetry.vendor_activity import (
    PHI_EGRESS_VIOLATIONS_KPI,
    SNAPSHOT_SCHEMA,
    ContentSmugglingError,
    VendorActivityCounters,
    VendorActivitySignal,
    VendorActivitySnapshot,
)

# ---------------------------------------------------------------------------
# 1. Contadores agregam; presença é derivada; o catálogo é fechado e completo
# ---------------------------------------------------------------------------


def test_contadores_agregam_e_presenca_e_derivada() -> None:
    contadores = VendorActivityCounters()
    vazio = contadores.snapshot()
    # catálogo completo com zeros explícitos: o dashboard lê sempre a mesma forma
    assert vazio.counts == {sinal.value: 0 for sinal in VendorActivitySignal}
    assert all(presence is False for presence in vazio.presence.values())
    assert vazio.schema_ == SNAPSHOT_SCHEMA

    contadores.record(VendorActivitySignal.SUBMISSION_RECEIVED)
    contadores.record(VendorActivitySignal.SUBMISSION_RECEIVED)
    contadores.record(VendorActivitySignal.SUBMISSION_REFUSED)
    contadores.record(VendorActivitySignal.SESSION_OPENED)

    agregado = contadores.snapshot()
    assert agregado.counts["submission_received"] == 2
    assert agregado.counts["submission_refused"] == 1
    assert agregado.counts["session_opened"] == 1
    assert agregado.counts["publication_attempted"] == 0
    assert agregado.presence["submission_received"] is True
    assert agregado.presence["publication_attempted"] is False
    # presença é BOOL, não medida (WAVES §2.9-i: sinal bool, não valor)
    assert all(type(valor) is bool for valor in agregado.presence.values())
    assert all(type(valor) is int for valor in agregado.counts.values())


def test_reset_zera_a_janela_sem_historico() -> None:
    contadores = VendorActivityCounters()
    contadores.record(VendorActivitySignal.PUBLICATION_DELIVERED)
    contadores.reset()
    assert contadores.snapshot().counts["publication_delivered"] == 0
    assert contadores.snapshot().presence["publication_delivered"] is False


def test_snapshot_e_json_deterministico_e_fechado() -> None:
    contadores = VendorActivityCounters()
    contadores.record(VendorActivitySignal.SUBMISSION_RECEIVED)
    primeiro = contadores.snapshot()
    with pytest.raises(ValidationError):
        VendorActivitySnapshot(
            schema_=SNAPSHOT_SCHEMA,
            counts={"session_opened": "conteúdo"},  # não-int: a forma fechada recusa
            presence={"session_opened": False},
            phi_egress_violations=0,
        )
    with pytest.raises(ValidationError):
        VendorActivitySnapshot(
            schema_=SNAPSHOT_SCHEMA,
            counts={"session_opened": 1},
            presence={"session_opened": "não-bool"},  # presença é BOOL exato, nunca medida
            phi_egress_violations=0,
        )
    # chave FORA do catálogo fechado não é recusa do pydantic (Mapping aceita chave qualquer) —
    # é recusa da CAMADA de contadores: não entra no merge e é acusada no KPI (teste abaixo).
    outro = VendorActivityCounters()
    outro.record(VendorActivitySignal.SUBMISSION_RECEIVED)
    assert primeiro.as_json() == outro.snapshot().as_json()  # determinístico
    payload = json.loads(primeiro.as_json())
    assert payload[PHI_EGRESS_VIOLATIONS_KPI] == 0
    assert list(payload["counts"]) == sorted(payload["counts"])


# ---------------------------------------------------------------------------
# 2. Contrabando de conteúdo: TODAS as rotas recusadas, contador inalterado
# ---------------------------------------------------------------------------


def _tentativas_de_contrabando(contadores: VendorActivityCounters) -> list[str]:
    recusas: list[str] = []
    # (1) string solta no lugar do membro do catálogo — o canal clássico de conteúdo disfarçado
    try:
        contadores.record("submission_received")  # type: ignore[arg-type]
    except ContentSmugglingError:
        recusas.append("str_solta")
    # (2) membro de OUTRO enum/string com o mesmo valor de outro domínio
    try:
        contadores.record(VendorActivitySignal.SESSION_OPENED.value)  # type: ignore[arg-type]
    except ContentSmugglingError:
        recusas.append("value_str")
    # (3) payload como kwarg — a assinatura não tem `**kwargs`
    try:
        contadores.record(VendorActivitySignal.SUBMISSION_RECEIVED, payload={"conteudo": "clinico"})  # type: ignore[call-arg]
    except TypeError:
        recusas.append("kwarg_payload")
    # (4) argumento posicional extra — a assinatura é single-arg positional-only
    try:
        contadores.record(VendorActivitySignal.SUBMISSION_RECEIVED, {"conteudo": "clinico"})  # type: ignore[call-overload]
    except TypeError:
        recusas.append("posicional_extra")
    # (5) chamada vazia
    try:
        contadores.record()  # type: ignore[call-arg]
    except TypeError:
        recusas.append("vazia")
    return recusas


def test_contrabando_e_recusado_e_contador_nao_muda() -> None:
    contadores = VendorActivityCounters()
    recusas = _tentativas_de_contrabando(contadores)
    assert sorted(recusas) == ["kwarg_payload", "posicional_extra", "str_solta", "value_str", "vazia"]
    # nada chegou ao contador: a janela continua vazia e o KPI moldável segue em 0 (WAVES §5)
    snapshot = contadores.snapshot()
    assert all(valor == 0 for valor in snapshot.counts.values())
    assert snapshot.phi_egress_violations == 0


def test_kpi_phi_egress_violations_e_zero_ate_depois_do_contrabando() -> None:
    """O KPI do pacote: `phi_egress_violations == 0` — verificável, não declarado."""
    contadores = VendorActivityCounters()
    contadores.record(VendorActivitySignal.PUBLICATION_ATTEMPTED)
    _tentativas_de_contrabando(contadores)
    assert contadores.snapshot().phi_egress_violations == 0


def test_a_varredura_do_kpi_esta_viva_nao_e_constante() -> None:
    """Estado interno corrompido à mão ⇒ KPI acusa. Se `== 0` virar constante, este teste quebra."""
    contadores = VendorActivityCounters()
    contadores._counts["payload_content"] = "conteúdo clínico"  # type: ignore[assignment]
    contadores._counts[VendorActivitySignal.SESSION_OPENED.value] = "não-int"  # type: ignore[assignment]
    assert contadores.snapshot().phi_egress_violations == 2


# ---------------------------------------------------------------------------
# 3. A forma das assinaturas: não existe onde um payload entraria
# ---------------------------------------------------------------------------


def test_record_tem_exatamente_um_parametro_posicional_e_variatico_nenhum() -> None:
    assinatura = inspect.signature(VendorActivityCounters.record)
    parametros = list(assinatura.parameters.values())
    assert [p.name for p in parametros if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)] == [
        "self",
        "signal",
    ]
    assert all(p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parametros), (
        "superfície variádica é rota de contrabando"
    )
    assert parametros[-1].kind is inspect.Parameter.POSITIONAL_ONLY, "sinal deve ser positional-only"


def test_superficie_publica_dos_contadores_e_somente_leitura_e_um_record() -> None:
    metodos = {
        nome
        for nome, membro in inspect.getmembers(VendorActivityCounters, predicate=inspect.isfunction)
        if not nome.startswith("_")
    }
    assert metodos == {"record", "snapshot", "reset"}


# ---------------------------------------------------------------------------
# 4. Cerca de import: módulo folha — tipos que carregam conteúdo não são nem nomeáveis
# ---------------------------------------------------------------------------


def test_importar_a_telemetria_nao_arrasta_gateway_portal_runtime_nem_tools() -> None:
    """Espelho da cerca da observabilidade: em interpretador NOVO, importar o módulo não pode
    puxar `maezo.gateway`/`maezo.portal`/`maezo.runtime`/`maezo.tools` para `sys.modules` —
    sem esses pacotes, nenhum tipo portador de conteúdo é sequer referenciável daqui."""
    sonda = (
        "import sys; import maezo.platform.telemetry.vendor_activity as t; "
        "proibidos = sorted(m for m in sys.modules if m.startswith(('maezo.gateway', 'maezo.portal', "
        "'maezo.runtime', 'maezo.tools'))); print('PROIBIDOS:' + ','.join(proibidos)); "
        "print('SINAIS:' + str(len(t.VendorActivitySignal)))"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", sonda], capture_output=True, text=True, timeout=120, check=True
    )
    linha_proibidos, linha_sinais = (
        linha for linha in resultado.stdout.splitlines() if linha.startswith(("PROIBIDOS:", "SINAIS:"))
    )
    assert linha_proibidos == "PROIBIDOS:", "pacote portador de conteúdo puxado pelo módulo folha"
    assert linha_sinais == f"SINAIS:{len(VendorActivitySignal)}"


def test_o_arquivo_nao_importa_nada_de_maezo(tmp_path: Path) -> None:
    """A cerca no TEXTO, além do interpretador: nenhum import de `maezo.*` no módulo folha."""
    fonte = Path(
        __import__("maezo.platform.telemetry.vendor_activity", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    linhas = [linha.strip() for linha in fonte.splitlines()]
    imports = [
        linha
        for linha in linhas
        if (linha.startswith(("import ", "from ")) and "maezo" in linha and not linha.startswith("#"))
    ]
    assert imports == [], f"módulo folha não importa nada de maezo.*: {imports}"

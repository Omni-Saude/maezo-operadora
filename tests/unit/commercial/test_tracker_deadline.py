"""E-SF-08 / TB-E-13-b + EP-REG-23b — prazo do tracker de contratacao: so' com evento de inicio, nunca nos
estagios 4-7, suspensao registrada, nenhuma ETA sem ratificacao, nenhum rotulo generico e nenhuma duracao
DRAFT no que chega ao comprador.

A projecao em `journey.get` NAO e' construida nesta onda (campo novo no sucessor do C6-4, casa AMH): estas
cercas pinam a fonte (o catalogo) e o estado fechado do wire.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from tests.unit.dmn._catalogo_prazos import (
    SEM_ETA_ANCORA_AUSENTE,
    SEM_ETA_NAO_RATIFICADO,
    LinhaCatalogo,
    auditar_catalogo,
    auditar_textos_ao_comprador,
    duracoes_draft_internas,
    linha_para,
    linhas,
    motivo_eta,
    textos_do_assistente_de_reembolso,
    textos_do_catalogo,
)

_C6_4 = Path(__file__).resolve().parents[2] / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
#: Nomes que um campo de prazo/ETA teria na projecao do tracker (REG-23b, TB-E-13-b).
_CAMPOS_DE_PRAZO = frozenset(
    {
        "deadline",
        "deadline_at",
        "deadlineAt",
        "eta",
        "device_ref",
        "start_event_ref",
        "suspensions",
        "prazo",
        "prazo_maximo",
    }
)


def _par2() -> LinhaCatalogo:
    return linha_para("RN413-art5-par2")


def test_deadline_requires_start_event() -> None:
    par2 = _par2()
    assert par2.evento_ancora_ref == ""  # NORM-GAP-8 aberto
    assert auditar_catalogo([par2]) == []
    ratificada_sem_inicio = dataclasses.replace(par2, status="RATIFIED", regulatory_approval_ref="REF-J")
    assert auditar_catalogo([ratificada_sem_inicio]) == [
        ("r_rn413_art5_par2", "DEADLINE_WITHOUT_START_EVENT")
    ]


def test_no_deadline_for_stages_4_to_7() -> None:
    estagios = {e for x in linhas() for e in x.estagios_tracker.split(",") if e}
    assert estagios <= {"1", "2", "3"}
    assert linha_para("estagio-5-analise").regra == "r_catchall"
    injetada = dataclasses.replace(_par2(), estagios_tracker="2,5")
    assert auditar_catalogo([injetada]) == [("r_rn413_art5_par2", "DEADLINE_NOT_ANCHORED")]


def test_suspension_extends_deadline() -> None:
    par2 = _par2()
    assert set(par2.regras_suspensao.split(";")) == {"RN413-art5-par5", "RN413-art5-par7"}
    injetada = dataclasses.replace(par2, regras_suspensao="RN413-art5-par5")
    assert auditar_catalogo([injetada]) == [("r_rn413_art5_par2", "SUSPENSION_IGNORED")]


def test_tracker_no_eta_without_ratified_deadline() -> None:
    for ref in ("RN413-art5-par2", "RN413-art5-par7", "Lei9656-art12-VI", "estagio-5-analise"):
        assert motivo_eta(linha_para(ref), ancora_observada=True) == SEM_ETA_NAO_RATIFICADO
    # A regra nao e' constante: com lastro completo ela libera; sem ancora observada ela recusa.
    ratificada = dataclasses.replace(
        _par2(), status="RATIFIED", regulatory_approval_ref="REF-J", evento_ancora_ref="ancora-x"
    )
    assert motivo_eta(ratificada, ancora_observada=True) is None
    assert motivo_eta(ratificada, ancora_observada=False) == SEM_ETA_ANCORA_AUSENTE


def test_tracker_bans_generic_status_labels() -> None:
    todas = linhas()
    verbatim = frozenset(x.dispositivo_verbatim for x in todas)
    textos = {**textos_do_catalogo(todas), **textos_do_assistente_de_reembolso()}
    assert [
        m
        for m in auditar_textos_ao_comprador(textos, verbatim=verbatim)
        if m[1] == "generic_status_label_refused"
    ] == []
    assert auditar_textos_ao_comprador({"rotulo": "Em Análise"}) == [
        ("rotulo", "generic_status_label_refused")
    ]
    assert auditar_textos_ao_comprador({"rotulo": "em processamento"}) == [
        ("rotulo", "generic_status_label_refused")
    ]


def test_no_draft_iso_duration_in_buyer_projection() -> None:
    draft = duracoes_draft_internas()
    assert {"P5D", "P30D", "PT4H"} <= draft
    todas = linhas()
    verbatim = frozenset(x.dispositivo_verbatim for x in todas)
    textos = {**textos_do_catalogo(todas), **textos_do_assistente_de_reembolso()}
    assert auditar_textos_ao_comprador(textos, verbatim=verbatim) == []
    citacao = _par2().dispositivo_citacao
    assert auditar_textos_ao_comprador({"citacao": f"{citacao} (P5D)"}) == [
        ("citacao", "DRAFT_DEADLINE_DISCLOSED")
    ]


def _propriedades_alcancaveis(contrato: dict[str, Any], raiz: str) -> set[str]:
    defs = contrato["$defs"]
    vistos, nomes, pilha = {raiz}, set(), [defs[raiz]]
    while pilha:
        no = pilha.pop()
        if isinstance(no, dict):
            ref = no.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                alvo = ref.rsplit("/", 1)[-1]
                if alvo not in vistos:
                    vistos.add(alvo)
                    pilha.append(defs[alvo])
            nomes.update(no.get("properties", {}))
            pilha.extend(no.values())
        elif isinstance(no, list):
            pilha.extend(no)
    return nomes


def test_journey_snapshot_wire_has_no_deadline_field() -> None:
    """Portao fechado do wire: o C6-4 congelado nao tem campo de prazo/ETA; acrescentar um e' mudanca do
    sucessor (AMH) e tem de passar pela regra de ETA acima — este teste muda junto, nunca antes."""
    contrato = json.loads(_C6_4.read_text(encoding="utf-8"))
    nomes = _propriedades_alcancaveis(contrato, "CustomerJourneySnapshot")
    assert len(nomes) > 50  # a varredura alcancou o snapshot de fato
    assert not (nomes & _CAMPOS_DE_PRAZO)

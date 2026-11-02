"""Inventario somente-leitura das ordens PAGTO sob a chave lote do handoff CONTAS (script de ops).

Decisao do dono de 2026-11-01 (opcao A): plano de migracao das ordens criadas sob a forma lote.
Sem rede, sem motor: o script le dois exports JSON e classifica. Fixa cada disposicao, a chave de
alias proposta (o MESMO compositor do worker, com o prestador — DEC-30 D13), as recusas R-101/D13
(guia ou prestador com '-'), o cruzamento que acha os pagamentos perdidos (inclusive de outro
prestador) e o codigo de saida de `--estrito`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.ops.pagto_chave_conta_inventario import (
    ALIAS_BLOQUEADO_GUIA_COM_HIFEN,
    ALIAS_BLOQUEADO_PRESTADOR_COM_HIFEN,
    ALIAS_POR_CONTA,
    FORA_DE_ESCOPO,
    FORMA_LOTE,
    INDETERMINADA,
    JA_POR_CONTA,
    classificar_ordem,
    contas_sem_ordem,
    inventariar,
    main,
)

from maezo.tools.workers.contas import _pagto_business_key, _pagto_business_key_por_conta

_BASE = {"tenant_id": "amh", "numero_lote_tiss": "LOTE-9", "prestador_id": "P1"}
_CHAVE_LOTE = "PAGTO-amh-LOTE-9-P1"


def _ordem(chave: str, instancia: str = "i-1", **variaveis: object) -> dict[str, object]:
    return {"id": instancia, "businessKey": chave, "state": "ACTIVE", "variables": {**_BASE, **variaveis}}


def test_a_chave_lote_recomposta_e_a_mesma_do_worker() -> None:
    assert _pagto_business_key("amh", "LOTE-9", "P1") == _CHAVE_LOTE


def test_ordem_lote_de_conta_conhecida_propoe_alias_com_o_compositor_do_worker() -> None:
    linha = classificar_ordem(_ordem(_CHAVE_LOTE, numero_guia_tiss="G1", numero_conta="C1"))
    assert linha["disposicao"] == ALIAS_POR_CONTA
    assert linha["chave_por_conta"] == "PAGTO-amh-C-P1-G1-C1"
    assert linha["chave_por_conta"] == _pagto_business_key_por_conta("amh", "P1", "G1", "C1")


def test_ordem_da_forma_lote_fica() -> None:
    linha = classificar_ordem(_ordem(_CHAVE_LOTE, numero_guia_tiss="G1", numero_conta=""))
    assert linha["disposicao"] == FORMA_LOTE
    assert linha["chave_por_conta"] is None


def test_ordem_sem_conta_semeada_e_indeterminada() -> None:
    assert classificar_ordem(_ordem(_CHAVE_LOTE, numero_guia_tiss="G1"))["disposicao"] == INDETERMINADA


def test_guia_com_hifen_bloqueia_o_alias() -> None:
    linha = classificar_ordem(_ordem(_CHAVE_LOTE, numero_guia_tiss="G-1", numero_conta="C1"))
    assert linha["disposicao"] == ALIAS_BLOQUEADO_GUIA_COM_HIFEN
    assert "numero_guia_tiss" in linha["motivo"]


def test_prestador_com_hifen_bloqueia_o_alias() -> None:
    """DEC-30 D13: o prestador e' segmento do meio da chave `C`; com '-' ela recusa — humano decide."""
    chave_lote = "PAGTO-amh-LOTE-9-prov:teste-0001"
    linha = classificar_ordem(
        _ordem(chave_lote, prestador_id="prov:teste-0001", numero_guia_tiss="G1", numero_conta="C1")
    )
    assert linha["disposicao"] == ALIAS_BLOQUEADO_PRESTADOR_COM_HIFEN
    assert "prestador_id" in linha["motivo"]
    assert linha["chave_por_conta"] is None
    assert (
        inventariar(
            [_ordem(chave_lote, prestador_id="prov:teste-0001", numero_guia_tiss="G1", numero_conta="C1")]
        )["exige_humano"]
        == 1
    )


@pytest.mark.parametrize("chave", ["PAGTO-amh-ORDEM-77", "PAGTO-amh-GUIA1-GLOSA-1", ""])
def test_formas_de_andre_e_recurso_ficam_fora_de_escopo(chave: str) -> None:
    assert classificar_ordem(_ordem(chave, numero_guia_tiss="G1"))["disposicao"] == FORA_DE_ESCOPO


def test_ordem_ja_na_forma_por_conta() -> None:
    linha = classificar_ordem(_ordem("PAGTO-amh-C-P1-G1-C1", numero_guia_tiss="G1", numero_conta="C1"))
    assert linha["disposicao"] == JA_POR_CONTA


def test_cruzamento_acha_a_segunda_conta_sem_ordem() -> None:
    """O defeito no export: a conta C2 recebeu `pagto_already_existed=True` apontando a ordem da C1."""
    ordens = [_ordem(_CHAVE_LOTE, "pagto-c1", numero_guia_tiss="G1", numero_conta="C1")]
    contas = [
        {
            "id": "contas-c1",
            "variables": {
                "numero_guia_tiss": "G1",
                "numero_conta": "C1",
                "pagto_instance_id": "pagto-c1",
                "pagto_already_existed": False,
            },
        },
        {
            "id": "contas-c1-reenvio",
            "variables": {
                "numero_guia_tiss": "G1",
                "numero_conta": "C1",
                "pagto_instance_id": "pagto-c1",
                "pagto_already_existed": True,
            },
        },
        {
            "id": "contas-c2",
            "variables": {
                "numero_guia_tiss": "G1",
                "numero_conta": "C2",
                "pagto_instance_id": "pagto-c1",
                "pagto_already_existed": True,
            },
        },
        {
            "id": "contas-lote",
            "variables": {
                "numero_guia_tiss": "",
                "numero_conta": "",
                "pagto_instance_id": "pagto-c1",
                "pagto_already_existed": True,
            },
        },
    ]
    assert contas_sem_ordem(contas, ordens) == [
        {"contas_instance_id": "contas-c2", "pagto_instance_id": "pagto-c1", "motivo": "ordem_de_outra_conta"}
    ]


@pytest.mark.parametrize(
    ("variaveis_da_ordem", "motivo"),
    [
        ({"numero_guia_tiss": "G9", "numero_conta": "C1"}, "ordem_de_outra_guia"),
        ({"numero_guia_tiss": "G1"}, "ordem_sem_conta_semeada"),
    ],
)
def test_cruzamento_motivos(variaveis_da_ordem: dict[str, str], motivo: str) -> None:
    ordens = [_ordem(_CHAVE_LOTE, "pagto-x", **variaveis_da_ordem)]
    contas = [
        {
            "id": "c",
            "variables": {
                "numero_guia_tiss": "G1",
                "numero_conta": "C1",
                "pagto_instance_id": "pagto-x",
                "pagto_already_existed": True,
            },
        }
    ]
    assert contas_sem_ordem(contas, ordens)[0]["motivo"] == motivo
    contas[0]["variables"]["pagto_instance_id"] = "fora"  # type: ignore[index]
    assert contas_sem_ordem(contas, ordens)[0]["motivo"] == "ordem_fora_do_export"


def test_cruzamento_acha_a_conta_de_outro_prestador_com_a_mesma_guia_e_conta() -> None:
    """DEC-30 D13: a ordem e' da MESMA guia/conta mas de OUTRO prestador — pagamento perdido."""
    ordens = [_ordem(_CHAVE_LOTE, "pagto-x", numero_guia_tiss="G1", numero_conta="C1")]
    contas = [
        {
            "id": "c-outro",
            "variables": {
                "prestador_id": "P2",
                "numero_guia_tiss": "G1",
                "numero_conta": "C1",
                "pagto_instance_id": "pagto-x",
                "pagto_already_existed": True,
            },
        },
        {
            "id": "c-mesmo",
            "variables": {
                "prestador_id": " P1 ",
                "numero_guia_tiss": "G1",
                "numero_conta": "C1",
                "pagto_instance_id": "pagto-x",
                "pagto_already_existed": True,
            },
        },
    ]
    assert contas_sem_ordem(contas, ordens) == [
        {
            "contas_instance_id": "c-outro",
            "pagto_instance_id": "pagto-x",
            "motivo": "ordem_de_outro_prestador",
        }
    ]


def test_relatorio_conta_o_que_exige_humano_e_se_declara_somente_leitura() -> None:
    relatorio = inventariar(
        [
            _ordem(_CHAVE_LOTE, "a", numero_guia_tiss="G1", numero_conta="C1"),
            _ordem(_CHAVE_LOTE, "b", numero_guia_tiss="G1"),
            _ordem("PAGTO-amh-ORDEM-1", "c"),
        ]
    )
    assert relatorio["somente_leitura"] is True
    assert relatorio["contagem"] == {ALIAS_POR_CONTA: 1, FORA_DE_ESCOPO: 1, INDETERMINADA: 1}
    assert relatorio["exige_humano"] == 1


def test_cli_estrito_sai_2_com_item_humano_e_0_sem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    com_humano = tmp_path / "pagto.json"
    com_humano.write_text(json.dumps([_ordem(_CHAVE_LOTE, numero_guia_tiss="G1")]), encoding="utf-8")
    assert main(["--pagto", str(com_humano), "--estrito"]) == 2
    assert main(["--pagto", str(com_humano)]) == 0
    limpo = tmp_path / "limpo.json"
    limpo.write_text(
        json.dumps([_ordem(_CHAVE_LOTE, numero_guia_tiss="G1", numero_conta="")]), encoding="utf-8"
    )
    assert main(["--pagto", str(limpo), "--estrito"]) == 0
    saida = capsys.readouterr().out
    assert '"somente_leitura": true' in saida

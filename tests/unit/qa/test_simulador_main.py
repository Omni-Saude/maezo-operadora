"""A linha de comando do simulador e as pecas puras do `__main__` (sem AWS, sem banco)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from maezo.qa.simulador import __main__ as principal
from maezo.qa.simulador.armazenamento import ArmazenamentoEmMemoria
from maezo.qa.simulador.registro import Avaliacao

from .apoio_simulador import CPF_SINTETICO, conversa, fatos_de_julho, sujeito, turno


def test_parser_padroes_do_desenho() -> None:
    args = principal.construir_parser().parse_args([])
    assert args.conversas == 10 and args.concorrencia == 3
    assert args.consentimento_terceiros == "somente-leitura"
    assert args.repetir_falhas is None and args.limpar is None and not args.sem_juiz
    args = principal.construir_parser().parse_args(
        ["--conversas", "40", "--personas", "idoso,contesta", "--consentimento-terceiros", "completo"]
    )
    assert args.conversas == 40 and args.personas == "idoso,contesta"


def test_run_id_e_ordenavel_e_sem_barra() -> None:
    run_id = principal.novo_run_id(datetime(2026, 10, 12, 22, 15, 0, tzinfo=UTC))
    assert re.fullmatch(r"20261012T221500Z-[0-9a-f]{6}", run_id)


def test_item_mascara_achados_transcricao_e_motivo_do_juiz() -> None:
    c = conversa(
        turno(1, texto=CPF_SINTETICO, fase="acesso", tema="acesso", acesso=["Obrigada!"]),
        turno(2, tema="cobranca", helena=["Oi Joana, seu CPF 529.982.247-25"], fatos=[fatos_de_julho()]),
    )
    avaliacao = Avaliacao(4, 4, 4, 4, 4, False, False, "vazamento_de_dados", 2, "ecoou Joana", None)
    item = principal._item(c, avaliacao)
    dump = json.dumps({"t": item.transcricao, "a": [[a.motivo for a in r.achados] for r in item.checagens]})
    assert CPF_SINTETICO not in dump and "Joana" not in dump and "529.982" not in dump
    assert item.avaliacao is not None and "Joana" not in item.avaliacao.motivo
    assert any(r.checagem == "D9" and not r.passou for r in item.checagens)


async def test_planos_de_repeticao_so_as_conversas_que_falharam() -> None:
    memoria = ArmazenamentoEmMemoria()
    manifesto = {
        "roster": "roster/x.csv",
        "conversas": [
            {"id": "c0001", "persona": "idoso", "sujeito": "s001", "semente": 1, "telefone": "5511900000501"},
            {
                "id": "c0002",
                "persona": "contesta",
                "sujeito": "s001",
                "semente": 2,
                "telefone": "5511900000501",
            },
            {
                "id": "c0003",
                "persona": "apressado",
                "sujeito": "s999",
                "semente": 3,
                "telefone": "5511900000502",
            },
        ],
    }
    await memoria.gravar("runs/r0/manifest.json", json.dumps(manifesto), tipo="application/json")
    checks = [
        {"conversa": "c0001", "checagens": {"D1": {"aplicavel": True, "passou": False}}},
        {"conversa": "c0002", "checagens": {"D1": {"aplicavel": True, "passou": True}}},
        {"conversa": "c0003", "checagens": {"D1": {"aplicavel": True, "passou": False}}},
    ]
    await memoria.gravar("runs/r0/checks.jsonl", "\n".join(json.dumps(c) for c in checks), tipo="x")
    await memoria.gravar("runs/r0/judge.jsonl", json.dumps({"conversa": "c0002", "falha": "tom"}), tipo="x")
    planos, roster = await principal._planos_de_repeticao(memoria, "r0", [sujeito(rotulo="s001")])  # type: ignore[arg-type]
    assert roster == "roster/x.csv"
    # c0003 falhou mas a pessoa nao esta' no roster atual: fica de fora
    assert [(p.id, p.persona.id, p.semente) for p in planos] == [
        ("c0001", "idoso", 1),
        ("c0002", "contesta", 2),
    ]


def test_main_devolve_codigo_de_preparo_sem_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_SIMULADOR_BUCKET", raising=False)
    assert principal.main(["--conversas", "1"]) == principal.SAIDA_PREPARO


def test_argumentos_vem_do_ambiente_na_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULADOR_ARGS", "--conversas 20 --personas 'idoso,contesta'")
    monkeypatch.setattr("sys.argv", ["simulador", "--sem-juiz"])
    assert principal.argumentos_do_ambiente(None) == [
        "--conversas",
        "20",
        "--personas",
        "idoso,contesta",
        "--sem-juiz",
    ]
    assert principal.argumentos_do_ambiente(["--limpar", "r1"]) == ["--limpar", "r1"]


def test_parser_aceita_o_roteiro_do_dono_e_recusa_outro() -> None:
    assert principal.construir_parser().parse_args(["--roteiro", "dono_v1"]).roteiro == "dono_v1"
    assert principal.construir_parser().parse_args([]).roteiro is None
    with pytest.raises(SystemExit):
        principal.construir_parser().parse_args(["--roteiro", "inexistente"])

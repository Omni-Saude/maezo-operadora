"""Roster (CSV do Athena + preflight), armazenamento e relatorio/agrupamento de falhas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.qa.simulador import armazenamento as arm
from maezo.qa.simulador import relatorio as rel
from maezo.qa.simulador.registro import Achado, Avaliacao, ResultadoChecagem, Sujeito
from maezo.qa.simulador.roster import CPF_DO_DONO, RosterInvalidoError, ler_roster_csv, preflight

from .apoio_simulador import CPF_SINTETICO

_OUTRO_CPF = "11144477735"


def _csv(*linhas: str) -> str:
    return '"cpf","nascimento","nome"\n' + "\n".join(linhas) + "\n"


# --- roster ---------------------------------------------------------------------------------------
def test_roster_le_csv_do_athena_descarta_invalido_e_poe_o_dono_primeiro() -> None:
    texto = _csv(
        f'"{CPF_SINTETICO}","1980-03-14","Joana Silva"',
        f'"{CPF_DO_DONO}","1990-01-02 00:00:00.000","Dono"',
        '"12345678900","1980-01-01","CPF invalido"',
        f'"{_OUTRO_CPF}","","Sem nascimento"',
        f'"{CPF_SINTETICO}","1980-03-14","Duplicado"',
        f'"{_OUTRO_CPF}","02/05/1975","  Ana   Souza "',
    )
    sujeitos = ler_roster_csv(texto)
    assert [s.rotulo for s in sujeitos] == ["s000", "s001", "s002"]
    assert sujeitos[0].dono and sujeitos[0].cpf == CPF_DO_DONO
    assert {s.cpf for s in sujeitos} == {CPF_DO_DONO, CPF_SINTETICO, _OUTRO_CPF}
    ana = next(s for s in sujeitos if s.cpf == _OUTRO_CPF)
    assert ana.nome == "Ana Souza" and ana.nascimento.isoformat() == "1975-05-02"


def test_repr_do_sujeito_nao_mostra_dado_pessoal() -> None:
    (s,) = ler_roster_csv(_csv(f'"{CPF_SINTETICO}","1980-03-14","Joana Silva"'))
    assert CPF_SINTETICO not in repr(s) and "Joana" not in repr(s)


def test_roster_sem_cabecalho_recusa_sem_ecoar() -> None:
    with pytest.raises(RosterInvalidoError) as erro:
        ler_roster_csv(f"{CPF_SINTETICO},1980-03-14,Joana\n")
    assert CPF_SINTETICO not in str(erro.value)


async def test_preflight_descarta_quem_nao_resolve_e_para_no_limite() -> None:
    sujeitos = ler_roster_csv(
        _csv(
            f'"{CPF_DO_DONO}","1990-01-02","Dono"',
            f'"{CPF_SINTETICO}","1980-03-14","Joana"',
            f'"{_OUTRO_CPF}","1975-05-02","Ana"',
        )
    )
    chamadas: list[str] = []

    async def resolver(s: Sujeito) -> str | None:
        chamadas.append(s.rotulo)
        if s.cpf == CPF_SINTETICO:
            raise RuntimeError("amh fora")
        return f"ref-{s.rotulo}"

    resolvidos, descartados = await preflight(sujeitos, resolver, limite=1, intervalo_s=0)
    assert [s.ref for s in resolvidos] == ["ref-s000"] and descartados == 0
    resolvidos, descartados = await preflight(sujeitos, resolver, intervalo_s=0)
    assert len(resolvidos) == 2 and descartados == 1


# --- armazenamento --------------------------------------------------------------------------------
def test_chave_da_execucao_recusa_caminho_fora_de_runs() -> None:
    assert arm.chave_da_execucao("r1", "report.md") == "runs/r1/report.md"
    for ruim in ("", "../x", "a/b"):
        with pytest.raises(ValueError):
            arm.chave_da_execucao(ruim, "x")


async def test_roster_mais_recente_e_sem_roster() -> None:
    memoria = arm.ArmazenamentoEmMemoria()
    with pytest.raises(FileNotFoundError):
        await arm.roster_mais_recente(memoria)
    await memoria.gravar("roster/a.csv", "x", tipo="text/csv")
    await memoria.gravar("roster/b.csv", "x", tipo="text/csv")
    await memoria.gravar("roster/b.csv.metadata", "x", tipo="x")
    assert await arm.roster_mais_recente(memoria) == "roster/b.csv"


class _ClienteS3:
    def __init__(self) -> None:
        self.objetos: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objetos[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        corpo = self.objetos[Key]

        class _Corpo:
            def read(self) -> bytes:
                return corpo

        return {"Body": _Corpo()}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        if "ContinuationToken" not in kwargs:
            return {
                "Contents": [{"Key": "roster/1.csv", "LastModified": datetime(2026, 1, 1, tzinfo=UTC)}],
                "IsTruncated": True,
                "NextContinuationToken": "t",
            }
        return {"Contents": [{"Key": "roster/2.csv", "LastModified": datetime(2026, 1, 2, tzinfo=UTC)}]}


async def test_armazenamento_s3_grava_le_e_pagina() -> None:
    s3 = arm.ArmazenamentoS3("bucket", regiao="sa-east-1", cliente=_ClienteS3())
    await s3.gravar("runs/r/x.json", "{}", tipo="application/json")
    assert await s3.ler("runs/r/x.json") == "{}"
    assert await arm.roster_mais_recente(s3) == "roster/2.csv"
    with pytest.raises(ValueError):
        arm.ArmazenamentoS3("", regiao="sa-east-1", cliente=_ClienteS3())


def test_jsonl_uma_linha_por_objeto() -> None:
    assert arm.jsonl([{"a": 1}, {"b": "ç"}]) == '{"a": 1}\n{"b": "ç"}\n'


# --- relatorio / agrupamento -----------------------------------------------------------------------
def _avaliacao(falha: str = "nenhuma", nota: int = 5, erro: str | None = None) -> Avaliacao:
    return Avaliacao(nota, nota, nota, nota, nota, False, False, falha, None, "motivo", erro)


def _item(cid: str, persona: str, achados: list[Achado], avaliacao: Avaliacao | None) -> rel.ItemDaExecucao:
    checagens = [ResultadoChecagem("D2", True, [a for a in achados if a.checagem == "D2"])]
    checagens.append(ResultadoChecagem("D5", False, []))
    return rel.ItemDaExecucao(
        conversa_id=cid,
        persona_id=persona,
        sujeito="s001",
        checagens=checagens,
        avaliacao=avaliacao,
        transcricao=[
            {
                "turno": 1,
                "fase": "persona",
                "beneficiario": "quanto pago?",
                "tema": "cobranca",
                "respostas": [{"agente": "lucas", "texto": "R$ [valor]"}],
                "escalonamentos": [],
                "erro": None,
            }
        ],
    )


def test_normalizar_motivo_tira_a_instancia() -> None:
    assert (
        rel.normalizar_motivo("valor R$ 450.00 fora dos fatos do turno")
        == "valor R$ # fora dos fatos do turno"
    )
    assert rel.normalizar_motivo("data 15/11/2026 fora") == "data # fora"
    assert rel.normalizar_motivo("mes 'julho' sem 'de 20xx'") == "mes '…' sem '…'"
    assert rel.normalizar_motivo("palavra proibida 'multa'") == "palavra proibida 'multa'"


def test_agrupar_falhas_junta_o_mesmo_defeito_e_conta_conversas() -> None:
    itens = [
        _item("c1", "apressado", [Achado("D2", "valor R$ 1.00 fora dos fatos do turno", 1, "lucas")], None),
        _item("c2", "idoso", [Achado("D2", "valor R$ 9.00 fora dos fatos do turno", 2, "lucas")], None),
        _item("c3", "idoso", [], _avaliacao("tom_inadequado")),
        _item("c4", "idoso", [], _avaliacao(nota=2)),
        _item("c5", "idoso", [], _avaliacao(erro="RuntimeError")),
    ]
    grupos = rel.agrupar_falhas(itens)
    assert grupos[0].chave == ("D2", "lucas", "valor R$ # fora dos fatos do turno")
    assert grupos[0].conversas == ["c1", "c2"] and grupos[0].personas == {"apressado": 1, "idoso": 1}
    chaves = {g.chave for g in grupos}
    assert ("juiz", "-", "tom_inadequado") in chaves and ("juiz", "-", "nota_baixa") in chaves
    assert all("c5" not in g.conversas for g in grupos)  # juiz indisponivel nao vira falha nem aprovacao


def test_resumo_e_relatorio() -> None:
    itens = [
        _item(
            "c1",
            "apressado",
            [Achado("D2", "valor R$ 1.00 fora dos fatos do turno", 1, "lucas")],
            _avaliacao(),
        ),
        _item("c2", "idoso", [], _avaliacao()),
    ]
    dados = rel.resumo(itens, execucao={"latencia_p95_s": 3.0}, extras={"consentimento": {"gravados": 1}})
    assert dados["conversas"] == 2 and dados["conversas_aprovadas"] == 1
    assert dados["por_checagem"]["D2"] == {
        "descricao": "R$ e datas do Lucas estao nos fatos do turno",
        "aplicavel": 2,
        "aprovadas": 1,
        "taxa": 0.5,
    }
    assert dados["por_checagem"]["D5"]["taxa"] is None
    assert dados["achados_por_agente"] == {"lucas": 1}
    assert dados["consentimento"] == {"gravados": 1}
    md = rel.relatorio_md("r1", dados, rel.agrupar_falhas(itens), itens)
    assert "# Simulador de beneficiario" in md and "Exemplo `c1`" in md
    assert "D2 · lucas · valor R$ # fora dos fatos do turno" in md

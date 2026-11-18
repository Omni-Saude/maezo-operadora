"""O documento da primeira linha do roster e' parametro OBRIGATORIO da execucao, nunca um literal.

Guarda: nenhum CPF com digito verificador correto em `src/maezo/qa/**` nem em `docs/runbooks/**` (numero
com verificador errado e' sintetico e passa). A falha cita so' `arquivo:linha`, nunca o numero. Os CPFs
validos destes testes sao gerados na hora (`cpf_sintetico_valido`), nunca escritos aqui.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.platform.webhooks.whatsapp.acesso import cpf_valido
from maezo.qa.simulador import __main__ as principal
from maezo.qa.simulador import roster
from maezo.qa.simulador.armazenamento import ArmazenamentoEmMemoria
from maezo.qa.simulador.condutor import PlanoDeConversa
from maezo.qa.simulador.limpeza import ResultadoDaLimpeza
from maezo.qa.simulador.registro import Conversa

from .apoio_simulador import cpf_sintetico_valido, digitos_verificadores, turno

_RAIZ = Path(__file__).resolve().parents[3]
_PASTAS_GUARDADAS = (_RAIZ / "src" / "maezo" / "qa", _RAIZ / "docs" / "runbooks")
_SIMULADOR = _RAIZ / "src" / "maezo" / "qa" / "simulador"
#: 11 digitos, com ou sem a pontuacao `000.000.000-00`, sem digito colado antes nem depois.
_CANDIDATO = re.compile(r"(?<!\d)(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})(?!\d)")


def _cpf_invalido() -> str:
    """11 digitos com o verificador ERRADO (o ultimo digito trocado)."""
    valido = cpf_sintetico_valido()
    return valido[:10] + str((int(valido[10]) + 1) % 10)


def _pontuado(cpf: str) -> str:
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


# --- guarda do repositorio ------------------------------------------------------------------------------
def _cpfs_validos(texto: str) -> int:
    return sum(1 for m in _CANDIDATO.finditer(texto) if cpf_valido("".join(m.groups())))


def test_nenhum_cpf_valido_em_src_qa_nem_nos_runbooks() -> None:
    achados: list[str] = []
    for pasta in _PASTAS_GUARDADAS:
        for arquivo in sorted(pasta.rglob("*")):
            if not arquivo.is_file() or "__pycache__" in arquivo.parts:
                continue
            try:
                linhas = arquivo.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            achados.extend(
                f"{arquivo.relative_to(_RAIZ)}:{n}"
                for n, linha in enumerate(linhas, 1)
                if _cpfs_validos(linha)
            )
    # So' a localizacao: o numero nunca vai para a mensagem do teste.
    assert not achados, f"CPF com verificador valido no repositorio (troque por parametro): {achados}"


def test_a_guarda_acha_cpf_valido_com_e_sem_pontuacao_e_ignora_o_invalido() -> None:
    valido = cpf_sintetico_valido()
    pontuado = _pontuado(valido)
    assert _cpfs_validos(f"x {valido} y") == 1 and _cpfs_validos(f"CPF {pontuado}.") == 1
    assert _cpfs_validos(_cpf_invalido()) == 0 and _cpfs_validos("1" + valido) == 0
    assert digitos_verificadores(valido[:9]) == valido[9:]


# --- o parametro obrigatorio ----------------------------------------------------------------------------
def _sem_executar(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    chamadas: list[str] = []

    async def executar(_args: Any, documento_primeira_linha: str) -> int:
        chamadas.append(documento_primeira_linha)
        return principal.SAIDA_OK

    monkeypatch.setattr(principal, "executar", executar)
    return chamadas


def test_sem_o_parametro_o_simulador_nao_comeca(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(roster.VARIAVEL_DO_DOCUMENTO, raising=False)
    chamadas = _sem_executar(monkeypatch)
    assert principal.main(["--conversas", "1"]) == principal.SAIDA_PREPARO
    assert chamadas == []
    erro = capsys.readouterr().err
    assert "falta o parametro obrigatorio" in erro and roster.VARIAVEL_DO_DOCUMENTO in erro


@pytest.mark.parametrize("so_espaco", ["", "   "])
def test_parametro_vazio_e_o_mesmo_que_ausente(monkeypatch: pytest.MonkeyPatch, so_espaco: str) -> None:
    monkeypatch.setenv(roster.VARIAVEL_DO_DOCUMENTO, so_espaco)
    chamadas = _sem_executar(monkeypatch)
    assert principal.main(["--conversas", "2"]) == principal.SAIDA_PREPARO and chamadas == []


def _sem_vazamento(valor: str, *textos: str) -> None:
    """Nem o valor cru nem pontuado aparece em nenhum dos textos (a mensagem nunca mostra o valor)."""
    formas = (valor, _pontuado(valor))
    vazou = [i for i, texto in enumerate(textos) if any(f in texto for f in formas)]
    assert not vazou, f"documento da primeira linha vazou nos textos de indice {vazou}"


@pytest.mark.parametrize(
    "fabrica",
    [
        _cpf_invalido,
        lambda: cpf_sintetico_valido()[:10],  # 10 digitos
        lambda: _pontuado(cpf_sintetico_valido()),  # so' digitos: pontuacao e' recusada
        lambda: cpf_sintetico_valido() + "' OR '1'='1",  # tentativa de injecao
        lambda: "1" * 11,  # todos iguais
    ],
)
def test_parametro_invalido_e_recusado_sem_ecoar_o_valor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    fabrica: Any,
) -> None:
    valor = fabrica()
    monkeypatch.setenv(roster.VARIAVEL_DO_DOCUMENTO, valor)
    chamadas = _sem_executar(monkeypatch)
    with capture_logs() as logs, caplog.at_level(logging.DEBUG):
        assert principal.main(["--conversas", "1"]) == principal.SAIDA_PREPARO
    assert chamadas == []
    saida = capsys.readouterr()
    assert "nao e' um CPF valido" in saida.err
    assert any(e["event"] == "simulador_sem_documento_da_primeira_linha" for e in logs)
    textos = (saida.err, saida.out, json.dumps(logs, default=str), caplog.text)
    assert not any(valor in x for x in textos)
    _sem_vazamento(valor[:11], *textos)


def test_parametro_valido_chega_a_execucao_e_nao_vai_para_os_argumentos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento = cpf_sintetico_valido()
    monkeypatch.setenv(roster.VARIAVEL_DO_DOCUMENTO, f" {documento} ")
    chamadas = _sem_executar(monkeypatch)
    assert principal.main(["--conversas", "1"]) == principal.SAIDA_OK
    assert chamadas == [documento]
    # `vars(args)` vai inteiro para o manifesto: o documento nao pode ser argumento.
    assert all("documento" not in opcao for opcao in vars(principal.construir_parser().parse_args([])))


# --- execucao completa com fakes: o valor nao vaza em log, manifesto, relatorio nem transcricao ---------
class _CondutorFalso:
    """Conversa sintetica que ECOA o documento da pessoa (pior caso): a saida gravada tem de mascara'-lo."""

    def __init__(self, **_: Any) -> None:
        pass

    def conversation_id(self, telefone: str) -> str:
        return f"wa:sim:{telefone[-4:]}"

    async def executar(self, planos: list[PlanoDeConversa]) -> list[Conversa]:
        conversas = []
        for p in planos:
            doc = p.sujeito.cpf
            conversas.append(
                Conversa(
                    id=p.id,
                    persona_id=p.persona.id,
                    sujeito=p.sujeito,
                    telefone=p.telefone,
                    conversation_id=self.conversation_id(p.telefone),
                    semente=p.semente,
                    turnos=[
                        turno(1, texto=doc, fase="acesso", tema="acesso", acesso=["Obrigada!"]),
                        turno(2, texto=f"meu cpf e' {_pontuado(doc)}", helena=[f"Recebi o CPF {doc}."]),
                    ],
                )
            )
        return conversas


class _LimpezaFalsa:
    async def limpar_execucao(self, ids: list[str]) -> ResultadoDaLimpeza:
        return ResultadoDaLimpeza(conversas=len(ids))

    async def aclose(self) -> None:
        return None


def test_execucao_completa_nao_grava_nem_loga_o_documento(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    documento = cpf_sintetico_valido()
    outro = cpf_sintetico_valido(excluir={documento})
    monkeypatch.setenv(roster.VARIAVEL_DO_DOCUMENTO, documento)
    memoria = ArmazenamentoEmMemoria()
    memoria.objetos["roster/20261101T000000Z.csv"] = (
        f"cpf,nascimento,nome\n{outro},1980-03-14,Joana Silva\n{documento},1990-01-02,Primeira Pessoa\n"
    )
    memoria.bucket = "bucket-falso"  # type: ignore[attr-defined]

    async def resolver(sujeito: Any) -> str:
        return f"ref-{sujeito.rotulo}"

    async def compor(*_: Any, **__: Any) -> Any:
        async def aclose() -> None:
            return None

        return SimpleNamespace(
            resolver=resolver,
            consentimento=None,
            escalonamentos=SimpleNamespace(total=0),
            captura=SimpleNamespace(fora_de_turno=0),
            tenant_id="tenant-falso",
            dispatcher=SimpleNamespace(inference=None),
            aclose=aclose,
        )

    monkeypatch.setattr(principal, "ArmazenamentoS3", lambda *_a, **_k: memoria)
    monkeypatch.setattr(principal, "compor", compor)
    monkeypatch.setattr(principal, "_settings", lambda: None)
    monkeypatch.setattr(principal, "_limpeza_da", lambda _c: _LimpezaFalsa())
    monkeypatch.setattr(principal, "inferencia_crua", lambda _c: None)
    monkeypatch.setattr(principal, "gerador_da_inferencia", lambda *_a, **_k: None)
    monkeypatch.setattr(principal, "BeneficiarioLLM", lambda *_a, **_k: None)
    monkeypatch.setattr(principal, "Condutor", _CondutorFalso)

    with capture_logs() as logs, caplog.at_level(logging.DEBUG):
        codigo = principal.main(["--conversas", "2", "--sem-juiz", "--max-sujeitos", "1", "--semente", "7"])
    assert codigo == principal.SAIDA_OK, logs

    gravados = {k: v for k, v in memoria.objetos.items() if k.startswith("runs/")}
    nomes = {k.rsplit("/", 1)[-1] for k in gravados}
    assert {"manifest.json", "transcripts.jsonl", "checks.jsonl", "summary.json", "report.md"} <= nomes
    manifesto = json.loads(next(v for k, v in gravados.items() if k.endswith("manifest.json")))
    assert manifesto["status"] == "concluida" and manifesto["conversas"][0]["sujeito"] == "s000"
    assert not any("documento" in opcao for opcao in manifesto["argumentos"])
    # A primeira linha do roster (quem tem o documento) rodou, e nada a mostra.
    assert any(e["event"] == "simulador_execucao_concluida" for e in logs)
    # A verificacao enxerga o valor onde ele esta' de fato (o roster de entrada) e a transcricao o mascara.
    with pytest.raises(AssertionError):
        _sem_vazamento(documento, memoria.objetos["roster/20261101T000000Z.csv"])
    transcricoes = next(v for k, v in gravados.items() if k.endswith("transcripts.jsonl"))
    assert "Recebi o CPF" in transcricoes
    saida = capsys.readouterr()
    _sem_vazamento(
        documento,
        *gravados.values(),
        json.dumps(logs, default=str),
        caplog.text,
        saida.out,
        saida.err,
    )


def test_ler_roster_exige_documento_valido_e_marca_a_primeira_linha() -> None:
    documento = cpf_sintetico_valido()
    outro = cpf_sintetico_valido(excluir={documento})
    texto = f"cpf,nascimento,nome\n{outro},1980-03-14,Joana\n{documento},1990-01-02,Primeira\n"
    sujeitos = roster.ler_roster_csv(texto, documento_primeira_linha=documento)
    assert sujeitos[0].dono and sujeitos[0].cpf == documento and not sujeitos[1].dono
    with pytest.raises(roster.DocumentoDaPrimeiraLinhaError) as erro:
        roster.ler_roster_csv(texto, documento_primeira_linha=_cpf_invalido())
    assert not re.search(r"\d{11}", str(erro.value))


# --- os SQLs: parametro de execucao do Athena -----------------------------------------------------------
def test_roster_sql_monta_consulta_com_parametros_de_execucao() -> None:
    documento = cpf_sintetico_valido()
    consulta, parametros = roster.montar_consulta(
        (_SIMULADOR / "roster.sql").read_text(encoding="utf-8"), documento
    )
    assert documento not in consulta and "--" not in consulta
    assert consulta.count("?") == 2 and parametros == [f"'{documento}'"] * 2
    assert "cpf = ?" in consulta and "p.cpf <> ?" in consulta


@pytest.mark.parametrize("numero", [1, 2])
def test_roster_lucas_v4_monta_cada_consulta_com_um_parametro(numero: int) -> None:
    documento = cpf_sintetico_valido()
    sql = (_SIMULADOR / "roster_lucas_v4.sql").read_text(encoding="utf-8")
    assert len(roster.consultas_do_sql(sql)) == 2
    consulta, parametros = roster.montar_consulta(sql, documento, numero=numero)
    assert consulta.startswith("WITH pessoa AS") and documento not in consulta
    assert parametros == [f"'{documento}'"]


def test_montar_consulta_recusa_documento_invalido_e_consulta_sem_parametro() -> None:
    with pytest.raises(roster.DocumentoDaPrimeiraLinhaError):
        roster.montar_consulta("SELECT 1 WHERE x = ?", "'; DROP TABLE t; --")
    with pytest.raises(ValueError, match="parametro"):
        roster.montar_consulta("SELECT 1", cpf_sintetico_valido())
    with pytest.raises(ValueError, match="consulta"):
        roster.montar_consulta("SELECT ?", cpf_sintetico_valido(), numero=2)


def test_linha_de_comando_do_roster(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    arquivo = str(_SIMULADOR / "roster.sql")
    monkeypatch.delenv(roster.VARIAVEL_DO_DOCUMENTO, raising=False)
    assert roster._principal(["parametros", arquivo]) == 2
    assert roster.VARIAVEL_DO_DOCUMENTO in capsys.readouterr().err
    invalido = _cpf_invalido()
    monkeypatch.setenv(roster.VARIAVEL_DO_DOCUMENTO, invalido)
    assert roster._principal(["consulta", arquivo]) == 2
    saida = capsys.readouterr()
    assert invalido not in saida.err + saida.out and saida.out == ""
    documento = cpf_sintetico_valido()
    monkeypatch.setenv(roster.VARIAVEL_DO_DOCUMENTO, documento)
    assert roster._principal(["parametros", arquivo]) == 0
    assert capsys.readouterr().out.split() == [f"'{documento}'"] * 2
    assert roster._principal(["consulta", arquivo]) == 0
    assert documento not in capsys.readouterr().out

"""`maezo.runtime.pacote_do_agente` (ADR-0067 fase 3): pacote imutavel por sha256, ponteiro por ambiente."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maezo.runtime import pacote_do_agente as pda


def _pacote(tmp_path: Path, conteudo: str = "a: 1\n", versao: str = "v1") -> tuple[bytes, pda.PacoteDoAgente]:
    d = tmp_path / versao
    (d / "sub").mkdir(parents=True)
    (d / "configuracao.yaml").write_text(conteudo, encoding="utf-8")
    (d / "sub" / "texto.md").write_text("olá\n", encoding="utf-8")
    return pda.montar("helena", versao, d)


def test_montar_e_ler_ida_e_volta_com_hash_dos_bytes(tmp_path: Path) -> None:
    conteudo, pacote = _pacote(tmp_path)
    assert set(pacote.arquivos) == {"configuracao.yaml", "sub/texto.md"}
    lido = pda.ler(conteudo, sha256_esperado=pacote.sha256, agente="helena")
    assert lido == pacote
    # canonico: montar de novo o mesmo diretorio da' o mesmo sha256
    assert pda.montar("helena", "v1", tmp_path / "v1")[1].sha256 == pacote.sha256


@pytest.mark.parametrize(
    ("mexer", "motivo"),
    [
        (lambda c, s: (c + b" ", s), "sha256"),
        (lambda c, s: (c, "0" * 64), "sha256"),
    ],
)
def test_ler_recusa_hash_que_nao_bate(tmp_path: Path, mexer, motivo: str) -> None:  # type: ignore[no-untyped-def]
    conteudo, pacote = _pacote(tmp_path)
    c, s = mexer(conteudo, pacote.sha256)
    with pytest.raises(pda.PacoteInvalidoError, match=motivo):
        pda.ler(c, sha256_esperado=s, agente="helena")


def test_ler_recusa_outro_agente_e_formato_desconhecido(tmp_path: Path) -> None:
    conteudo, pacote = _pacote(tmp_path)
    with pytest.raises(pda.PacoteInvalidoError, match="outro agente"):
        pda.ler(conteudo, sha256_esperado=pacote.sha256, agente="lucas")
    estranho = pda.canonico({"formato": "x", "agente": "helena", "arquivos": {}})
    import hashlib

    with pytest.raises(pda.PacoteInvalidoError, match="formato"):
        pda.ler(estranho, sha256_esperado=hashlib.sha256(estranho).hexdigest(), agente="helena")


class _S3Falso:
    def __init__(self) -> None:
        self.objetos: dict[tuple[str, str], bytes] = {}
        self.leituras = 0
        self.falhar = False

    def publicar(self, conteudo: bytes, pacote: pda.PacoteDoAgente, ambiente: str = "dev") -> None:
        self.objetos[("b", f"agentes/helena/pacotes/{pacote.sha256}.json")] = conteudo
        self.objetos[("b", f"agentes/helena/{ambiente}/ativo.json")] = json.dumps(
            {"sha256": pacote.sha256, "versao": pacote.versao}
        ).encode()

    def __call__(self, bucket: str, chave: str) -> bytes:
        self.leituras += 1
        if self.falhar:
            raise ConnectionError("s3 fora")
        return self.objetos[(bucket, chave)]


class _Relogio:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _carregador(s3: _S3Falso, relogio: _Relogio, validar=None) -> pda.CarregadorDePacote:  # type: ignore[no-untyped-def]
    return pda.CarregadorDePacote(
        "helena",
        uri="s3://b/agentes",
        ambiente="dev",
        ttl_s=60,
        validar=validar,
        ler_objeto=s3,
        relogio=relogio,
    )


def test_carregador_usa_o_ponteiro_guarda_por_ttl_e_troca_de_versao(tmp_path: Path) -> None:
    s3, relogio = _S3Falso(), _Relogio()
    c1, p1 = _pacote(tmp_path, "a: 1\n", "v1")
    s3.publicar(c1, p1)
    carregador = _carregador(s3, relogio)
    assert carregador.atual() == p1
    leituras = s3.leituras
    relogio.t = 30
    assert carregador.atual() == p1 and s3.leituras == leituras  # dentro do TTL: nem o ponteiro
    c2, p2 = _pacote(tmp_path, "a: 2\n", "v2")
    s3.publicar(c2, p2)
    relogio.t = 61
    assert carregador.atual() == p2  # passado o TTL: a versao nova, sem deploy


def test_falha_de_rede_ou_validacao_mantem_o_ultimo_pacote_bom(tmp_path: Path) -> None:
    s3, relogio = _S3Falso(), _Relogio()
    c1, p1 = _pacote(tmp_path, "a: 1\n", "v1")
    s3.publicar(c1, p1)

    def validar(pacote: pda.PacoteDoAgente) -> None:
        if "ruim" in pacote.arquivos["configuracao.yaml"]:
            raise pda.PacoteInvalidoError("recusado pelo agente")

    carregador = _carregador(s3, relogio, validar)
    assert carregador.atual() == p1
    s3.falhar, relogio.t = True, 61
    assert carregador.atual() == p1
    s3.falhar = False
    c2, p2 = _pacote(tmp_path, "ruim: 1\n", "v2")
    s3.publicar(c2, p2)
    relogio.t = 200
    assert carregador.atual() == p1


def test_sem_pacote_nenhum_devolve_none(tmp_path: Path) -> None:
    s3, relogio = _S3Falso(), _Relogio()
    s3.falhar = True
    assert _carregador(s3, relogio).atual() is None


def test_carregador_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pda.ENV_URI, raising=False)
    assert pda.carregador_do_ambiente("helena") is None
    monkeypatch.setenv(pda.ENV_URI, "s3://b/agentes")
    monkeypatch.delenv(pda.ENV_AMBIENTE, raising=False)
    with pytest.raises(ValueError, match=pda.ENV_AMBIENTE):
        pda.carregador_do_ambiente("helena")
    monkeypatch.setenv(pda.ENV_AMBIENTE, "dev")
    assert isinstance(pda.carregador_do_ambiente("helena"), pda.CarregadorDePacote)
    monkeypatch.setenv(pda.ENV_URI, "https://nao-e-s3")
    with pytest.raises(ValueError, match="s3://"):
        pda.carregador_do_ambiente("helena")

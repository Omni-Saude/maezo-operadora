"""O LEITOR do pacote K2 (HEL-3a, HEL-3c): contrato `rco-k2-v1` do rco, verificacao fail-closed, pin por
versao e hash, ratificacao, busca exata e o lock do consumidor.

A amostra em `helena_k2_amostra/pacote/` e' SINTETICA (ver `helena_k2_amostra/LEIAME.md`). Cada recusa copia a
amostra para `tmp_path`, adultera UMA coisa e, quando a adulteracao toca um arquivo listado, refaz o
manifesto, o `manifesto.sha256` e o pin para que a checagem sob teste seja a que recusa (e nao a do hash).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from maezo.agents.helena.habilidades.cobertura import manual as k2
from maezo.agents.helena.habilidades.cobertura.manual import (
    ManualK2,
    PacoteK2InvalidoError,
    PinDoK2,
    carregar,
    carregar_ou_nenhum,
)

_AMOSTRA = Path(__file__).parent / "helena_k2_amostra" / "pacote"
#: O pin da amostra, CONSTANTE (como o consumidor real pina): quem mexer na amostra recalcula aqui.
_PIN = PinDoK2(
    versao="amostra-0.1.0",
    sha256_manifesto="f9124f357352eaffa58cf06f3af7576209deb25f6d5dfe0cc1624115e49e15cd",
)
#: O aceite de nao ratificado dos testes: sai do PORTAO, como em dev (nunca construido direto).
_ACEITE = k2.aceite_de_nao_ratificado(ambiente="dev", configurado=True)


def _sha(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _copia(tmp_path: Path) -> Path:
    destino = tmp_path / "pacote"
    shutil.copytree(_AMOSTRA, destino)
    return destino


def _manifesto(pacote: Path) -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))
    return dados


def _gravar_manifesto(pacote: Path, manifesto: dict[str, Any]) -> PinDoK2:
    """Grava o manifesto CANONICO e o `manifesto.sha256`, e devolve o pin que casa com eles."""
    (pacote / "manifesto.json").write_bytes(k2.bytes_canonicos(manifesto))
    hex_ = _sha(pacote / "manifesto.json")
    (pacote / "manifesto.sha256").write_text(f"sha256:{hex_}\n", encoding="ascii")
    versao = manifesto.get("versao")
    return PinDoK2(versao=versao if isinstance(versao, str) else _PIN.versao, sha256_manifesto=hex_)


def _regravar_arquivo(pacote: Path, nome: str, conteudo: str) -> PinDoK2:
    """Troca um arquivo listado E atualiza o hash dele no manifesto (o pacote fica coerente, so' o conteudo
    esta' fora do formato)."""
    (pacote / nome).write_text(conteudo, encoding="utf-8")
    manifesto = _manifesto(pacote)
    manifesto["arquivos"][nome] = "sha256:" + _sha(pacote / nome)
    return _gravar_manifesto(pacote, manifesto)


def _json(pacote: Path, nome: str) -> Any:
    return json.loads((pacote / nome).read_text(encoding="utf-8"))


def _yaml(pacote: Path, nome: str) -> Any:
    return yaml.safe_load((pacote / nome).read_text(encoding="utf-8"))


def _regravar_json(pacote: Path, nome: str, dados: Any) -> PinDoK2:
    return _regravar_arquivo(pacote, nome, json.dumps(dados, ensure_ascii=False))


def _regravar_yaml(pacote: Path, nome: str, dados: Any) -> PinDoK2:
    return _regravar_arquivo(pacote, nome, yaml.safe_dump(dados, allow_unicode=True))


def _regravar_sinonimos(pacote: Path, sinonimos: Any) -> PinDoK2:
    """Troca o `sinonimos.json` E a copia dele no indice (a igualdade entre os dois nao e' a checagem)."""
    indice = _json(pacote, "search-index.json")
    indice["sinonimos"] = sinonimos
    _regravar_json(pacote, "search-index.json", indice)
    return _regravar_json(pacote, "sinonimos.json", sinonimos)


def _carregar(pacote: Path, pin: PinDoK2) -> ManualK2:
    return carregar(pacote, pin=pin, aceite=_ACEITE)


def _recusa(pacote: Path, pin: Any, *, aceite: Any = _ACEITE) -> str:
    with pytest.raises(PacoteK2InvalidoError) as exc:
        carregar(pacote, pin=pin, aceite=aceite)
    return exc.value.motivo


@pytest.fixture
def manual() -> ManualK2:
    return _carregar(_AMOSTRA, _PIN)


# --- Carregamento ok ----------------------------------------------------------------------------------------
def test_a_amostra_carrega(manual: ManualK2) -> None:
    assert manual.versao == "amostra-0.1.0"
    assert manual.commit_rco == "0" * 40
    assert manual.ratificado is False
    assert list(manual.regras) == sorted(manual.regras)
    assert len(manual.regras) == 5
    assert all(i.startswith("AMOSTRA-") for i in manual.regras)
    assert all(r.texto.startswith("Texto fictício") for r in manual.regras.values())
    assert [s.id for s in manual.capitulo_geral] == ["AMOSTRA-GERAL-01", "AMOSTRA-GERAL-02"]


def test_o_pacote_da_amostra_tem_exatamente_os_sete_arquivos() -> None:
    assert {p.name for p in _AMOSTRA.iterdir()} == k2.ENTRADAS_DO_DIRETORIO
    assert len(k2.ENTRADAS_DO_DIRETORIO) == 7


def test_o_manifesto_da_amostra_e_canonico_e_tem_os_sha256_reais() -> None:
    dados = (_AMOSTRA / "manifesto.json").read_bytes()
    assert dados == k2.bytes_canonicos(json.loads(dados))
    assert not dados.endswith(b"\n")
    arquivos = _manifesto(_AMOSTRA)["arquivos"]
    assert arquivos == {nome: "sha256:" + _sha(_AMOSTRA / nome) for nome in k2.ARQUIVOS_DO_PACOTE}
    assert _sha(_AMOSTRA / "manifesto.json") == _PIN.sha256_manifesto
    assert (_AMOSTRA / "manifesto.sha256").read_bytes() == f"sha256:{_PIN.sha256_manifesto}\n".encode()


def test_a_amostra_traz_fatos_verificaveis(manual: ManualK2) -> None:
    """O verificador de fatos e os evals dependem destes fatos no texto da amostra."""
    textos = " ".join(r.texto for r in manual.regras.values())
    assert "7 dias úteis" in textos
    assert "autorização prévia" in textos
    assert "20 sessões" in textos


def test_os_campos_novos_da_regra(manual: ManualK2) -> None:
    regra = manual.regra("AMOSTRA-AUT-001")
    assert regra is not None
    assert regra.sinonimos == ("exame de imagem", "ressonancia", "rm")
    assert (regra.versao, regra.dominio, regra.dominio_nome) == ("1", "autorizacao", "Autorização prévia")


def test_as_avaliacoes(manual: ManualK2) -> None:
    avaliacoes = {a.id: a for a in manual.avaliacoes}
    assert len(avaliacoes) == 4
    assert all(a.publico == "beneficiario" for a in avaliacoes.values())
    assert avaliacoes["AMOSTRA-AV-001"].regra_origem == ("AMOSTRA-AUT-001",)
    assert avaliacoes["AMOSTRA-AV-001"].comportamento is None
    assert {a.comportamento for a in avaliacoes.values()} == {None, *k2.COMPORTAMENTOS}
    assert avaliacoes["AMOSTRA-AV-003"].regra_origem == ()
    assert isinstance(avaliacoes["AMOSTRA-AV-002"].elementos_obrigatorios, tuple)


def test_as_estruturas_sao_imutaveis(manual: ManualK2) -> None:
    regra = manual.regra("AMOSTRA-AUT-001")
    assert regra is not None
    with pytest.raises(AttributeError):
        regra.texto = "outro"  # type: ignore[misc]
    with pytest.raises(TypeError):
        manual.regras["X"] = regra  # type: ignore[index]
    with pytest.raises(TypeError):
        manual.termos["x"] = ()  # type: ignore[index]
    with pytest.raises(AttributeError):
        manual.avaliacoes[0].pergunta = "outra"  # type: ignore[misc]
    assert isinstance(regra.sinonimos, tuple)


# --- regra(id) ----------------------------------------------------------------------------------------------
def test_regra_por_id(manual: ManualK2) -> None:
    regra = manual.regra("AMOSTRA-QTD-003")
    assert regra is not None
    assert regra.titulo == "Sessões de fisioterapia"
    assert regra.url.startswith("https://manual.exemplo.invalid/")
    assert manual.regra("AMOSTRA-QTD-999") is None
    assert manual.regra("amostra-qtd-003") is None  # id e' exato


# --- buscar -------------------------------------------------------------------------------------------------
def _ids(regras: tuple[k2.RegraDoManual, ...]) -> list[str]:
    return [r.id for r in regras]


def test_buscar_por_sinonimo_ignora_acento_e_maiusculas(manual: ManualK2) -> None:
    assert _ids(manual.buscar(["ressonância"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["RESSONANCIA"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["RM"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["Clínico  Geral"])) == ["AMOSTRA-PRZ-002"]


def test_buscar_por_token_do_titulo(manual: ManualK2) -> None:
    assert _ids(manual.buscar(["magnetica"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["negativa"])) == ["AMOSTRA-REC-005"]
    # token curto do titulo nao e' chave ("de", "a")
    assert manual.buscar(["de"]) == ()


def test_buscar_e_exato_sem_aproximacao(manual: ManualK2) -> None:
    assert manual.buscar(["ressonancia magnetica"]) == ()  # frase que nao e' sinonimo (e' termo tecnico)
    assert manual.buscar(["ressonanc"]) == ()  # prefixo
    assert manual.buscar(["fisioterapeuta"]) == ()  # parecido
    assert manual.buscar(["reembolsos"]) == ()  # plural
    assert manual.buscar(["imagem"]) == ()  # pedaco de sinonimo
    assert manual.buscar(["carencia"]) == ()  # termo sem regra
    assert manual.buscar(["", "   "]) == ()


def test_buscar_une_termos_em_ordem_de_id_sem_repetir(manual: ManualK2) -> None:
    # "exame de imagem" e' sinonimo de duas regras
    assert _ids(manual.buscar(["exame de imagem"])) == ["AMOSTRA-AUT-001", "AMOSTRA-REC-005"]
    assert _ids(manual.buscar(["reembolso", "fisio", "fisioterapia", "consulta"])) == [
        "AMOSTRA-PRZ-002",
        "AMOSTRA-QTD-003",
        "AMOSTRA-REE-004",
    ]


def test_sinonimos_por_termo(manual: ManualK2) -> None:
    mapa = manual.sinonimos_por_termo()
    assert mapa["exame de imagem"] == ("AMOSTRA-AUT-001", "AMOSTRA-REC-005")
    assert mapa["ressonancia"] == ("AMOSTRA-AUT-001",)
    assert mapa["devolucao do dinheiro"] == ("AMOSTRA-REE-004",)
    assert "magnetica" not in mapa  # so' sinonimos, nao o titulo
    assert "carencia" not in mapa  # termo publicado sem regra
    assert manual.termos["carencia"] == ()
    assert manual.termos_tecnicos["rm"] == ("ressonancia magnetica",)
    assert list(mapa) == sorted(mapa)


def test_sinonimos_por_termo_e_a_projecao_do_arquivo(manual: ManualK2) -> None:
    """O mapa vem do `sinonimos-por-termo.json` e casa com o `sinonimos` de cada regra."""
    arquivo = _json(_AMOSTRA, "sinonimos-por-termo.json")
    assert manual.sinonimos_por_termo() == {t: tuple(ids) for t, ids in arquivo.items() if ids}
    inverso: dict[str, set[str]] = {}
    for regra in manual.regras.values():
        for termo in regra.sinonimos:
            inverso.setdefault(termo, set()).add(regra.id)
    assert {t: tuple(sorted(i)) for t, i in inverso.items()} == manual.sinonimos_por_termo()


def test_normalizar() -> None:
    assert k2.normalizar("  Autorização   PRÉVIA! ") == "autorizacao previa"
    assert k2.normalizar("Sessão nº 2") == "sessao no 2"


# --- O pin --------------------------------------------------------------------------------------------------
def test_pin_com_outra_versao_recusa() -> None:
    pin = PinDoK2(versao="amostra-0.2.0", sha256_manifesto=_PIN.sha256_manifesto)
    assert _recusa(_AMOSTRA, pin) == "versao_diferente_do_pin"


def test_pin_com_outro_hash_recusa() -> None:
    pin = PinDoK2(versao=_PIN.versao, sha256_manifesto="f" * 64)
    assert _recusa(_AMOSTRA, pin) == "sha256_do_manifesto_diferente_do_pin"


def test_manifesto_mexido_sem_repinar_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["gerado_em"] = "2026-11-17T00:00:00Z"
    _gravar_manifesto(pacote, manifesto)
    assert _recusa(pacote, _PIN) == "sha256_do_manifesto_diferente_do_pin"


# --- Ratificacao --------------------------------------------------------------------------------------------
def test_o_padrao_recusa_pacote_nao_ratificado() -> None:
    with pytest.raises(PacoteK2InvalidoError, match="pacote_nao_ratificado"):
        carregar(_AMOSTRA, pin=_PIN)
    assert _recusa(_AMOSTRA, _PIN, aceite=None) == "pacote_nao_ratificado"


def test_pacote_ratificado_carrega_sem_aceite(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["ratificado"] = True
    manual = carregar(pacote, pin=_gravar_manifesto(pacote, manifesto))
    assert manual.ratificado is True


@pytest.mark.parametrize("valor", ["true", "false", 1, 0, None])
def test_ratificado_nao_booleano_recusa(tmp_path: Path, valor: object) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["ratificado"] = valor
    assert _recusa(pacote, _gravar_manifesto(pacote, manifesto)) == "ratificado_invalido"


@pytest.mark.parametrize(
    ("ambiente", "configurado", "aceita"),
    [
        ("dev", True, True),  # dev com a configuracao nomeada: aceita
        ("DEV ", True, True),
        ("dev", False, False),  # dev sem a configuracao: recusa
        ("prod", True, False),  # PRODUCAO com a configuracao ligada: recusa assim mesmo
        ("producao", True, False),
        ("production", True, False),
        ("staging", True, False),  # ambiente desconhecido: recusa
        ("", True, False),  # vazio: recusa
        ("  ", True, False),
    ],
)
def test_o_portao_do_aceite(ambiente: str, configurado: bool, aceita: bool) -> None:
    aceite = k2.aceite_de_nao_ratificado(ambiente=ambiente, configurado=configurado)
    assert (aceite is not None) is aceita
    if aceite is not None:
        assert isinstance(aceite, k2.AceiteDeNaoRatificado) and aceite.ambiente == "dev"


@pytest.mark.parametrize(
    ("ambiente", "configurado"),
    [(None, True), (1, True), ("dev", 1), ("dev", "true"), ("dev", None), (b"dev", True)],
)
def test_o_portao_com_tipo_errado_nao_da_aceite(ambiente: object, configurado: object) -> None:
    assert k2.aceite_de_nao_ratificado(ambiente=ambiente, configurado=configurado) is None


def test_producao_com_a_configuracao_ligada_recusa_o_pacote() -> None:
    """O teste negativo exigido pela ROOT: em producao nenhuma configuracao aceita o nao ratificado."""
    aceite = k2.aceite_de_nao_ratificado(ambiente="prod", configurado=True)
    assert aceite is None
    with pytest.raises(PacoteK2InvalidoError, match="pacote_nao_ratificado"):
        carregar(_AMOSTRA, pin=_PIN, aceite=aceite)
    with capture_logs() as logs:
        assert carregar_ou_nenhum(_AMOSTRA, pin=_PIN, aceite=aceite) is None
    assert [e["motivo"] for e in logs] == ["pacote_invalido"]


def test_dev_com_a_configuracao_carrega() -> None:
    aceite = k2.aceite_de_nao_ratificado(ambiente="dev", configurado=True)
    assert carregar(_AMOSTRA, pin=_PIN, aceite=aceite).versao == _PIN.versao


def test_o_aceite_nao_se_constroi_fora_do_portao() -> None:
    with pytest.raises(TypeError):
        k2.AceiteDeNaoRatificado(ambiente="prod", _selo=object())
    with pytest.raises(TypeError):
        k2.AceiteDeNaoRatificado("prod")  # type: ignore[call-arg]


@pytest.mark.parametrize("aceite", [True, 1, "dev", object()])
def test_aceite_que_nao_e_o_token_recusa(aceite: object) -> None:
    """O booleano antigo (ou qualquer imitacao) nao liga o aceite: e' recusado logo na entrada."""
    assert _recusa(_AMOSTRA, _PIN, aceite=aceite) == "aceite_invalido"


def test_os_nomes_da_configuracao() -> None:
    assert k2.CHAVE_ACEITAR_NAO_RATIFICADO == "cobertura_k2_aceitar_nao_ratificado"
    assert k2.VARIAVEL_DO_AMBIENTE == "MAEZO_ENV"
    assert "prod" not in k2.AMBIENTES_QUE_PODEM_ACEITAR


# --- O manifesto --------------------------------------------------------------------------------------------
def test_diretorio_ausente(tmp_path: Path) -> None:
    assert _recusa(tmp_path / "nao_existe", _PIN) == "diretorio_ausente"


def test_manifesto_ausente(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.json").unlink()
    assert _recusa(pacote, _PIN) == "manifesto_ausente"


def _manifesto_cru(pacote: Path, dados: bytes) -> PinDoK2:
    """Grava bytes quaisquer como manifesto e devolve o pin que CASA com eles (o hash passa; o resto nao)."""
    (pacote / "manifesto.json").write_bytes(dados)
    hex_ = _sha(pacote / "manifesto.json")
    (pacote / "manifesto.sha256").write_text(f"sha256:{hex_}\n", encoding="ascii")
    return PinDoK2(_PIN.versao, hex_)


@pytest.mark.parametrize(
    "dados",
    [
        b"{nao e json",
        b"[]",
        b"\xff\xfe",
        b'{"formato":"rco-k2-v1","formato":"rco-k2-v1"}',  # chave repetida
        b'{"versao":NaN}',
        b"\xef\xbb\xbf{}",  # BOM
        b"[" * 100_000 + b"]" * 100_000,  # estoura a pilha do parser
    ],
)
def test_manifesto_ilegivel(tmp_path: Path, dados: bytes) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _manifesto_cru(pacote, dados)) == "manifesto_ilegivel"


def test_o_pin_e_conferido_antes_de_interpretar_o_manifesto(tmp_path: Path) -> None:
    """Manifesto hostil (aninhamento fundo) com pin que NAO casa: recusa pelo hash, sem tocar no parser."""
    pacote = _copia(tmp_path)
    (pacote / "manifesto.json").write_bytes(b"[" * 100_000 + b"]" * 100_000)
    assert _recusa(pacote, _PIN) == "sha256_do_manifesto_diferente_do_pin"
    with capture_logs() as logs:
        assert carregar_ou_nenhum(pacote, pin=_PIN, aceite=_ACEITE) is None
    assert [e["motivo"] for e in logs] == ["pacote_invalido"]


@pytest.mark.parametrize(
    "pin",
    [
        PinDoK2(_PIN.versao, _PIN.sha256_manifesto.upper()),  # hex EXATO: maiusculo e' recusado
        PinDoK2(_PIN.versao, "sha256:" + _PIN.sha256_manifesto),
        PinDoK2(_PIN.versao, _PIN.sha256_manifesto[:-1]),
        PinDoK2(_PIN.versao, None),  # type: ignore[arg-type]
        PinDoK2(None, _PIN.sha256_manifesto),  # type: ignore[arg-type]
        PinDoK2("", _PIN.sha256_manifesto),
        PinDoK2(_PIN.versao, _PIN.sha256_manifesto, commit_rco="abc"),
        PinDoK2(_PIN.versao, _PIN.sha256_manifesto, commit_rco="A" * 40),
        "nao e pin",
        None,
    ],
)
def test_pin_invalido(pin: object) -> None:
    assert _recusa(_AMOSTRA, pin) == "pin_invalido"


def test_commit_do_pin_conferido_com_o_manifesto() -> None:
    assert _carregar(_AMOSTRA, PinDoK2(_PIN.versao, _PIN.sha256_manifesto, commit_rco="0" * 40)).versao
    pin = PinDoK2(_PIN.versao, _PIN.sha256_manifesto, commit_rco="1" * 40)
    assert _recusa(_AMOSTRA, pin) == "commit_rco_diferente_do_pin"


@pytest.mark.parametrize("diretorio", [str(_AMOSTRA), None, 7])
def test_diretorio_de_tipo_errado(diretorio: object) -> None:
    assert _recusa(diretorio, _PIN) == "diretorio_invalido"  # type: ignore[arg-type]


def test_diretorio_que_e_link_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(pacote, target_is_directory=True)
    assert _recusa(link, _PIN) == "diretorio_nao_regular"


_SERIALIZACOES_NAO_CANONICAS: list[Callable[[dict[str, Any]], str]] = [
    lambda m: json.dumps(m, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
    lambda m: json.dumps(m, sort_keys=True, ensure_ascii=False, indent=2),
    lambda m: json.dumps(m, ensure_ascii=False, separators=(",", ":")),  # chaves fora de ordem
    lambda m: json.dumps(m, sort_keys=True, separators=(",", ":")).replace("rco-k2-v1", "rco-k2-v\\u0031"),
]


@pytest.mark.parametrize("serializar", _SERIALIZACOES_NAO_CANONICAS)
def test_manifesto_nao_canonico_recusa(tmp_path: Path, serializar: Callable[[dict[str, Any]], str]) -> None:
    pacote = _copia(tmp_path)
    manifesto = dict(reversed(list(_manifesto(pacote).items())))
    (pacote / "manifesto.json").write_text(serializar(manifesto), encoding="utf-8")
    hex_ = _sha(pacote / "manifesto.json")
    (pacote / "manifesto.sha256").write_text(f"sha256:{hex_}\n", encoding="ascii")
    assert _recusa(pacote, PinDoK2(_PIN.versao, hex_)) == "manifesto_nao_canonico"


def test_arquivo_grande_demais(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k2, "TAMANHO_MAXIMO", 64)
    assert _recusa(_copia(tmp_path), _PIN) == "arquivo_grande_demais"


def test_manifesto_sha256_ausente(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.sha256").unlink()
    assert _recusa(pacote, _PIN) == "manifesto_sha256_ausente"


@pytest.mark.parametrize(
    "conteudo",
    [
        f"sha256:{_PIN.sha256_manifesto}",  # sem a quebra de linha
        f"sha256:{_PIN.sha256_manifesto}\n\n",
        f"sha256:{_PIN.sha256_manifesto}\r\n",
        f" sha256:{_PIN.sha256_manifesto}\n",
        f"{_PIN.sha256_manifesto}\n",  # hex cru
        f"sha256:{_PIN.sha256_manifesto.upper()}\n",
        f"sha256:{_PIN.sha256_manifesto}  manifesto.json\n",
        "",
    ],
)
def test_manifesto_sha256_fora_do_formato(tmp_path: Path, conteudo: str) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.sha256").write_bytes(conteudo.encode("ascii"))
    assert _recusa(pacote, _PIN) == "manifesto_sha256_fora_do_formato"


def test_manifesto_sha256_diferente(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.sha256").write_text("sha256:" + "a" * 64 + "\n", encoding="ascii")
    assert _recusa(pacote, _PIN) == "manifesto_sha256_diferente"


_AJUSTES_DO_MANIFESTO: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("formato_diferente", lambda m: m.update(formato="rco-k2-v2")),
    ("formato_diferente", lambda m: m.pop("formato")),
    ("manifesto_com_campo_desconhecido", lambda m: m.update(extra=1)),
    ("manifesto_com_campo_desconhecido", lambda m: m.update(_amostra="anotacao")),  # sem tolerancia a `_`
    ("manifesto_sem_campo_obrigatorio", lambda m: m.pop("gerado_em")),
    ("manifesto_sem_campo_obrigatorio", lambda m: m.pop("ratificado")),
    ("commit_rco_invalido", lambda m: m.update(commit_rco="abc123")),
    ("commit_rco_invalido", lambda m: m.update(commit_rco="G" * 40)),
    ("commit_rco_invalido", lambda m: m.update(commit_rco="A" * 40)),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="ontem")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15T00:00:00")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15T00:00:00-03:00")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15T00:00:00+00:00")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15T00:00:00.123Z")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-02-30T00:00:00Z")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em=20261115)),
    ("lista_de_arquivos_invalida", lambda m: m["arquivos"].pop("avaliacoes.yaml")),
    ("lista_de_arquivos_invalida", lambda m: m["arquivos"].update({"outro.json": "sha256:" + "0" * 64})),
    (
        "lista_de_arquivos_invalida",
        lambda m: m["arquivos"].update({"manifesto.sha256": "sha256:" + "0" * 64}),
    ),
    ("lista_de_arquivos_invalida", lambda m: m["arquivos"].update({"search-index.json": "xyz"})),
    # o hex cru, sem a notacao `sha256:`, e' recusado
    ("lista_de_arquivos_invalida", lambda m: m["arquivos"].update({"search-index.json": "0" * 64})),
    ("lista_de_arquivos_invalida", lambda m: m.update(arquivos=[])),
]


@pytest.mark.parametrize(("motivo", "ajuste"), _AJUSTES_DO_MANIFESTO)
def test_manifesto_fora_do_formato(
    tmp_path: Path, motivo: str, ajuste: Callable[[dict[str, Any]], None]
) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    ajuste(manifesto)
    assert _recusa(pacote, _gravar_manifesto(pacote, manifesto)) == motivo


# --- O diretorio --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("nome", sorted(k2.ARQUIVOS_DO_PACOTE))
def test_arquivo_listado_ausente(tmp_path: Path, nome: str) -> None:
    pacote = _copia(tmp_path)
    (pacote / nome).unlink()
    assert _recusa(pacote, _PIN) == "arquivo_listado_ausente"


@pytest.mark.parametrize("nome", sorted(k2.ARQUIVOS_DO_PACOTE))
def test_arquivo_listado_com_sha256_diferente(tmp_path: Path, nome: str) -> None:
    pacote = _copia(tmp_path)
    with (pacote / nome).open("a", encoding="utf-8") as f:
        f.write(" ")
    assert _recusa(pacote, _PIN) == "sha256_de_arquivo_diferente"


@pytest.mark.parametrize("intruso", ["LEIAME.md", ".oculto", "search-index.json.bak", "manifesto.json.sig"])
def test_arquivo_nao_listado_recusa(tmp_path: Path, intruso: str) -> None:
    pacote = _copia(tmp_path)
    (pacote / intruso).write_text("x", encoding="utf-8")
    assert _recusa(pacote, _PIN) == "arquivo_nao_listado_no_manifesto"


def test_subdiretorio_nao_listado_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "extra").mkdir()
    assert _recusa(pacote, _PIN) == "arquivo_nao_listado_no_manifesto"


@pytest.mark.parametrize("nome", ["capitulo-geral.yaml", "manifesto.sha256", "manifesto.json"])
def test_arquivo_como_link_recusa(tmp_path: Path, nome: str) -> None:
    pacote = _copia(tmp_path)
    original = tmp_path / f"fora-{nome}"
    shutil.move(pacote / nome, original)
    (pacote / nome).symlink_to(original)
    assert _recusa(pacote, _PIN) == "arquivo_nao_regular"


# --- search-index.json --------------------------------------------------------------------------------------
def test_indice_json_invalido(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_arquivo(pacote, "search-index.json", "[{")) == "indice_json_invalido"


_AJUSTES_DO_INDICE: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("indice_com_campo_desconhecido", lambda i: i.update(extra=1)),
    ("indice_com_campo_desconhecido", lambda i: i.update(_origem="anotacao")),
    ("indice_sem_campo_obrigatorio", lambda i: i.pop("data_versao")),
    ("indice_sem_campo_obrigatorio", lambda i: i.pop("sinonimos")),
    ("indice_de_outro_publico", lambda i: i.update(publico="prestador")),
    ("versao_do_indice_diferente_do_manifesto", lambda i: i.update(versao_manual="amostra-0.2.0")),
    ("indice_fora_do_formato", lambda i: i.update(data_versao="")),
    ("sinonimos_do_indice_diferentes", lambda i: i["sinonimos"].update(nova=["x"])),
    ("sinonimos_do_indice_diferentes", lambda i: i["sinonimos"].pop("rm")),
    ("indice_fora_do_formato", lambda i: i.update(documentos={})),
    ("indice_sem_regras", lambda i: i.update(documentos=[])),
]


@pytest.mark.parametrize(("motivo", "ajuste"), _AJUSTES_DO_INDICE)
def test_indice_fora_do_formato(
    tmp_path: Path, motivo: str, ajuste: Callable[[dict[str, Any]], None]
) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    ajuste(indice)
    assert _recusa(pacote, _regravar_json(pacote, "search-index.json", indice)) == motivo


def test_indice_que_nao_e_objeto(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    documentos = _json(pacote, "search-index.json")["documentos"]
    assert (
        _recusa(pacote, _regravar_json(pacote, "search-index.json", documentos)) == "indice_fora_do_formato"
    )


def _sem(campo: str) -> Callable[[dict[str, Any]], None]:
    return lambda r: r.pop(campo)


_CAMPOS_DA_REGRA = (
    "id", "titulo", "versao", "publico", "publico_nome", "dominio", "dominio_nome", "resumo", "url", "texto",
    "sinonimos", "t_titulo", "t_chaves", "t_texto",
)  # fmt: skip
_AJUSTES_DA_REGRA: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    *[("regra_sem_campo_obrigatorio", _sem(campo)) for campo in _CAMPOS_DA_REGRA],
    ("regra_com_campo_desconhecido", lambda r: r.update(extra="x")),
    ("regra_com_campo_desconhecido", lambda r: r.update(_origem="anotacao")),  # sem tolerancia a `_`
    ("regra_de_outro_publico", lambda r: r.update(publico="prestador")),
    ("regra_sem_id", lambda r: r.update(id="")),
    ("regra_sem_id", lambda r: r.update(id=7)),
    ("regra_sem_texto", lambda r: r.update(texto="  ")),
    ("regra_sem_texto", lambda r: r.update(texto=None)),
    ("regra_sem_resumo", lambda r: r.update(resumo="")),
    ("regra_fora_do_formato", lambda r: r.update(sinonimos="fisioterapia")),
    ("regra_fora_do_formato", lambda r: r.update(sinonimos=["fisio", 3])),
    ("regra_fora_do_formato", lambda r: r.update(sinonimos=list(reversed(r["sinonimos"])))),  # fora de ordem
    ("regra_fora_do_formato", lambda r: r.update(sinonimos=[*r["sinonimos"], r["sinonimos"][-1]])),
    ("regra_fora_do_formato", lambda r: r.update(url="")),
    ("regra_fora_do_formato", lambda r: r.update(titulo=["x"])),
    ("regra_fora_do_formato", lambda r: r.update(versao="")),
    ("regra_fora_do_formato", lambda r: r.update(dominio=" ")),
    ("regra_fora_do_formato", lambda r: r.update(dominio_nome=None)),
    ("regra_fora_do_formato", lambda r: r.update(publico_nome="")),
    ("regra_fora_do_formato", lambda r: r.update(t_titulo=1)),
    ("regra_fora_do_formato", lambda r: r.update(t_chaves=r["sinonimos"])),  # t_chaves e' texto, nao lista
    ("regra_fora_do_formato", lambda r: r.update(t_texto=None)),
]


@pytest.mark.parametrize(("motivo", "ajuste"), _AJUSTES_DA_REGRA)
def test_regra_fora_do_formato(tmp_path: Path, motivo: str, ajuste: Callable[[dict[str, Any]], None]) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    ajuste(indice["documentos"][2])
    assert _recusa(pacote, _regravar_json(pacote, "search-index.json", indice)) == motivo


def test_t_campos_vazios_sao_aceitos(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    indice["documentos"][2].update(t_titulo="", t_chaves="", t_texto="")
    assert _carregar(pacote, _regravar_json(pacote, "search-index.json", indice)).regra("AMOSTRA-QTD-003")


def test_regra_que_nao_e_objeto(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    indice["documentos"].append("solta")
    assert _recusa(pacote, _regravar_json(pacote, "search-index.json", indice)) == "regra_fora_do_formato"


def test_id_de_regra_duplicado(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    indice["documentos"][1]["id"] = indice["documentos"][0]["id"]
    assert _recusa(pacote, _regravar_json(pacote, "search-index.json", indice)) == "id_de_regra_duplicado"


# --- sinonimos.json e sinonimos-por-termo.json --------------------------------------------------------------
def test_sinonimos_json_invalido(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_arquivo(pacote, "sinonimos.json", "{")) == "sinonimos_json_invalido"


@pytest.mark.parametrize(
    "sinonimos",
    [[], {"rm": "ressonancia magnetica"}, {"rm": [1]}, {"rm": [""]}, {"": ["x"]}, {"rm": ["a", "a"]}],
)
def test_sinonimos_fora_do_formato(tmp_path: Path, sinonimos: Any) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_sinonimos(pacote, sinonimos)) == "sinonimos_fora_do_formato"


def test_sinonimos_por_termo_json_invalido(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    pin = _regravar_arquivo(pacote, "sinonimos-por-termo.json", "[")
    assert _recusa(pacote, pin) == "sinonimos_por_termo_json_invalido"


@pytest.mark.parametrize("por_termo", [[], {"rm": "AMOSTRA-AUT-001"}, {"rm": [7]}])
def test_sinonimos_por_termo_fora_do_formato(tmp_path: Path, por_termo: Any) -> None:
    pacote = _copia(tmp_path)
    pin = _regravar_json(pacote, "sinonimos-por-termo.json", por_termo)
    assert _recusa(pacote, pin) == "sinonimos_por_termo_fora_do_formato"


@pytest.mark.parametrize("ajuste", [lambda p: p.pop("carencia"), lambda p: p.update(nova=[])])
def test_sinonimos_por_termo_com_chaves_diferentes(
    tmp_path: Path, ajuste: Callable[[dict[str, Any]], None]
) -> None:
    pacote = _copia(tmp_path)
    por_termo = _json(pacote, "sinonimos-por-termo.json")
    ajuste(por_termo)
    pin = _regravar_json(pacote, "sinonimos-por-termo.json", por_termo)
    assert _recusa(pacote, pin) == "sinonimos_por_termo_com_chaves_diferentes"


def test_sinonimos_por_termo_com_regra_desconhecida(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    por_termo = _json(pacote, "sinonimos-por-termo.json")
    por_termo["carencia"] = ["AMOSTRA-XXX-999"]
    pin = _regravar_json(pacote, "sinonimos-por-termo.json", por_termo)
    assert _recusa(pacote, pin) == "sinonimos_por_termo_com_regra_desconhecida"


@pytest.mark.parametrize(
    ("termo", "ids"),
    [
        ("carencia", ["AMOSTRA-AUT-001"]),  # o termo ganhou uma regra que nao o lista
        ("rm", []),  # a regra lista um termo que nao aponta para ela
        ("rm", ["AMOSTRA-PRZ-002"]),
    ],
)
def test_sinonimos_da_regra_inconsistentes(tmp_path: Path, termo: str, ids: list[str]) -> None:
    pacote = _copia(tmp_path)
    por_termo = _json(pacote, "sinonimos-por-termo.json")
    por_termo[termo] = ids
    pin = _regravar_json(pacote, "sinonimos-por-termo.json", por_termo)
    assert _recusa(pacote, pin) == "sinonimos_da_regra_inconsistentes"


# --- capitulo-geral.yaml ------------------------------------------------------------------------------------
_CAPITULOS_INVALIDOS: list[tuple[str, str]] = [
    ("capitulo_geral_yaml_invalido", "secoes: [\n  - id: x\n    titulo: : :"),
    ("capitulo_geral_fora_do_formato", "- 1\n- 2\n"),
    ("capitulo_geral_fora_do_formato", "secoes: []\n"),
    ("capitulo_geral_com_campo_desconhecido", "outra: 1\n"),
    ("capitulo_geral_com_campo_desconhecido", "_nota: 1\n"),  # sem tolerancia a `_`
    ("capitulo_geral_sem_campo_obrigatorio", "{}\n"),
    ("capitulo_geral_com_campo_desconhecido", "secoes: [{id: a, titulo: b, texto: c}]\nextra: 1\n"),
    ("secao_fora_do_formato", "secoes: [solta]\n"),
    ("secao_sem_campo_obrigatorio", "secoes: [{id: a, titulo: b}]\n"),
    ("secao_com_campo_desconhecido", "secoes: [{id: a, titulo: b, texto: c, d: e}]\n"),
    ("secao_sem_texto", "secoes: [{id: a, titulo: b, texto: ''}]\n"),
    ("secao_fora_do_formato", "secoes: [{id: 2026-11-15, titulo: b, texto: c}]\n"),
    ("id_de_secao_duplicado", "secoes: [{id: a, titulo: b, texto: c}, {id: a, titulo: d, texto: e}]\n"),
]


@pytest.mark.parametrize(("motivo", "conteudo"), _CAPITULOS_INVALIDOS)
def test_capitulo_geral_fora_do_formato(tmp_path: Path, motivo: str, conteudo: str) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_arquivo(pacote, "capitulo-geral.yaml", conteudo)) == motivo


# --- avaliacoes.yaml ----------------------------------------------------------------------------------------
_RAIZES_DE_AVALIACOES_INVALIDAS: list[tuple[str, str]] = [
    ("avaliacoes_yaml_invalido", "avaliacoes: [\n  - id: x\n    pergunta: : :"),
    ("avaliacoes_fora_do_formato", "- 1\n"),
    ("avaliacoes_sem_campo_obrigatorio", "{}\n"),
    ("avaliacoes_com_campo_desconhecido", "avaliacoes: []\nextra: 1\n"),
    ("avaliacoes_fora_do_formato", "avaliacoes: {}\n"),
    ("avaliacao_fora_do_formato", "avaliacoes: [solta]\n"),
]


@pytest.mark.parametrize(("motivo", "conteudo"), _RAIZES_DE_AVALIACOES_INVALIDAS)
def test_avaliacoes_fora_do_formato(tmp_path: Path, motivo: str, conteudo: str) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_arquivo(pacote, "avaliacoes.yaml", conteudo)) == motivo


_AJUSTES_DA_AVALIACAO: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("avaliacao_com_campo_desconhecido", lambda a: a.update(extra="x")),
    ("avaliacao_sem_campo_obrigatorio", lambda a: a.pop("resposta_esperada")),
    ("avaliacao_sem_campo_obrigatorio", lambda a: a.pop("regra_origem")),
    ("avaliacao_de_outro_publico", lambda a: a.update(publico="prestador")),
    ("avaliacao_com_comportamento_invalido", lambda a: a.update(comportamento="inventa")),
    ("avaliacao_com_comportamento_invalido", lambda a: a.update(comportamento=None)),
    ("avaliacao_com_comportamento_invalido", lambda a: a.update(comportamento=["corrige_premissa"])),
    ("avaliacao_com_regra_desconhecida", lambda a: a.update(regra_origem=["AMOSTRA-XXX-999"])),
    ("avaliacao_fora_do_formato", lambda a: a.update(regra_origem="AMOSTRA-AUT-001")),
    ("avaliacao_fora_do_formato", lambda a: a.update(elementos_obrigatorios="x")),
    ("avaliacao_fora_do_formato", lambda a: a.update(o_que_seria_invencao=[""])),
    ("avaliacao_fora_do_formato", lambda a: a.update(pergunta="")),
    ("avaliacao_fora_do_formato", lambda a: a.update(id=3)),
]


@pytest.mark.parametrize(("motivo", "ajuste"), _AJUSTES_DA_AVALIACAO)
def test_avaliacao_fora_do_formato(
    tmp_path: Path, motivo: str, ajuste: Callable[[dict[str, Any]], None]
) -> None:
    pacote = _copia(tmp_path)
    avaliacoes = _yaml(pacote, "avaliacoes.yaml")
    ajuste(avaliacoes["avaliacoes"][0])
    assert _recusa(pacote, _regravar_yaml(pacote, "avaliacoes.yaml", avaliacoes)) == motivo


def test_id_de_avaliacao_duplicado(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    avaliacoes = _yaml(pacote, "avaliacoes.yaml")
    avaliacoes["avaliacoes"][1]["id"] = avaliacoes["avaliacoes"][0]["id"]
    pin = _regravar_yaml(pacote, "avaliacoes.yaml", avaliacoes)
    assert _recusa(pacote, pin) == "id_de_avaliacao_duplicado"


def test_lista_de_avaliacoes_vazia_e_aceita(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    assert (
        _carregar(pacote, _regravar_arquivo(pacote, "avaliacoes.yaml", "avaliacoes: []\n")).avaliacoes == ()
    )


def test_motivo_nunca_carrega_conteudo(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    indice["documentos"][0]["texto"] = ""
    pin = _regravar_json(pacote, "search-index.json", indice)
    with pytest.raises(PacoteK2InvalidoError) as exc:
        _carregar(pacote, pin)
    assert str(exc.value) == exc.value.motivo == "regra_sem_texto"
    assert "AMOSTRA" not in str(exc.value)


# --- carregar_ou_nenhum -------------------------------------------------------------------------------------
def test_carregar_ou_nenhum_ok() -> None:
    manual = carregar_ou_nenhum(_AMOSTRA, pin=_PIN, aceite=_ACEITE)
    assert manual is not None and manual.versao == _PIN.versao


@pytest.mark.parametrize(
    ("diretorio", "pin", "motivo"),
    [(None, _PIN, "sem_diretorio"), (_AMOSTRA, None, "sem_pin"), (None, None, "sem_diretorio")],
)
def test_carregar_ou_nenhum_sem_configuracao(
    diretorio: Path | None, pin: PinDoK2 | None, motivo: str
) -> None:
    with capture_logs() as logs:
        assert carregar_ou_nenhum(diretorio, pin=pin, aceite=_ACEITE) is None
    assert logs == [{"event": "helena_k2_recusado", "motivo": motivo, "log_level": "warning"}]


def test_carregar_ou_nenhum_pacote_invalido_loga_so_o_motivo() -> None:
    # o padrao (sem aceite) recusa a amostra nao ratificada
    with capture_logs() as logs:
        assert carregar_ou_nenhum(_AMOSTRA, pin=_PIN) is None
    assert logs == [
        {
            "event": "helena_k2_recusado",
            "motivo": "pacote_invalido",
            "erro": "PacoteK2InvalidoError",
            "log_level": "warning",
        }
    ]
    with pytest.raises(PacoteK2InvalidoError, match="pacote_nao_ratificado"):
        carregar(_AMOSTRA, pin=_PIN)  # o motivo exato e' o de `carregar`


def test_carregar_ou_nenhum_erro_de_leitura(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha(_caminho: Path) -> int:
        raise PermissionError("sem permissao")

    monkeypatch.setattr(k2, "_abrir", _falha)
    with capture_logs() as logs:
        assert carregar_ou_nenhum(_AMOSTRA, pin=_PIN, aceite=_ACEITE) is None
    assert logs == [
        {
            "event": "helena_k2_recusado",
            "motivo": "erro_de_leitura",
            "erro": "PermissionError",
            "log_level": "warning",
        }
    ]


# --- pin_do_lock --------------------------------------------------------------------------------------------
_DIGEST_DO_PIN = f"sha256:{_PIN.sha256_manifesto}"


def _lock(**ajustes: Any) -> dict[str, Any]:
    lock: dict[str, Any] = {
        "lock_format": {"name": "rco-k2-lock", "version": "1.0.0"},
        "estado": "fixado",
        "provenance": {"rco_repo": "exemplo/rco", "rco_commit_sha": "0" * 40},
        "manifest_pin": {"path": "gerado/manual/k2/manifesto.json", "sha256": _DIGEST_DO_PIN},
        "versao": _PIN.versao,
    }
    lock.update(ajustes)
    return lock


def _gravar_lock(tmp_path: Path, lock: Any) -> Path:
    caminho = tmp_path / "k2.lock.json"
    caminho.write_text(json.dumps(lock), encoding="utf-8")
    return caminho


def test_pin_do_lock_ausente(tmp_path: Path) -> None:
    assert k2.pin_do_lock(tmp_path / "k2.lock.json") is None


def test_pin_do_lock_sem_pacote(tmp_path: Path) -> None:
    sem_pacote = {"lock_format": {"name": "rco-k2-lock", "version": "1.0.0"}, "estado": "sem_pacote"}
    assert k2.pin_do_lock(_gravar_lock(tmp_path, sem_pacote)) is None
    assert k2.pin_do_lock(_gravar_lock(tmp_path, _lock(estado="sem_pacote"))) is None


def test_pin_do_lock_fixado_tira_o_prefixo_e_carrega_a_amostra(tmp_path: Path) -> None:
    pin = k2.pin_do_lock(_gravar_lock(tmp_path, _lock()))
    assert pin == PinDoK2(_PIN.versao, _PIN.sha256_manifesto, commit_rco="0" * 40)
    assert _carregar(_AMOSTRA, pin).versao == _PIN.versao


_LOCKS_INVALIDOS: list[tuple[str, Any]] = [
    ("lock_fora_do_formato", []),
    ("lock_fora_do_formato", _lock(extra=1)),
    ("lock_formato_diferente", _lock(lock_format={"name": "rco-k2-lock", "version": "2.0.0"})),
    ("lock_formato_diferente", _lock(lock_format={"name": "outro", "version": "1.0.0"})),
    ("lock_formato_diferente", {"estado": "sem_pacote"}),
    ("lock_estado_invalido", _lock(estado="pendente")),
    ("lock_estado_invalido", _lock(estado=None)),
    ("lock_fora_do_formato", {k: v for k, v in _lock().items() if k != "versao"}),
    ("lock_fora_do_formato", _lock(versao="")),
    ("lock_fora_do_formato", _lock(provenance={"rco_repo": "exemplo/rco", "rco_commit_sha": "abc"})),
    ("lock_fora_do_formato", _lock(provenance={"rco_repo": "", "rco_commit_sha": "0" * 40})),
    ("lock_fora_do_formato", _lock(provenance={"rco_commit_sha": "0" * 40})),
    ("lock_fora_do_formato", _lock(manifest_pin={"path": "m.json", "sha256": _PIN.sha256_manifesto})),
    ("lock_fora_do_formato", _lock(manifest_pin={"path": "m.json", "sha256": "sha256:" + "A" * 64})),
    ("lock_fora_do_formato", _lock(manifest_pin={"sha256": _DIGEST_DO_PIN})),
    ("lock_fora_do_formato", _lock(manifest_pin=_DIGEST_DO_PIN)),
]


@pytest.mark.parametrize(("motivo", "lock"), _LOCKS_INVALIDOS)
def test_pin_do_lock_malformado(tmp_path: Path, motivo: str, lock: Any) -> None:
    with pytest.raises(PacoteK2InvalidoError) as exc:
        k2.pin_do_lock(_gravar_lock(tmp_path, lock))
    assert exc.value.motivo == motivo


def test_pin_do_lock_ilegivel_e_link(tmp_path: Path) -> None:
    caminho = tmp_path / "k2.lock.json"
    caminho.write_text("{", encoding="utf-8")
    with pytest.raises(PacoteK2InvalidoError, match="lock_ilegivel"):
        k2.pin_do_lock(caminho)
    link = tmp_path / "link.lock.json"
    link.symlink_to(_gravar_lock(tmp_path, _lock()))
    with pytest.raises(PacoteK2InvalidoError, match="arquivo_nao_regular"):
        k2.pin_do_lock(link)


# --- Revisao independente (HEL-3c): leitura estrita, limites e entradas -------------------------------------
def test_resumo_no_limite_do_contrato(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _json(pacote, "search-index.json")
    indice["documentos"][2]["resumo"] = "a" * k2.RESUMO_MAXIMO
    assert _carregar(pacote, _regravar_json(pacote, "search-index.json", indice)).regra("AMOSTRA-QTD-003")
    indice["documentos"][2]["resumo"] = "a" * (k2.RESUMO_MAXIMO + 1)
    pin = _regravar_json(pacote, "search-index.json", indice)
    assert _recusa(pacote, pin) == "regra_com_resumo_longo_demais"


@pytest.mark.parametrize(
    ("nome", "motivo"),
    [
        ("search-index.json", "indice_json_invalido"),
        ("sinonimos.json", "sinonimos_json_invalido"),
        ("sinonimos-por-termo.json", "sinonimos_por_termo_json_invalido"),
    ],
)
def test_json_de_dados_com_chave_repetida_recusa(tmp_path: Path, nome: str, motivo: str) -> None:
    pacote = _copia(tmp_path)
    texto = (pacote / nome).read_text(encoding="utf-8")
    chave, valor = next(iter(json.loads(texto).items()))
    repetido = (
        "{" + json.dumps(chave) + ":" + json.dumps(valor, ensure_ascii=False) + "," + texto.lstrip()[1:]
    )
    assert _recusa(pacote, _regravar_arquivo(pacote, nome, repetido)) == motivo


@pytest.mark.parametrize(
    ("nome", "motivo"),
    [
        ("search-index.json", "indice_json_invalido"),
        ("sinonimos.json", "sinonimos_json_invalido"),
        ("capitulo-geral.yaml", "capitulo_geral_yaml_invalido"),
        ("avaliacoes.yaml", "avaliacoes_yaml_invalido"),
    ],
)
def test_bom_utf8_recusa_em_json_e_yaml(tmp_path: Path, nome: str, motivo: str) -> None:
    pacote = _copia(tmp_path)
    conteudo = "﻿" + (pacote / nome).read_text(encoding="utf-8")
    assert _recusa(pacote, _regravar_arquivo(pacote, nome, conteudo)) == motivo


@pytest.mark.parametrize("constante", ["NaN", "Infinity", "-Infinity"])
def test_json_com_constante_nao_finita_recusa(tmp_path: Path, constante: str) -> None:
    pacote = _copia(tmp_path)
    pin = _regravar_arquivo(pacote, "sinonimos-por-termo.json", '{"rm": ' + constante + "}")
    assert _recusa(pacote, pin) == "sinonimos_por_termo_json_invalido"


@pytest.mark.parametrize(
    ("nome", "motivo"),
    [
        ("sinonimos.json", "sinonimos_json_invalido"),
        ("search-index.json", "indice_json_invalido"),
        ("sinonimos-por-termo.json", "sinonimos_por_termo_json_invalido"),
    ],
)
def test_json_de_dados_fundo_demais_recusa(tmp_path: Path, nome: str, motivo: str) -> None:
    pacote = _copia(tmp_path)
    pin = _regravar_arquivo(pacote, nome, "[" * 100_000 + "]" * 100_000)
    assert _recusa(pacote, pin) == motivo
    with capture_logs():
        assert carregar_ou_nenhum(pacote, pin=pin, aceite=_ACEITE) is None


_YAMLS_PROIBIDOS: list[str] = [
    "secoes: []\nsecoes: [{id: a, titulo: b, texto: c}]\n",  # chave repetida na raiz
    "secoes: [{id: a, id: b, titulo: c, texto: d}]\n",  # chave repetida dentro
    "secoes:\n- &s {id: a, titulo: b, texto: c}\n",  # ancora
    "base: &b {titulo: b, texto: c}\nsecoes: [*b]\n",  # alias
    "secoes: [{<<: {titulo: b, texto: c}, id: a}]\n",  # chave de merge
    "secoes: [{? [a, b] : c}]\n",  # chave nao hashable
    "secoes: " + "[" * 100_000 + "]" * 100_000 + "\n",  # estoura a pilha do parser
    "secoes:\n- {id: !!python/name:os.system , titulo: b, texto: c}\n",
]


@pytest.mark.parametrize("conteudo", _YAMLS_PROIBIDOS)
def test_yaml_estrito_recusa(tmp_path: Path, conteudo: str) -> None:
    pacote = _copia(tmp_path)
    pin = _regravar_arquivo(pacote, "capitulo-geral.yaml", conteudo)
    assert _recusa(pacote, pin) == "capitulo_geral_yaml_invalido"
    with capture_logs():
        assert carregar_ou_nenhum(pacote, pin=pin, aceite=_ACEITE) is None


def test_yaml_bilhao_de_risadas_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    linhas = ["a: &a [x, x, x, x, x, x, x, x, x]"]
    for i in range(8):
        anterior, atual = chr(97 + i), chr(98 + i)
        linhas.append(f"{atual}: &{atual} [" + ", ".join([f"*{anterior}"] * 9) + "]")
    pin = _regravar_arquivo(pacote, "avaliacoes.yaml", "\n".join([*linhas, "avaliacoes: []", ""]))
    assert _recusa(pacote, pin) == "avaliacoes_yaml_invalido"


@pytest.mark.parametrize(
    "gerado_em", ["２０２６-11-16T00:00:00Z", "٢٠٢٦-١١-١٦T00:00:00Z", "2026-11-16T00:00:0٠Z"]
)
def test_gerado_em_com_digito_nao_ascii_recusa(tmp_path: Path, gerado_em: str) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["gerado_em"] = gerado_em
    assert _recusa(pacote, _gravar_manifesto(pacote, manifesto)) == "gerado_em_invalido"


def test_estrutura_funda_que_escape_vira_motivo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _funda(*_args: object) -> ManualK2:
        raise RecursionError

    monkeypatch.setattr(k2, "_carregar", _funda)
    assert _recusa(_AMOSTRA, _PIN) == "estrutura_funda_demais"


def test_carregar_ou_nenhum_nunca_levanta(monkeypatch: pytest.MonkeyPatch) -> None:
    def _inesperado(*_args: object) -> ManualK2:
        raise ValueError("conteudo que nao pode ir ao log")

    monkeypatch.setattr(k2, "_carregar", _inesperado)
    with capture_logs() as logs:
        assert carregar_ou_nenhum(_AMOSTRA, pin=_PIN, aceite=_ACEITE) is None
    assert logs == [
        {
            "event": "helena_k2_recusado",
            "motivo": "erro_inesperado",
            "erro": "ValueError",
            "log_level": "warning",
        }
    ]


@pytest.mark.parametrize(
    ("diretorio", "pin"),
    [(str(_AMOSTRA), _PIN), (_AMOSTRA, PinDoK2(_PIN.versao, None)), (_AMOSTRA, "pin")],  # type: ignore[arg-type]
)
def test_carregar_ou_nenhum_com_tipo_errado_devolve_nenhum(diretorio: Any, pin: Any) -> None:
    with capture_logs() as logs:
        assert carregar_ou_nenhum(diretorio, pin=pin, aceite=_ACEITE) is None
    assert [e["motivo"] for e in logs] == ["pacote_invalido"]


def test_diretorio_alterado_durante_a_leitura_recusa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pacote = _copia(tmp_path)
    ler = k2._ler

    def _ler_e_plantar(caminho: Path, *, ausente: str) -> bytes:
        dados = ler(caminho, ausente=ausente)
        if caminho.name == "search-index.json":
            (pacote / "plantado.json").write_text("{}", encoding="utf-8")
        return dados

    monkeypatch.setattr(k2, "_ler", _ler_e_plantar)
    assert _recusa(pacote, _PIN) == "diretorio_mudou_durante_a_leitura"


def test_fifo_no_lugar_de_arquivo_recusa_sem_travar(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "sinonimos.json").unlink()
    os.mkfifo(pacote / "sinonimos.json")
    assert _recusa(pacote, _PIN) == "arquivo_nao_regular"


def test_manifesto_sha256_que_e_diretorio_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.sha256").unlink()
    (pacote / "manifesto.sha256").mkdir()
    assert _recusa(pacote, _PIN) == "arquivo_nao_regular"


def test_leitura_limitada_mesmo_se_o_arquivo_crescer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O `fstat` diz que cabe (o arquivo 'cresceu' depois); a leitura para em `TAMANHO_MAXIMO + 1` bytes."""
    pacote = _copia(tmp_path)
    fstat = os.fstat
    lidos: list[int] = []

    def _fstat_pequeno(descritor: int) -> os.stat_result:
        real = fstat(descritor)
        return os.stat_result((real.st_mode, *list(real)[1:6], 0, *list(real)[7:10]))

    class _Espiao:
        def __init__(self, arquivo: Any) -> None:
            self._arquivo = arquivo

        def __enter__(self) -> _Espiao:
            return self

        def __exit__(self, *_exc: object) -> None:
            self._arquivo.close()

        def fileno(self) -> int:
            return int(self._arquivo.fileno())

        def read(self, tamanho: int) -> bytes:
            lidos.append(tamanho)
            dados: bytes = self._arquivo.read(tamanho)
            return dados

    fdopen = os.fdopen
    monkeypatch.setattr(k2, "TAMANHO_MAXIMO", 64)
    monkeypatch.setattr(k2.os, "fstat", _fstat_pequeno)
    monkeypatch.setattr(k2.os, "fdopen", lambda d, modo: _Espiao(fdopen(d, modo)))
    assert _recusa(pacote, _PIN) == "arquivo_grande_demais"
    assert lidos == [65]


# --- pin_do_lock: leitura estrita ---------------------------------------------------------------------------
def test_pin_do_lock_com_chave_repetida_recusa(tmp_path: Path) -> None:
    caminho = tmp_path / "k2.lock.json"
    texto = json.dumps(_lock())
    caminho.write_text('{"versao": "outra", ' + texto[1:], encoding="utf-8")
    with pytest.raises(PacoteK2InvalidoError, match="lock_ilegivel"):
        k2.pin_do_lock(caminho)


def test_pin_do_lock_fundo_demais_recusa(tmp_path: Path) -> None:
    caminho = tmp_path / "k2.lock.json"
    caminho.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(PacoteK2InvalidoError, match="lock_ilegivel"):
        k2.pin_do_lock(caminho)


def test_pin_do_lock_com_caminho_de_tipo_errado_recusa(tmp_path: Path) -> None:
    with pytest.raises(PacoteK2InvalidoError, match="lock_caminho_invalido"):
        k2.pin_do_lock(str(tmp_path / "k2.lock.json"))  # type: ignore[arg-type]


def test_lock_com_outro_commit_recusa_o_pacote(tmp_path: Path) -> None:
    lock = _lock(provenance={"rco_repo": "exemplo/rco", "rco_commit_sha": "1" * 40})
    pin = k2.pin_do_lock(_gravar_lock(tmp_path, lock))
    assert pin is not None
    assert _recusa(_AMOSTRA, pin) == "commit_rco_diferente_do_pin"

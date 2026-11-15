"""O LEITOR do pacote K2 (HEL-3a): verificacao fail-closed, pin por versao e hash, busca exata.

A amostra em `helena_k2_amostra/pacote/` e' SINTETICA (ver `helena_k2_amostra/LEIAME.md`). Cada recusa copia a
amostra para `tmp_path`, adultera UMA coisa e, quando a adulteracao toca o manifesto, refaz o pin para que a
checagem sob teste seja a que recusa (e nao a do hash).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
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
    sha256_manifesto="9af9c38159fc23b4b73e576bab19afbb87cb7562839906a67ff660320e6dd22a",
)


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
    """Grava o manifesto e devolve o pin que casa com ele (versao do manifesto, se for texto)."""
    (pacote / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    versao = manifesto.get("versao")
    return PinDoK2(
        versao=versao if isinstance(versao, str) else _PIN.versao,
        sha256_manifesto=_sha(pacote / "manifesto.json"),
    )


def _regravar_arquivo(pacote: Path, nome: str, conteudo: str) -> PinDoK2:
    """Troca um arquivo listado E atualiza o hash dele no manifesto (o pacote fica coerente, so' o conteudo
    esta' fora do formato)."""
    (pacote / nome).write_text(conteudo, encoding="utf-8")
    manifesto = _manifesto(pacote)
    manifesto["arquivos"][nome] = "sha256:" + _sha(pacote / nome)
    return _gravar_manifesto(pacote, manifesto)


def _indice(pacote: Path) -> list[dict[str, Any]]:
    dados: list[dict[str, Any]] = json.loads((pacote / "search-index.json").read_text(encoding="utf-8"))
    return dados


def _recusa(pacote: Path, pin: PinDoK2) -> str:
    with pytest.raises(PacoteK2InvalidoError) as exc:
        carregar(pacote, pin=pin)
    return exc.value.motivo


@pytest.fixture
def manual() -> ManualK2:
    return carregar(_AMOSTRA, pin=_PIN)


# --- Carregamento ok ---------------------------------------------------------------------------------------
def test_a_amostra_carrega(manual: ManualK2) -> None:
    assert manual.versao == "amostra-0.1.0"
    assert manual.commit_rco == "0" * 40
    assert list(manual.regras) == sorted(manual.regras)
    assert len(manual.regras) == 5
    assert all(i.startswith("AMOSTRA-") for i in manual.regras)
    assert [s.id for s in manual.capitulo_geral] == ["AMOSTRA-GERAL-01", "AMOSTRA-GERAL-02"]


def test_o_manifesto_da_amostra_tem_os_sha256_reais() -> None:
    arquivos = _manifesto(_AMOSTRA)["arquivos"]
    assert arquivos == {nome: "sha256:" + _sha(_AMOSTRA / nome) for nome in k2.ARQUIVOS_DO_PACOTE}
    assert _sha(_AMOSTRA / "manifesto.json") == _PIN.sha256_manifesto


def test_a_amostra_traz_fatos_verificaveis(manual: ManualK2) -> None:
    """A unidade seguinte (verificador de fatos) depende destes fatos no texto da amostra."""
    textos = " ".join(r.texto for r in manual.regras.values())
    assert "7 dias úteis" in textos
    assert "autorização prévia" in textos
    assert "20 sessões" in textos


def test_as_estruturas_sao_imutaveis(manual: ManualK2) -> None:
    regra = manual.regra("AMOSTRA-AUT-001")
    assert regra is not None
    with pytest.raises(AttributeError):
        regra.texto = "outro"  # type: ignore[misc]
    with pytest.raises(TypeError):
        manual.regras["X"] = regra  # type: ignore[index]
    assert isinstance(regra.sinonimos, tuple)


# --- regra(id) ---------------------------------------------------------------------------------------------
def test_regra_por_id(manual: ManualK2) -> None:
    regra = manual.regra("AMOSTRA-QTD-003")
    assert regra is not None
    assert regra.titulo == "Sessões de fisioterapia"
    assert regra.url.startswith("https://manual.exemplo.invalid/")
    assert manual.regra("AMOSTRA-QTD-999") is None
    assert manual.regra("amostra-qtd-003") is None  # id e' exato


# --- buscar ------------------------------------------------------------------------------------------------
def _ids(regras: tuple[k2.RegraDoManual, ...]) -> list[str]:
    return [r.id for r in regras]


def test_buscar_por_sinonimo_ignora_acento_e_maiusculas(manual: ManualK2) -> None:
    assert _ids(manual.buscar(["ressonância"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["RESSONANCIA"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["rm"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["Clinico  Geral"])) == ["AMOSTRA-PRZ-002"]


def test_buscar_por_token_do_titulo(manual: ManualK2) -> None:
    assert _ids(manual.buscar(["magnetica"])) == ["AMOSTRA-AUT-001"]
    assert _ids(manual.buscar(["negativa"])) == ["AMOSTRA-REC-005"]
    # token curto do titulo nao e' chave ("de", "a")
    assert manual.buscar(["de"]) == ()


def test_buscar_e_exato_sem_aproximacao(manual: ManualK2) -> None:
    assert manual.buscar(["ressonancia magnetica"]) == ()  # frase que nao e' sinonimo
    assert manual.buscar(["ressonanc"]) == ()  # prefixo
    assert manual.buscar(["fisioterapeuta"]) == ()  # parecido
    assert manual.buscar(["reembolsos"]) == ()  # plural
    assert manual.buscar(["imagem"]) == ()  # pedaco de sinonimo
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
    assert list(mapa) == sorted(mapa)


def test_normalizar() -> None:
    assert k2.normalizar("  Autorização   PRÉVIA! ") == "autorizacao previa"
    assert k2.normalizar("Sessão nº 2") == "sessao no 2"


# --- O pin -------------------------------------------------------------------------------------------------
def test_pin_com_outra_versao_recusa() -> None:
    pin = PinDoK2(versao="amostra-0.2.0", sha256_manifesto=_PIN.sha256_manifesto)
    assert _recusa(_AMOSTRA, pin) == "versao_diferente_do_pin"


def test_pin_com_outro_hash_recusa() -> None:
    pin = PinDoK2(versao=_PIN.versao, sha256_manifesto="f" * 64)
    assert _recusa(_AMOSTRA, pin) == "sha256_do_manifesto_diferente_do_pin"


def test_manifesto_mexido_sem_repinar_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["_amostra"] = "outra anotacao"  # ate' uma anotacao muda os bytes pinados
    _gravar_manifesto(pacote, manifesto)
    assert _recusa(pacote, _PIN) == "sha256_do_manifesto_diferente_do_pin"


# --- Cada motivo de recusa ---------------------------------------------------------------------------------
def test_diretorio_ausente(tmp_path: Path) -> None:
    assert _recusa(tmp_path / "nao_existe", _PIN) == "diretorio_ausente"


def test_manifesto_ausente(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.json").unlink()
    assert _recusa(pacote, _PIN) == "manifesto_ausente"


def test_manifesto_ilegivel(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "manifesto.json").write_text("{nao e json", encoding="utf-8")
    assert _recusa(pacote, _PIN) == "manifesto_ilegivel"
    (pacote / "manifesto.json").write_text("[]", encoding="utf-8")
    assert _recusa(pacote, _PIN) == "manifesto_ilegivel"


def test_arquivo_grande_demais(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k2, "TAMANHO_MAXIMO", 64)
    assert _recusa(_copia(tmp_path), _PIN) == "arquivo_grande_demais"


_AJUSTES_DO_MANIFESTO: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("formato_diferente", lambda m: m.update(formato="rco-k2-v2")),
    ("formato_diferente", lambda m: m.pop("formato")),
    ("pacote_nao_ratificado", lambda m: m.update(ratificado=False)),
    ("pacote_nao_ratificado", lambda m: m.update(ratificado="true")),
    ("pacote_nao_ratificado", lambda m: m.pop("ratificado")),
    ("manifesto_com_campo_desconhecido", lambda m: m.update(extra=1)),
    ("manifesto_sem_campo_obrigatorio", lambda m: m.pop("gerado_em")),
    ("commit_rco_invalido", lambda m: m.update(commit_rco="abc123")),
    ("commit_rco_invalido", lambda m: m.update(commit_rco="G" * 40)),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="ontem")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15T00:00:00")),
    ("gerado_em_invalido", lambda m: m.update(gerado_em="2026-11-15T00:00:00-03:00")),
    ("lista_de_arquivos_invalida", lambda m: m["arquivos"].pop("capitulo-geral.yaml")),
    ("lista_de_arquivos_invalida", lambda m: m["arquivos"].update({"outro.json": "sha256:" + "0" * 64})),
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


def test_gerado_em_com_offset_zero_explicito_aceita(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["gerado_em"] = "2026-11-15T00:00:00+00:00"
    assert carregar(pacote, pin=_gravar_manifesto(pacote, manifesto)).versao == _PIN.versao


def test_arquivo_listado_ausente(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "capitulo-geral.yaml").unlink()
    assert _recusa(pacote, _PIN) == "arquivo_listado_ausente"


def test_arquivo_listado_com_sha256_diferente(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    with (pacote / "search-index.json").open("a", encoding="utf-8") as f:
        f.write(" ")
    assert _recusa(pacote, _PIN) == "sha256_de_arquivo_diferente"


@pytest.mark.parametrize("intruso", ["LEIAME.md", ".oculto", "search-index.json.bak"])
def test_arquivo_nao_listado_recusa(tmp_path: Path, intruso: str) -> None:
    pacote = _copia(tmp_path)
    (pacote / intruso).write_text("x", encoding="utf-8")
    assert _recusa(pacote, _PIN) == "arquivo_nao_listado_no_manifesto"


def test_subdiretorio_nao_listado_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    (pacote / "extra").mkdir()
    assert _recusa(pacote, _PIN) == "arquivo_nao_listado_no_manifesto"


def test_arquivo_listado_como_link_recusa(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    original = tmp_path / "fora.yaml"
    shutil.move(pacote / "capitulo-geral.yaml", original)
    (pacote / "capitulo-geral.yaml").symlink_to(original)
    assert _recusa(pacote, _PIN) == "arquivo_nao_regular"


def test_indice_json_invalido(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_arquivo(pacote, "search-index.json", "[{")) == "indice_json_invalido"


def test_indice_fora_do_formato_e_vazio(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    assert _recusa(pacote, _regravar_arquivo(pacote, "search-index.json", "{}")) == "indice_fora_do_formato"
    assert _recusa(pacote, _regravar_arquivo(pacote, "search-index.json", "[]")) == "indice_sem_regras"


_AJUSTES_DA_REGRA: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("regra_sem_campo_obrigatorio", lambda r: r.pop("resumo")),
    ("regra_sem_campo_obrigatorio", lambda r: r.pop("sinonimos")),
    ("regra_com_campo_desconhecido", lambda r: r.update(extra="x")),
    ("regra_sem_id", lambda r: r.update(id="")),
    ("regra_sem_id", lambda r: r.update(id=7)),
    ("regra_sem_texto", lambda r: r.update(texto="  ")),
    ("regra_sem_texto", lambda r: r.update(texto=None)),
    ("regra_sem_resumo", lambda r: r.update(resumo="")),
    ("regra_fora_do_formato", lambda r: r.update(sinonimos="ressonancia")),
    ("regra_fora_do_formato", lambda r: r.update(sinonimos=["ok", 3])),
    ("regra_fora_do_formato", lambda r: r.update(url="")),
    ("regra_fora_do_formato", lambda r: r.update(titulo=["x"])),
]


@pytest.mark.parametrize(("motivo", "ajuste"), _AJUSTES_DA_REGRA)
def test_regra_fora_do_formato(tmp_path: Path, motivo: str, ajuste: Callable[[dict[str, Any]], None]) -> None:
    pacote = _copia(tmp_path)
    indice = _indice(pacote)
    ajuste(indice[2])
    assert _recusa(pacote, _regravar_arquivo(pacote, "search-index.json", json.dumps(indice))) == motivo


def test_regra_que_nao_e_objeto(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice: list[Any] = [*_indice(pacote), "solta"]
    pin = _regravar_arquivo(pacote, "search-index.json", json.dumps(indice))
    assert _recusa(pacote, pin) == "regra_fora_do_formato"


def test_id_de_regra_duplicado(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _indice(pacote)
    indice[1]["id"] = indice[0]["id"]
    pin = _regravar_arquivo(pacote, "search-index.json", json.dumps(indice))
    assert _recusa(pacote, pin) == "id_de_regra_duplicado"


def test_chave_com_sublinhado_e_ignorada(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _indice(pacote)
    indice[0]["_origem"] = "anotacao do publicador"
    pin = _regravar_arquivo(pacote, "search-index.json", json.dumps(indice))
    assert carregar(pacote, pin=pin).regra(indice[0]["id"]) is not None


_CAPITULOS_INVALIDOS: list[tuple[str, str]] = [
    ("capitulo_geral_yaml_invalido", "secoes: [\n  - id: x\n    titulo: : :"),
    ("capitulo_geral_fora_do_formato", "- 1\n- 2\n"),
    ("capitulo_geral_fora_do_formato", "secoes: []\n"),
    ("capitulo_geral_com_campo_desconhecido", "outra: 1\n"),
    ("capitulo_geral_fora_do_formato", "_nota: 1\n"),
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


def test_motivo_nunca_carrega_conteudo(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    indice = _indice(pacote)
    indice[0]["texto"] = ""
    pin = _regravar_arquivo(pacote, "search-index.json", json.dumps(indice))
    with pytest.raises(PacoteK2InvalidoError) as exc:
        carregar(pacote, pin=pin)
    assert str(exc.value) == exc.value.motivo == "regra_sem_texto"
    assert "AMOSTRA" not in str(exc.value)


# --- carregar_ou_nenhum ------------------------------------------------------------------------------------
def test_carregar_ou_nenhum_ok() -> None:
    manual = carregar_ou_nenhum(_AMOSTRA, pin=_PIN)
    assert manual is not None and manual.versao == _PIN.versao


@pytest.mark.parametrize(
    ("diretorio", "pin", "motivo"),
    [(None, _PIN, "sem_diretorio"), (_AMOSTRA, None, "sem_pin"), (None, None, "sem_diretorio")],
)
def test_carregar_ou_nenhum_sem_configuracao(
    diretorio: Path | None, pin: PinDoK2 | None, motivo: str
) -> None:
    with capture_logs() as logs:
        assert carregar_ou_nenhum(diretorio, pin=pin) is None
    assert logs == [{"event": "helena_k2_recusado", "motivo": motivo, "log_level": "warning"}]


def test_carregar_ou_nenhum_pacote_invalido_loga_so_o_motivo(tmp_path: Path) -> None:
    pacote = _copia(tmp_path)
    manifesto = _manifesto(pacote)
    manifesto["ratificado"] = False
    pin = _gravar_manifesto(pacote, manifesto)
    with capture_logs() as logs:
        assert carregar_ou_nenhum(pacote, pin=pin) is None
    assert logs == [
        {
            "event": "helena_k2_recusado",
            "motivo": "pacote_invalido",
            "erro": "PacoteK2InvalidoError",
            "log_level": "warning",
        }
    ]
    with pytest.raises(PacoteK2InvalidoError, match="pacote_nao_ratificado"):
        carregar(pacote, pin=pin)  # o motivo exato e' o de `carregar`


def test_carregar_ou_nenhum_erro_de_leitura(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha(_self: Path) -> bytes:
        raise PermissionError("sem permissao")

    monkeypatch.setattr(Path, "read_bytes", _falha)
    with capture_logs() as logs:
        assert carregar_ou_nenhum(_AMOSTRA, pin=_PIN) is None
    assert logs == [
        {
            "event": "helena_k2_recusado",
            "motivo": "erro_de_leitura",
            "erro": "PermissionError",
            "log_level": "warning",
        }
    ]

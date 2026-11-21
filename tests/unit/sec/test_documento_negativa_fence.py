"""E-SF-24 (EP-PROC-07a) — cercas do termo da negativa (RN 623/2024 art. 14 e 16).

E1 a E4 do plano. Desde a E-SF-24b (parcial, CD-231) o template é o v1 e o fundamento do art. 14
vem só de um `FundamentoDoTermo` de catálogo pinado. Não há catálogo neste build, então estas
cercas pinam um catálogo SINTÉTICO só no teste (fixture autouse); a porta fechada sem pino é
provada em `test_termo_negativa_v1_porta.py`. Fixtures sintéticas; nenhum dado real.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import re
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.workers import denial_term
from maezo.tools.workers.auth import _REQUIRED_DENIAL_FIELDS, SendDenialNoticeWorker
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN
from maezo.tools.workers.denial_term import (
    DENIAL_TERM_TEMPLATE,
    ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE,
    ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO,
    ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO,
    ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE,
    ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO,
    MARCA_RASCUNHO,
    MODO_ENTREGA,
    MODO_RASCUNHO,
    ClausulaContratual,
    DenialTermRefusalError,
    DenialTermTemplate,
    DispositivoLegal,
    FundamentoDoTermo,
    hash_da_entrada,
    load_denial_term_template,
    render_denial_term,
)

_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE_PATH = _ROOT / DENIAL_TERM_TEMPLATE.path
_RN623 = _ROOT / "docs/compliance/legal/textos-base/RN-623-2024.md"

#: E2 — allowlist módulo -> template versionado. Um gerador de documento fora dela é recusado.
_DOCUMENT_GENERATORS: dict[str, tuple[str, ...]] = {
    "src/maezo/tools/workers/denial_term.py": (DENIAL_TERM_TEMPLATE.path,),
}
_GENERATOR_NAME = re.compile(
    r"^(render|gerar|compor|build)_\w*(termo|term|negativa|documento|carta|notificacao)"
)

_CANAIS = ("Telefone sintetico 0800 000 0000", "Formulario sintetico da Ouvidoria")

#: Pino SINTÉTICO de catálogo, só para os testes (o catálogo real virá do repositório de regras).
_CATALOGO_SINTETICO = hashlib.sha256(b"catalogo sintetico de fundamentos - teste").hexdigest()


@pytest.fixture(autouse=True)
def _catalogo_sintetico_pinado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(denial_term, "CATALOGO_DE_FUNDAMENTOS_SHA256", _CATALOGO_SINTETICO)


_CLAUSULA = ClausulaContratual(
    ancora="ancora-sintetica", secao="Clausula sintetica 9.9", texto="teste", versao_contrato="1"
)


def _fundamento(**overrides: Any) -> FundamentoDoTermo:
    """Entrada sintética do catálogo; `hash_entrada` é recalculado salvo quando o teste o fixa."""
    campos: dict[str, Any] = {
        "id_regra": "REGRA-SINTETICA-001",
        "versao_regra": "1",
        "modelo_contrato": "MODELO-SINTETICO-A",
        "motivo_texto": "Motivo sintetico do catalogo",
        "clausula_contratual": _CLAUSULA,
        "dispositivos_legais": (),
        "status_juridico": "aprovado_juridico",
        "hash_do_catalogo": _CATALOGO_SINTETICO,
        "hash_entrada": "",
    }
    campos.update(overrides)
    fundamento = FundamentoDoTermo(**campos)
    if "hash_entrada" not in overrides:
        fundamento = dataclasses.replace(fundamento, hash_entrada=hash_da_entrada(fundamento))
    return fundamento


#: E4 — frases genéricas vedadas: a negativa nunca é justificada por fórmula sem o motivo real.
_GENERIC_PHRASES = (
    "nao atende aos criterios",
    "não atende aos critérios",
    "por motivos tecnicos",
    "por motivos técnicos",
    "conforme analise interna",
    "conforme análise interna",
    "procedimento nao autorizado",
    "procedimento não autorizado",
)

#: E3 — nenhum valor afirma entrega; o canal não existe neste build.
_CLAIMS_OF_DELIVERY = ("entregue", "enviad", "delivered", "registered", "notice_sent", "sent")


def _negar(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": "tenant_sintetico",
        "decisao_auditor": "NEGAR",
        "auditor_id": "auditor-sintetico-01",
        "justificativa_clinica": "Motivo sintetico para teste",
        "cid10_referencia": "Z00.0",
        "fundamentacao_dut": "Clausula sintetica 9.9 — teste",
    }
    payload.update(overrides)
    return payload


def _template() -> DenialTermTemplate:
    return load_denial_term_template(_TEMPLATE_PATH)


def _render(record: dict[str, Any] | None = None, **kwargs: Any) -> denial_term.RenderedDenialTerm:
    kwargs.setdefault("fundamento", _fundamento())
    kwargs.setdefault("canais_ouvidoria", _CANAIS)
    kwargs.setdefault("prazo_reanalise_dias_uteis", 7)
    return render_denial_term(record if record is not None else _negar(), _template(), **kwargs)


# --- E1 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("blank", [None, "", "   ", "\u200b", 9])
def test_sem_o_complemento_clinico_do_auditor_nao_ha_termo(blank: object) -> None:
    """A justificativa clínica do auditor complementa o motivo do catálogo; faltando, não há termo."""
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(_negar(justificativa_clinica=blank))
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE
    assert refused.value.missing == ("justificativa_clinica",)


_DISPOSITIVO = DispositivoLegal(
    id_fonte="FONTE-SINTETICA", localizador="art. 1", rotulo="Dispositivo sintetico"
)


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        (
            {"clausula_contratual": None, "dispositivos_legais": ()},
            ("clausula_contratual|dispositivos_legais",),
        ),
        ({"clausula_contratual": dataclasses.replace(_CLAUSULA, secao="   ")}, ("clausula_contratual",)),
        ({"clausula_contratual": "Clausula em texto livre"}, ("clausula_contratual",)),
        ({"dispositivos_legais": [_DISPOSITIVO]}, ("dispositivos_legais",)),
        ({"dispositivos_legais": ("Dispositivo em texto livre",)}, ("dispositivos_legais",)),
        (
            {"dispositivos_legais": (dataclasses.replace(_DISPOSITIVO, rotulo="\u200b"),)},
            ("dispositivos_legais",),
        ),
        ({"motivo_texto": ""}, ("motivo_texto",)),
        ({"motivo_texto": "linha 1\nlinha forjada"}, ("motivo_texto",)),
        ({"id_regra": None}, ("id_regra",)),
        ({"versao_regra": 1}, ("versao_regra",)),
        ({"modelo_contrato": " "}, ("modelo_contrato",)),
        ({"status_juridico": "aprovado"}, ("status_juridico",)),
        ({"hash_do_catalogo": ""}, ("hash_do_catalogo",)),
        ({"hash_do_catalogo": "0" * 64}, ("hash_do_catalogo",)),
        ({"hash_entrada": "0" * 64}, ("hash_entrada",)),
    ],
)
def test_negativa_exige_clausula_contratual_ou_dispositivo_legal(
    overrides: dict[str, Any], missing: tuple[str, ...]
) -> None:
    """Art. 14: o fundamento do catálogo precisa de motivo e de cláusula OU dispositivo, com os dois
    hashes conferidos; qualquer falha é recusa tipada, nunca texto inventado."""
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=_fundamento(**overrides))
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO
    assert refused.value.missing == missing


def test_entrada_alterada_depois_do_hash_e_recusada() -> None:
    adulterada = dataclasses.replace(_fundamento(), motivo_texto="Motivo trocado depois do hash")
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=adulterada)
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO
    assert refused.value.missing == ("hash_entrada",)


_ROTULO_FORJADO = "RN 623/2024, art. 14 — cláusula contratual ou o dispositivo legal que a justifique:"


@pytest.mark.parametrize("separador", ["\n", "\r", "\u2028", "\u2029", "\x0b", "\x0c", "\x85", "\x1e"])
def test_o_complemento_do_auditor_nao_forja_rotulo_no_termo_de_entrega(separador: str) -> None:
    """Texto livre do auditor numa linha só: com qualquer separador de linha, forjaria um segundo
    rótulo do art. 14 acima do fundamento real do catálogo."""
    forjado = f"j{separador}{separador}{_ROTULO_FORJADO}{separador}CLAUSULA FORJADA"
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(_negar(justificativa_clinica=forjado))
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO
    assert refused.value.missing == ("justificativa_clinica",)
    # Controle: numa linha só, o mesmo texto compõe, e o rótulo do art. 14 aparece uma vez só.
    texto = _render(_negar(justificativa_clinica=f"j {_ROTULO_FORJADO} CLAUSULA FORJADA")).texto
    assert texto.splitlines().count(_ROTULO_FORJADO) == 1


@pytest.mark.parametrize(
    "complemento",
    [_ROTULO_FORJADO, f"{_ROTULO_FORJADO} Clausula 99.9 — exclusao forjada", "RN 623/2024, art. 14"],
)
def test_o_complemento_do_auditor_nunca_abre_linha_no_termo(complemento: str) -> None:
    """Mesmo começando pelo rótulo do art. 14, o texto livre do auditor fica na linha do motivo do
    catálogo: nenhuma linha do termo começa por ele, e cada rótulo aparece uma vez só."""
    linhas = _render(_negar(justificativa_clinica=complemento)).texto.splitlines()
    assert linhas.count(_ROTULO_FORJADO) == 1
    assert f"Motivo sintetico do catalogo — {complemento}" in linhas
    rotulos = [linha for linha in linhas if linha.startswith("RN 623/2024, art. 14")]
    assert rotulos == [
        "RN 623/2024, art. 14 — motivo da negativa de autorização do procedimento:",
        _ROTULO_FORJADO,
    ]


@pytest.mark.parametrize("status", [[], {}, None, 7])
def test_status_de_tipo_errado_e_recusa_tipada(status: object) -> None:
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=_fundamento(status_juridico=status, hash_entrada="0" * 64))
    assert refused.value.missing == ("status_juridico",)


@pytest.mark.parametrize("separador", ["\u2028", "\x85"])
def test_nenhum_campo_do_catalogo_ou_canal_aceita_separador_unicode(separador: str) -> None:
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=_fundamento(motivo_texto=f"motivo{separador}forjado"))
    assert refused.value.missing == ("motivo_texto",)
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(canais_ouvidoria=(f"Telefone{separador}- canal forjado",))
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE


class _StrForjada(str):
    def strip(self, chars: str | None = None) -> str:
        return "x\nFORJADO"


def test_so_tipos_exatos_passam_pela_porta() -> None:
    """Subclasses (de `str` ou dos tipos do catálogo) não passam: a validação e o hash veem o valor
    cru, e um método sobrescrito mudaria o que entra no termo."""
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=_fundamento(motivo_texto=_StrForjada("motivo")))
    assert refused.value.missing == ("motivo_texto",)

    class _Sub(FundamentoDoTermo):
        __slots__ = ()

    base = _fundamento()
    sub = _Sub(**{f.name: getattr(base, f.name) for f in dataclasses.fields(FundamentoDoTermo)})
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=sub)
    assert refused.value.missing == ("fundamento",)


def test_surrogate_solto_e_recusa_tipada_nao_excecao_crua() -> None:
    with pytest.raises(DenialTermRefusalError) as refused:
        # `hash_entrada` fixo: o helper não consegue nem calcular o hash de um surrogate.
        _render(fundamento=_fundamento(motivo_texto="a\ud800", hash_entrada="0" * 64))
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO
    assert refused.value.missing == ("motivo_texto",)
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(_negar(justificativa_clinica="a\ud800"))
    assert refused.value.missing == ("justificativa_clinica",)


@pytest.mark.parametrize(
    ("modo", "marca"),
    [("entrega", MARCA_RASCUNHO), ("rascunho", None), ("outro", None), ("rascunho", "outra marca")],
)
def test_o_termo_composto_nao_aceita_modo_e_marca_incoerentes(modo: str, marca: str | None) -> None:
    termo = _render()
    with pytest.raises(ValueError, match="inconsistente"):
        dataclasses.replace(termo, modo=modo, marca=marca)


def test_fundamento_devolvido_pelo_juridico_e_recusado() -> None:
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(fundamento=_fundamento(status_juridico="devolvido"))
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO


def test_aprovado_juridico_e_para_entrega_e_pendente_so_rascunho_com_a_marca_fora_do_texto() -> None:
    entrega = _render()
    rascunho = _render(fundamento=_fundamento(status_juridico="pendente"))
    assert (entrega.modo, entrega.marca) == (MODO_ENTREGA, None)
    assert (rascunho.modo, rascunho.marca) == (MODO_RASCUNHO, MARCA_RASCUNHO)
    assert MARCA_RASCUNHO == "fundamento sem revisão jurídica"
    # A marca nunca entra no texto ao beneficiário; o texto é o mesmo nos dois modos.
    for termo in (entrega, rascunho):
        assert "revisão jurídica" not in termo.texto.lower()
        assert "rascunho" not in termo.texto.lower()
    assert rascunho.texto == entrega.texto


def test_o_rascunho_nao_vira_termo_entregue_no_registro() -> None:
    """O render não registra nada: o worker continua só com o pino e `entrega_manual_pendente`."""
    _render(fundamento=_fundamento(status_juridico="pendente"))
    result = SendDenialNoticeWorker(written_notice=True).execute(_negar())
    assert result["entrega_negativa_status"] == "entrega_manual_pendente"
    assert not [k for k in result if "rascunho" in k or "modo" in k]


def test_fundamentacao_dut_nunca_substitui_o_fundamento() -> None:
    """A composição não lê `fundamentacao_dut`: preenchida, ela não abre a porta nem entra no termo."""
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(_negar(), fundamento=None)
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO
    texto = _render(_negar(fundamentacao_dut="DUT sintetica que nao pode aparecer")).texto
    assert "DUT sintetica" not in texto
    assert texto == _render(_negar(fundamentacao_dut=None)).texto
    arvore = ast.parse(Path(denial_term.__file__).read_text(encoding="utf-8"))
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(arvore)
        if isinstance(n, ast.Module | ast.FunctionDef | ast.ClassDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    lidos = [
        n.value
        for n in ast.walk(arvore)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    assert not [v for v in lidos if "fundamentacao_dut" in v]


def test_o_guard_do_worker_continua_exigindo_os_campos_do_registro() -> None:
    # O guard de completude da decisão humana não muda; a composição só lê o complemento clínico.
    assert {"justificativa_clinica", "fundamentacao_dut"} <= set(_REQUIRED_DENIAL_FIELDS)
    assert denial_term._COMPLEMENTO_DO_MOTIVO == "justificativa_clinica"


# --- E2 ------------------------------------------------------------------------------------------


def _generator_functions() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted((_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _GENERATOR_NAME.match(node.name)
        ]
        if names:
            found[path.relative_to(_ROOT).as_posix()] = names
    return found


def test_nenhum_gerador_de_documento_sem_template_versionado() -> None:
    found = _generator_functions()
    # Controle: a varredura acha o gerador conhecido (não é vácua).
    assert found.get("src/maezo/tools/workers/denial_term.py") == ["render_denial_term"]
    assert set(found) <= set(_DOCUMENT_GENERATORS), sorted(set(found) - set(_DOCUMENT_GENERATORS))
    for templates in _DOCUMENT_GENERATORS.values():
        for rel in templates:
            assert rel == DENIAL_TERM_TEMPLATE.path
            assert re.fullmatch(r"v\d+", DENIAL_TERM_TEMPLATE.versao)
            data = (_ROOT / rel).read_bytes()
            assert hashlib.sha256(data).hexdigest() == DENIAL_TERM_TEMPLATE.sha256


def test_template_alterado_ou_ausente_e_recusado(tmp_path: Path) -> None:
    altered = tmp_path / "termo.txt"
    altered.write_bytes(_TEMPLATE_PATH.read_bytes() + b" ")
    for path in (altered, tmp_path / "ausente.txt"):
        with pytest.raises(DenialTermRefusalError) as refused:
            load_denial_term_template(path)
        assert refused.value.code == ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO
    forged = DenialTermTemplate(versao="v0", sha256=DENIAL_TERM_TEMPLATE.sha256, texto="$motivo")
    with pytest.raises(DenialTermRefusalError) as refused:
        render_denial_term(
            _negar(), forged, fundamento=_fundamento(), canais_ouvidoria=_CANAIS, prazo_reanalise_dias_uteis=7
        )
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO


def test_template_coerente_mas_fora_do_pino_e_recusado() -> None:
    """Um template forjado com sha256 coerente com o próprio texto não passa: vale só o pino."""
    texto = "$motivo $fundamento $canais_ouvidoria $prazo_reanalise_dias_uteis"
    forged = DenialTermTemplate(
        versao="v1", sha256=hashlib.sha256(texto.encode("utf-8")).hexdigest(), texto=texto
    )
    with pytest.raises(DenialTermRefusalError) as refused:
        render_denial_term(
            _negar(), forged, fundamento=_fundamento(), canais_ouvidoria=_CANAIS, prazo_reanalise_dias_uteis=7
        )
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO


def _rn623_artigo(numero: int) -> str:
    """Texto do artigo (caput + parágrafos), linhas do arquivo de texto-base, byte a byte."""
    linhas = _RN623.read_text(encoding="utf-8").splitlines()
    inicio = next(i for i, linha in enumerate(linhas) if linha.startswith(f"Art. {numero}. "))
    fim = next(i for i in range(inicio + 1, len(linhas)) if linhas[i].startswith(f"Art. {numero + 1}. "))
    return "\n".join(linhas[inicio:fim])


def test_o_template_cita_o_art_16_verbatim_e_nao_tem_prosa_propria() -> None:
    """CD-192, ajuste 1, e CD-231 (A): o caput do art. 16, os incisos I, II e III do § 1° e cada
    trecho citado do art. 14 e do art. 16 batem byte a byte com
    `docs/compliance/legal/textos-base/RN-623-2024.md`; o resto é rótulo, slot ou metadado."""
    texto = _TEMPLATE_PATH.read_text(encoding="utf-8")
    art14, art16 = _rn623_artigo(14), _rn623_artigo(16)
    caput16 = art16.splitlines()[0].removeprefix("Art. 16. ")
    incisos = tuple(linha for linha in art16.splitlines() if re.match(r"(I|II|III) - ", linha))
    assert len(incisos) == 3  # controle: o texto-base tem os três incisos do § 1°
    assert caput16.encode("utf-8") in texto.encode("utf-8")
    citados = {"14": art14, "16": art16}
    rotulo = re.compile(r"RN 623/2024, art\. (14|16)(?:, § 1°)?(?: — (.+))?:")
    linhas = texto.splitlines()
    assert linhas[0].startswith("# ") and "FundamentoDoTermo" in linhas[0]
    rotulos = 0
    for linha in linhas[1:]:
        if linha in ("", caput16, *incisos) or re.fullmatch(r"\$\w+", linha):
            continue
        if linha == "$prazo_reanalise_dias_uteis dias úteis":
            assert "dias úteis" in art16
            continue
        casou = rotulo.fullmatch(linha)
        assert casou, f"prosa propria no template: {linha!r}"
        rotulos += 1
        if casou.group(2):
            assert casou.group(2).encode("utf-8") in citados[casou.group(1)].encode("utf-8"), linha
    assert rotulos == 4  # controle: a varredura viu os quatro rótulos


def test_o_metadado_do_template_nao_entra_no_termo() -> None:
    assert "FundamentoDoTermo" not in _render().texto
    assert not [linha for linha in _render().texto.splitlines() if linha.startswith("#")]


# --- E3 ------------------------------------------------------------------------------------------


def test_registro_da_negativa_nao_carrega_texto_autoral() -> None:
    """Com a flag ligada, o registro pina o template e não leva o termo nem afirma entrega."""
    result = SendDenialNoticeWorker(written_notice=True).execute(_negar())
    assert result["entrega_negativa_status"] == "entrega_manual_pendente"
    assert result["termo_negativa_template_versao"] == DENIAL_TERM_TEMPLATE.versao
    assert result["termo_negativa_template_sha256"] == DENIAL_TERM_TEMPLATE.sha256
    rendered = _render()
    flat = repr(result).lower()
    valores = [v for v in result.values() if isinstance(v, str)]
    assert rendered.texto not in valores
    assert not [v for v in valores if "Motivo sintetico" in v or "Ouvidoria" in v]
    assert "art. 16" not in flat and "ouvidoria" not in flat
    for key, value in result.items():
        assert not any(token in key.lower() for token in ("delivered", "registered", "sent")), key
        if isinstance(value, str):
            assert not any(token in value.lower() for token in _CLAIMS_OF_DELIVERY), (key, value)


def test_com_a_flag_desligada_o_registro_nao_ganha_o_pino_do_template() -> None:
    result = SendDenialNoticeWorker().execute(_negar())
    assert not {k for k in result if k.startswith("termo_negativa_")}


# --- E4 ------------------------------------------------------------------------------------------


def test_render_da_negativa_e_puro_e_completo() -> None:
    first, second = _render(), _render()
    assert first == second
    assert first.sha256 == hashlib.sha256(first.texto.encode("utf-8")).hexdigest()
    assert (first.template_versao, first.template_sha256) == (
        DENIAL_TERM_TEMPLATE.versao,
        DENIAL_TERM_TEMPLATE.sha256,
    )
    assert "$" not in first.texto
    for expected in (
        "Motivo sintetico do catalogo — Motivo sintetico para teste\n",
        "Clausula sintetica 9.9 — teste",
        *_CANAIS,
    ):
        assert expected in first.texto
    assert first.texto.rstrip().endswith("\n7 dias úteis")
    # Só os campos do art. 14 entram: nem o CID nem o auditor vão para o termo.
    assert "Z00.0" not in first.texto and "auditor-sintetico-01" not in first.texto
    lowered = first.texto.lower()
    assert not [p for p in _GENERIC_PHRASES if p in lowered]
    # Fundamento diferente, termo diferente (o sha256 depende do conteúdo).
    outro = _render(fundamento=_fundamento(clausula_contratual=dataclasses.replace(_CLAUSULA, texto="outro")))
    assert outro.sha256 != first.sha256
    segundo = dataclasses.replace(_DISPOSITIVO, rotulo="Outro dispositivo sintetico")
    com_os_tres = _render(fundamento=_fundamento(dispositivos_legais=(_DISPOSITIVO, segundo))).texto
    assert (
        "Clausula sintetica 9.9 — teste\nDispositivo sintetico\nOutro dispositivo sintetico\n" in com_os_tres
    )
    so_dispositivo = _render(
        fundamento=_fundamento(clausula_contratual=None, dispositivos_legais=(_DISPOSITIVO,))
    )
    assert "justifique:\nDispositivo sintetico\n" in so_dispositivo.texto


def test_o_render_nao_usa_relogio_acaso_nem_io() -> None:
    tree = ast.parse(Path(denial_term.__file__).read_text(encoding="utf-8"))
    render = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "render_denial_term"
    )
    names = {n.id for n in ast.walk(render) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(render) if isinstance(n, ast.Attribute)}
    assert not names & {"open", "datetime", "time", "random", "uuid", "Path", "os"}
    assert not attrs & {"now", "today", "read_bytes", "read_text", "open", "urandom", "uuid4"}


@pytest.mark.parametrize(
    ("canais", "prazo", "missing"),
    [
        ((), 7, ("canais_ouvidoria",)),
        (("  ",), 7, ("canais_ouvidoria",)),
        (("Telefone", None), 7, ("canais_ouvidoria",)),
        ("Telefone", 7, ("canais_ouvidoria",)),
        (("Telefone", "Email\n- canal forjado"), 7, ("canais_ouvidoria",)),
        (("Telefone\r",), 7, ("canais_ouvidoria",)),
        (_CANAIS, 8, ("prazo_reanalise_dias_uteis",)),
        (_CANAIS, 0, ("prazo_reanalise_dias_uteis",)),
        (_CANAIS, True, ("prazo_reanalise_dias_uteis",)),
        (_CANAIS, "7", ("prazo_reanalise_dias_uteis",)),
    ],
)
def test_sem_os_elementos_da_ouvidoria_nao_ha_termo(
    canais: Any, prazo: Any, missing: tuple[str, ...]
) -> None:
    """Art. 16, § 1º, II e III: dados da operadora; faltando, recusa — nada é inventado."""
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(canais_ouvidoria=canais, prazo_reanalise_dias_uteis=prazo)
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE
    assert refused.value.missing == missing


# --- negativa nunca decidida por código (CD-192, ajuste 2) --------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"decisao_auditor": "APROVAR"},
        {"decisao_auditor": None},
        {"decisao_auditor": "negar"},
        {"auditor_id": None},
        {"auditor_id": "   "},
        {"auditor_id": 7},
        {"auditor_id": None, "human_approved": "true"},
        {"auditor_id": None, "human_approved": 1},
    ],
)
def test_sem_decisao_humana_registrada_nao_ha_render(overrides: dict[str, object]) -> None:
    with pytest.raises(DenialTermRefusalError) as refused:
        _render(_negar(**overrides))
    assert refused.value.code == ERR_DENIAL_NOT_HUMAN


def test_o_sinal_humano_explicito_basta_como_proveniencia() -> None:
    assert _render(_negar(auditor_id=None, human_approved=True)).texto == _render().texto


def test_render_e_worker_nao_produzem_nem_alteram_a_decisao() -> None:
    record = _negar()
    snapshot = dict(record)
    rendered = _render(record)
    assert record == snapshot
    assert {f.name for f in fields(rendered)} == {
        "texto",
        "sha256",
        "template_versao",
        "template_sha256",
        "modo",
        "marca",
    }
    for flag in (False, True):
        worker_input = _negar()
        out = SendDenialNoticeWorker(written_notice=flag).execute(worker_input)
        assert worker_input == snapshot
        assert "decisao_auditor" not in out
    tree = ast.parse(Path(denial_term.__file__).read_text(encoding="utf-8"))
    stores = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Subscript)
        and isinstance(n.ctx, ast.Store | ast.Del)
        and isinstance(n.slice, ast.Constant)
        and n.slice.value == "decisao_auditor"
    ]
    assert not stores


# --- flag desligada (CD-192, ajuste 3; CD-193, item 3) -------------------------------------------

#: Pré-condição DECLARADA para ligar `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` (CD-231): o parecer do
#: jurídico sobre o termo v1 e o catálogo revisado de fundamentos, pinado. Enquanto não existem, a
#: flag fica desligada e estes testes pinam o estado fechado.
PRECONDICAO_PARA_LIGAR_A_FLAG = (
    "parecer do juridico sobre o termo v1 e catalogo de fundamentos revisado e pinado"
)


def test_estado_fechado_flag_desligada_ate_a_confirmacao_juridica(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    monkeypatch.delenv("MAEZO_AUTH_DENIAL_WRITTEN_NOTICE", raising=False)
    assert WorkerRuntimeSettings().auth_denial_written_notice is False, PRECONDICAO_PARA_LIGAR_A_FLAG
    contrato = (_ROOT / "docs/processes/contracts/SP-OP-AUTH-001.md").read_text(encoding="utf-8")
    assert "Ligar a bandeira tem pre-condicao:** o parecer do juridico" in contrato
    assert "catalogo de fundamentos revisado e pinado" in contrato
    result = SendDenialNoticeWorker().execute(_negar())
    assert "entrega_negativa_status" not in result
    assert not [k for k in result if k.startswith("termo_negativa_")]


def _liga_a_flag(tree: ast.AST) -> bool:
    """Código (não docstring) que liga a flag: `written_notice=True` numa chamada ou
    `"denial_written_notice": True` num dict literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "written_notice"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    return True
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "denial_written_notice"
                    and isinstance(value, ast.Constant)
                    and value.value is True
                ):
                    return True
    return False


def test_nenhum_codigo_de_producao_chama_o_render_nem_liga_a_flag() -> None:
    chamadores: list[str] = []
    ligam: list[str] = []
    for path in sorted((_ROOT / "src").rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if rel != "src/maezo/tools/workers/denial_term.py":
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
            if names & {"render_denial_term", "load_denial_term_template"}:
                chamadores.append(rel)
        if _liga_a_flag(tree):
            ligam.append(rel)
    assert not chamadores
    assert not ligam
    # Controle: o detector acha as formas que ligam (não é vácuo).
    assert _liga_a_flag(ast.parse("SendDenialNoticeWorker(written_notice=True)"))
    assert _liga_a_flag(ast.parse('seams = {"denial_written_notice": True}'))
    assert not _liga_a_flag(ast.parse('"""written_notice=True"""'))

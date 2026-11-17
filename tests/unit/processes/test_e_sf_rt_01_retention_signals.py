"""E-SF-RT-01 — catalogo fechado `ret.*` e cerca anti-sinal-clinico (RT-09; CD-07).

Testes NEGATIVOS: cada um injeta a violacao e afirma a recusa TIPADA (codigo exato). Nenhum mock
de engine; nenhum xfail. Ancora normativa (verbatim, NORMATIVE-BASELINE-v2.md:185, LGPD art. 11
§5º): "É vedado às operadoras de planos privados de assistência à saúde o tratamento de dados de
saúde para a prática de seleção de riscos na contratação de qualquer modalidade, assim como na
contratação e exclusão de beneficiários."
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.validation import retention_signals as rs
from maezo.platform.validation.retention_signals import (
    RetentionSelector,
    RetentionSignalError,
    RetentionSignalRefusal,
    RetentionUse,
)

_RAIZ = Path(__file__).resolve().parents[3]
_ALLOWLIST = _RAIZ / rs.ALLOWLIST_RELPATH

#: Os 22 nomes de RT-09, transcritos do registro (literal: um nome a mais ou a menos no YAML
#: reprova; o teste nao confia no proprio arquivo que testa).
_NOMES_RT09 = frozenset(
    {
        "ret.aniversario.agendado",
        "ret.aviso_r0.enviado",
        "ret.aviso_r0.entregue",
        "ret.fato_reajuste.recebido",
        "ret.aviso_r1.enviado",
        "ret.recibo_renovacao.enviado",
        "ret.opcoes.abertas",
        "ret.cotacao_alternativa.solicitada",
        "ret.troca.solicitada",
        "ret.troca.concluida",
        "ret.contestacao.protocolada",
        "ret.contestacao.respondida",
        "ret.cancelamento.pedido_aberto",
        "ret.cancelamento.protocolado",
        "ret.cancelamento.motivo_registrado",
        "ret.cancelamento.efetivado",
        "ret.exclusao_dependente.protocolada",
        "lembrete.enviado",
        "segunda_via.fornecida",
        "negociacao.caso_aberto",
        "ret.boas_vindas.enviada",
        "ret.checkin_d7.enviado",
    }
)

#: Familias proibidas de RT-09 e o piso de tokens de cada uma (literal: tirar token do YAML para
#: "liberar" um sinal reprova aqui).
_FAMILIAS_PISO: dict[str, set[str]] = {
    "autorizacao": {"autorizac", "auth"},
    "guia": {"guia", "tiss"},
    "sinistro": {"sinistr", "contas", "glosa", "pagto"},
    "reembolso": {"reembols"},
    "programa_de_cuidado": {"programa", "cuidad"},
    "cpt_dlp": {"cpt", "dlp"},
    "declaracao_de_saude": {"ds", "saude"},
    "internacao": {"internac"},
    "uso_do_plano": {"uso", "utilizac"},
    "leitura_de_mensagem": {"leitur", "lido", "lida", "read", "visualiz"},
}

_SEM_USO = {n for n in _NOMES_RT09 if n.startswith("ret.cancelamento.") or n.startswith("ret.exclusao_")} | {
    "lembrete.enviado",
    "segunda_via.fornecida",
    "negociacao.caso_aberto",
}


@pytest.fixture(scope="module")
def catalogo() -> rs.RetentionSignalCatalog:
    return rs.load_catalog(_ALLOWLIST)


def _bruto() -> dict[str, Any]:
    dados = yaml.safe_load(_ALLOWLIST.read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return copy.deepcopy(dados)


def _payload_valido(catalogo: rs.RetentionSignalCatalog, evento: str) -> dict[str, object]:
    base: dict[str, object] = {
        "evento": evento,
        "versao_catalogo": catalogo.versao,
        "tenant_id": "tenant-sintetico",
        "ocorrido_em": "2026-11-16T12:00:00+00:00",
    }
    for campo in catalogo.eventos[evento].campos:
        base[campo.nome] = "valor-sintetico"
    return base


def _recusa(exc: pytest.ExceptionInfo[RetentionSignalError]) -> RetentionSignalRefusal:
    return exc.value.reason


# ---------------------------------------------------------------------------
# Testes obrigatorios do epico
# ---------------------------------------------------------------------------


def test_retencao_gatilho_fora_do_catalogo_recusa(catalogo: rs.RetentionSignalCatalog) -> None:
    """Criterio (2): `autorizacao.negada` como gatilho => `ERR_SINAL_RETENCAO_PROIBIDO`."""
    seletor = RetentionSelector(selector_id="sintetico", gatilhos=("autorizacao.negada",))
    with pytest.raises(RetentionSignalError) as exc:
        rs.validate_retention_selector(seletor, catalogo)
    assert _recusa(exc) is RetentionSignalRefusal.SINAL_PROIBIDO
    assert exc.value.reason.value == "ERR_SINAL_RETENCAO_PROIBIDO"
    # nao-vacuidade: o mesmo seletor com um gatilho do catalogo passa.
    rs.validate_retention_selector(
        RetentionSelector(selector_id="sintetico", gatilhos=("ret.aviso_r0.enviado",)), catalogo
    )


def test_evento_ret_sem_campo_clinico(catalogo: rs.RetentionSignalCatalog) -> None:
    """Criterio (3): `cid` no payload de um evento `ret.*` => `ERR_EVENTO_RET_CAMPO_CLINICO`."""
    payload = _payload_valido(catalogo, "ret.aviso_r0.enviado")
    rs.validate_retention_event_payload("ret.aviso_r0.enviado", payload, catalogo)  # nao-vacuidade
    payload["cid"] = "C50.9"
    with pytest.raises(RetentionSignalError) as exc:
        rs.validate_retention_event_payload("ret.aviso_r0.enviado", payload, catalogo)
    assert _recusa(exc) is RetentionSignalRefusal.CAMPO_CLINICO
    assert "C50.9" not in str(exc.value)  # a recusa nunca carrega valor


# ---------------------------------------------------------------------------
# Criterio (1) e (2): catalogo fechado
# ---------------------------------------------------------------------------


def test_catalogo_e_exatamente_rt09(catalogo: rs.RetentionSignalCatalog) -> None:
    assert set(catalogo.eventos) == _NOMES_RT09


@pytest.mark.parametrize(
    "sinal",
    [
        "agents.events.auth.completed",
        "agents.events.contas.glosa_identified",
        "agents.events.pagto.completed",
        "agents.events.reembolso.completed",
        "agents.events.programa.completed",
        "guia.emitida",
        "sinistro.registrado",
        "cpt.aplicada",
        "ds.preenchida",
        "internacao.iniciada",
        "ret.uso_plano.zerado",
        "ret.nao_uso.detectado",
        "ret.aviso_r0.lido",
        "ret.aviso_r0.visualizado",
        "ret.exame.agendado",  # forma de PHI (phi_completeness.SHAPE_TOKENS)
    ],
)
def test_familias_proibidas_recusam_como_gatilho_e_segmento(
    catalogo: rs.RetentionSignalCatalog, sinal: str
) -> None:
    for uso in RetentionUse:
        with pytest.raises(RetentionSignalError) as exc:
            rs.validate_retention_event_name(sinal, catalogo, uso=uso)
        assert _recusa(exc) is RetentionSignalRefusal.SINAL_PROIBIDO


def test_gatilho_desconhecido_nao_clinico_recusa_fora_do_catalogo(
    catalogo: rs.RetentionSignalCatalog,
) -> None:
    for sinal in ("ret.inventado.qualquer", "agents.events.cancel.completed", "RET.Aviso"):
        with pytest.raises(RetentionSignalError) as exc:
            rs.validate_retention_event_name(sinal, catalogo, uso=RetentionUse.GATILHO)
        assert _recusa(exc) is RetentionSignalRefusal.FORA_DO_CATALOGO


@pytest.mark.parametrize("nome", sorted(_SEM_USO))
def test_eventos_de_saida_e_cobranca_nao_sao_gatilho_nem_segmento(
    catalogo: rs.RetentionSignalCatalog, nome: str
) -> None:
    """DL-0098 / GTM-E-64 lido com OS-08 / RT-07 B / RT-06 item 2 / Adendo 01 §7 (q)."""
    for uso in RetentionUse:
        with pytest.raises(RetentionSignalError) as exc:
            rs.validate_retention_selector(
                RetentionSelector(
                    selector_id="s",
                    gatilhos=(nome,) if uso is RetentionUse.GATILHO else ("ret.aviso_r0.enviado",),
                    segmentos=(nome,) if uso is RetentionUse.SEGMENTO else (),
                ),
                catalogo,
            )
        assert _recusa(exc) is RetentionSignalRefusal.USO_NAO_PERMITIDO


def test_seletor_sem_gatilho_recusa(catalogo: rs.RetentionSignalCatalog) -> None:
    with pytest.raises(RetentionSignalError) as exc:
        rs.validate_retention_selector(RetentionSelector(selector_id="s", gatilhos=()), catalogo)
    assert _recusa(exc) is RetentionSignalRefusal.SELETOR_INVALIDO


def test_variavel_de_seletor_clinica_ou_nao_declarada_recusa(catalogo: rs.RetentionSignalCatalog) -> None:
    gat = ("ret.aviso_r0.enviado",)
    rs.validate_retention_selector(RetentionSelector("s", gat, variaveis=("numero_contrato",)), catalogo)
    for var, codigo in (
        ("cid10", RetentionSignalRefusal.CAMPO_CLINICO),
        ("numero_autorizacao", RetentionSignalRefusal.CAMPO_CLINICO),
        ("matricula_beneficiario", RetentionSignalRefusal.CAMPO_CLINICO),
        ("cor_favorita", RetentionSignalRefusal.CAMPO_NAO_DECLARADO),
    ):
        with pytest.raises(RetentionSignalError) as exc:
            rs.validate_retention_selector(RetentionSelector("s", gat, variaveis=(var,)), catalogo)
        assert _recusa(exc) is codigo, var


# ---------------------------------------------------------------------------
# Criterio (3): esquema fechado do payload
# ---------------------------------------------------------------------------


def test_payload_relabel_de_cancel_completed_recusa(catalogo: rs.RetentionSignalCatalog) -> None:
    """Licao `matricula-raw-to-general-zone-notifications-topic` (predeploy-findings.json)."""
    payload = _payload_valido(catalogo, "ret.cancelamento.efetivado")
    payload["matricula_beneficiario"] = "x"
    with pytest.raises(RetentionSignalError) as exc:
        rs.validate_retention_event_payload("ret.cancelamento.efetivado", payload, catalogo)
    assert _recusa(exc) is RetentionSignalRefusal.CAMPO_CLINICO


@pytest.mark.parametrize(
    ("mutacao", "codigo"),
    [
        (lambda p: p.update({"cor_favorita": "azul"}), RetentionSignalRefusal.CAMPO_NAO_DECLARADO),
        (lambda p: p.pop("numero_contrato"), RetentionSignalRefusal.CAMPO_AUSENTE),
        (lambda p: p.update({"numero_contrato": {"cid": "x"}}), RetentionSignalRefusal.VALOR_NAO_ESCALAR),
        (lambda p: p.update({"numero_contrato": 1.5}), RetentionSignalRefusal.VALOR_NAO_ESCALAR),
        (lambda p: p.update({"evento": "ret.troca.solicitada"}), RetentionSignalRefusal.ENVELOPE_INCOERENTE),
        (lambda p: p.update({"versao_catalogo": "outra"}), RetentionSignalRefusal.ENVELOPE_INCOERENTE),
        (lambda p: p.update({"dedupe_key": "k"}), RetentionSignalRefusal.CAMPO_NAO_DECLARADO),
        (lambda p: p.update({"procedimento_tuss": "x"}), RetentionSignalRefusal.CAMPO_NAO_DECLARADO),
    ],
)
def test_payload_fora_do_esquema_recusa(
    catalogo: rs.RetentionSignalCatalog, mutacao: Any, codigo: RetentionSignalRefusal
) -> None:
    payload = _payload_valido(catalogo, "ret.aviso_r0.enviado")
    mutacao(payload)
    with pytest.raises(RetentionSignalError) as exc:
        rs.validate_retention_event_payload("ret.aviso_r0.enviado", payload, catalogo)
    assert _recusa(exc) is codigo


def test_motivo_de_cancelamento_sem_vinculo_ao_contrato(catalogo: rs.RetentionSignalCatalog) -> None:
    """Variante de RT-D1: contagem agregada sem vinculo."""
    campos = {c.nome for c in catalogo.eventos["ret.cancelamento.motivo_registrado"].campos}
    assert campos == {"codigo_motivo"}


def test_nenhum_campo_declarado_e_clinico(catalogo: rs.RetentionSignalCatalog) -> None:
    from maezo.platform.validation.phi_completeness import CLEAN, classify

    campos = list(catalogo.envelope) + [c for ev in catalogo.eventos.values() for c in ev.campos]
    assert campos
    for campo in campos:
        assert classify(campo.nome) == CLEAN, campo.nome
        assert campo.classe in rs.ALLOWED_FIELD_CLASSES, campo.nome


# ---------------------------------------------------------------------------
# A allowlist nao lava sinal nem campo (mutacao do proprio YAML)
# ---------------------------------------------------------------------------


def test_allowlist_que_declara_campo_clinico_recusa() -> None:
    for campo, classe in (("cid", "C6"), ("matricula_beneficiario", "C6"), ("numero_contrato", "C3")):
        dados = _bruto()
        dados["eventos"][1]["campos"] = [{"nome": campo, "classe": classe}]
        with pytest.raises(RetentionSignalError) as exc:
            rs.catalog_from_mapping(dados)
        assert _recusa(exc) is RetentionSignalRefusal.CAMPO_CLINICO, campo


def test_allowlist_que_cataloga_evento_proibido_recusa() -> None:
    dados = _bruto()
    novo = dict(dados["eventos"][1])
    novo["nome"] = "ret.autorizacao.negada"
    dados["eventos"].append(novo)
    with pytest.raises(RetentionSignalError) as exc:
        rs.catalog_from_mapping(dados)
    assert _recusa(exc) is RetentionSignalRefusal.SINAL_PROIBIDO


def test_familias_proibidas_cobrem_rt09(catalogo: rs.RetentionSignalCatalog) -> None:
    por_id = {f.id: {t.token for t in f.tokens} for f in catalogo.familias}
    for fid, piso in _FAMILIAS_PISO.items():
        assert fid in por_id, fid
        assert piso <= por_id[fid], fid


# ---------------------------------------------------------------------------
# Portoes fechados por construcao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chave", "valor"),
    [("status", "RATIFICADO"), ("emissao", "lago"), ("schema", "outro")],
)
def test_portao_fechado_no_cabecalho(chave: str, valor: str) -> None:
    dados = _bruto()
    dados[chave] = valor
    with pytest.raises(RetentionSignalError) as exc:
        rs.catalog_from_mapping(dados)
    assert _recusa(exc) is RetentionSignalRefusal.ALLOWLIST_INVALIDA


def test_portao_emissor_declarado_recusa() -> None:
    dados = _bruto()
    dados["eventos"][0]["emissor"] = "SP-OP-CANCEL-001"
    with pytest.raises(RetentionSignalError) as exc:
        rs.catalog_from_mapping(dados)
    assert _recusa(exc) is RetentionSignalRefusal.ALLOWLIST_INVALIDA


def test_allowlist_com_chave_duplicada_recusa(tmp_path: Path) -> None:
    alvo = tmp_path / "allowlist.yaml"
    alvo.write_text(_ALLOWLIST.read_text(encoding="utf-8") + "\nstatus: DRAFT\n", encoding="utf-8")
    with pytest.raises(RetentionSignalError) as exc:
        rs.load_catalog(alvo)
    assert _recusa(exc) is RetentionSignalRefusal.ALLOWLIST_INVALIDA


def test_caminho_padrao_aponta_para_a_allowlist_do_repo() -> None:
    assert rs.default_allowlist_path() == _ALLOWLIST
    assert rs.load_catalog().status == "DRAFT"


_EXTENSOES = {".py", ".yaml", ".yml", ".json", ".bpmn", ".dmn", ".tf", ".tfvars", ".toml", ".sql"}
_RAIZES_VARRIDAS = ("src", "spec", "deploy", "config")
#: Registro de templates do OutboundGate (E-SF-13): declara templates `ret.*` REGISTRADOS SEM APROVACAO,
#: que o gate recusa. Declaracao de template nao e' emissor; so' sai da varredura enquanto
#: `test_templates_ret_registrados_seguem_sem_aprovacao` provar que nenhum deles pode ser enviado.
_REGISTRO_DE_TEMPLATES = _RAIZ / "spec/communications/whatsapp-templates.yaml"


def _ocorrencias(nomes: frozenset[str], *, excluir_allowlist: bool) -> list[tuple[str, str]]:
    padrao = re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(n) for n in sorted(nomes)) + r")(?!\w)")
    generico = re.compile(r"(?<![\w.])ret\.[a-z0-9_]+\.[a-z0-9_]+")
    achados: list[tuple[str, str]] = []
    for raiz in _RAIZES_VARRIDAS:
        for caminho in (_RAIZ / raiz).rglob("*"):
            if not caminho.is_file() or caminho.suffix not in _EXTENSOES or "__pycache__" in caminho.parts:
                continue
            fora_da_varredura = caminho.parent == _ALLOWLIST.parent or caminho == _REGISTRO_DE_TEMPLATES
            if excluir_allowlist and fora_da_varredura:
                continue
            texto = caminho.read_text(encoding="utf-8", errors="replace")
            rel = str(caminho.relative_to(_RAIZ))
            achados += [(rel, m.group()) for m in padrao.finditer(texto)]
            achados += [(rel, m.group()) for m in generico.finditer(texto)]
    return achados


def test_nenhum_emissor_de_evento_ret_na_onda_1(catalogo: rs.RetentionSignalCatalog) -> None:
    """A9 nao executavel e CD-07: nenhum nome do catalogo aparece fora da allowlist em src/,
    spec/, deploy/ ou config/ (BPMN `event_topic`, worker, topico, tfvars). O dia em que um
    emissor nascer (E-SF-35r), este teste cai e a mudanca e' consciente."""
    nomes = frozenset(catalogo.eventos)
    assert _ocorrencias(nomes, excluir_allowlist=False), "nao-vacuidade: a varredura nao acha a allowlist"
    assert _ocorrencias(nomes, excluir_allowlist=True) == []


def test_templates_ret_registrados_seguem_sem_aprovacao(catalogo: rs.RetentionSignalCatalog) -> None:
    """Contrapartida EXATA da exclusao do registro de templates na varredura de emissores: todo template
    que cite qualquer nome do catalogo ou forma `ret.<a>.<b>` em QUALQUER campo (nao so' no id) segue
    sem submissao a Meta, sem revisao de conteudo, sem corpo, sem variaveis, sem classificacao de
    publicidade e sob finalidade nao ratificada. No dia em que um deles puder ser enviado, este teste cai."""
    dados = yaml.safe_load(_REGISTRO_DE_TEMPLATES.read_text(encoding="utf-8"))
    nomes = sorted(catalogo.eventos)
    padrao = re.compile(r"(?<![\w.])(?:" + "|".join(re.escape(n) for n in nomes) + r")(?!\w)")
    generico = re.compile(r"(?<![\w.])ret\.[a-z0-9_]+\.[a-z0-9_]+")
    ratificadas = {p["id"] for p in dados["purposes"] if p.get("ratified") is not False}
    templates = []
    for t in dados["templates"]:
        texto = yaml.safe_dump(t)
        if padrao.search(texto) or generico.search(texto):
            templates.append(t)
    assert templates, "nao-vacuidade: o registro deveria declarar templates ret.*"
    for t in templates:
        assert t["meta_status"] == "not_submitted", t["id"]
        assert t["meta_template_ref"] is None, t["id"]
        assert t["content_review_ref"] is None, t["id"]
        assert t["body"] is None, t["id"]
        assert t["variables"] == [], t["id"]
        assert t["publicity"] is None, t["id"]
        assert t["purpose_ref"] not in ratificadas, t["id"]


def test_nenhum_processo_de_reajuste_criado() -> None:
    """CD-07: `SP-OP-REAJUSTE-001` nao existe e este epico nao o cria."""
    assert not list((_RAIZ / "spec/processes/bpmn").glob("*REAJUSTE*"))
    assert not list((_RAIZ / "docs/processes/contracts").glob("*REAJUSTE*"))


def _seletores_literais(fonte: str) -> list[RetentionSelector]:
    """Toda construcao `RetentionSelector(...)` de um fonte Python, com argumentos LITERAIS."""
    achados: list[RetentionSelector] = []
    for no in ast.walk(ast.parse(fonte)):
        if (
            isinstance(no, ast.Call)
            and getattr(no.func, "id", getattr(no.func, "attr", None)) == "RetentionSelector"
        ):
            kwargs = {k.arg: ast.literal_eval(k.value) for k in no.keywords if k.arg}
            achados.append(RetentionSelector(**kwargs))
    return achados


def test_todo_seletor_do_repo_declara_so_eventos_do_catalogo(catalogo: rs.RetentionSignalCatalog) -> None:
    """Criterio (1). Hoje nao ha seletor; a varredura prova que pegaria um (nao-vacuidade)."""
    sintetico = "RetentionSelector(selector_id='x', gatilhos=('autorizacao.negada',))"
    [pego] = _seletores_literais(sintetico)
    with pytest.raises(RetentionSignalError):
        rs.validate_retention_selector(pego, catalogo)
    for caminho in sorted((_RAIZ / "src").rglob("*.py")):
        for seletor in _seletores_literais(caminho.read_text(encoding="utf-8")):
            rs.validate_retention_selector(seletor, catalogo)


def test_lembrete_proativo_segue_desligado_por_padrao() -> None:
    """Adendo 01 §7 (q) / RT-D4: `lembrete.enviado` nao tem emissor e o job segue desligado."""
    from maezo.platform.lembretes.config import LembreteSettings

    assert LembreteSettings.model_fields["habilitado"].default is False


def test_validate_artifacts_segue_verde_com_o_diretorio_novo() -> None:
    from maezo.platform.validation.cli import validate_artifacts

    assert _ALLOWLIST.is_file()
    assert validate_artifacts(["spec/processes", "spec/policies", "spec/agents"]) == 0


#: Esquema de campos de cada evento, literal: campo novo no YAML (ex.: chave de medicao) reprova.
_COBRANCA = ("lembrete.enviado", "segunda_via.fornecida", "negociacao.caso_aberto")
_CAMPOS_ESPERADOS: dict[str, set[str]] = {n: {"numero_contrato"} for n in _NOMES_RT09 if n not in _COBRANCA}
_CAMPOS_ESPERADOS["ret.cancelamento.motivo_registrado"] = {"codigo_motivo"}
_CAMPOS_ESPERADOS.update({n: set() for n in _COBRANCA})


def test_esquema_de_campos_e_exatamente_o_declarado(catalogo: rs.RetentionSignalCatalog) -> None:
    assert {c.nome for c in catalogo.envelope} == {"evento", "versao_catalogo", "tenant_id", "ocorrido_em"}
    assert {n: {c.nome for c in ev.campos} for n, ev in catalogo.eventos.items()} == _CAMPOS_ESPERADOS

"""`scripts/ops/staff_material_escalation_catalogo.py`: repin OFFLINE do SP-OP-ESCALATION-001 (DL-0145).

O vivo sintetico passa pelo MESMO `approver.review_admission` do `gerar`; cada recusa tem controle positivo.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts.ops import staff_material_escalation_catalogo as esc

from maezo.portal.engine.profile import canonicalize
from tests.unit.staff_materials.test_approver import POLICIES, _artifact, _pin, admission

UUID_V2 = "11111111-2222-3333-4444-555555555555"
UUID_V4 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PID_V2 = f"SP-OP-ESCALATION-001:2:{UUID_V2}"
PID_V4 = f"SP-OP-ESCALATION-001:4:{UUID_V4}"
DMN = dict(
    kind="dmn",
    groups=["atendimento-humano", "enfermagem-triagem", "plantao-clinico"],
    dmn_definition_id="escalation_routing:1:x",
    dmn_definition_key="escalation_routing",
    dmn_definition_version="1",
    dmn_resource_digest="9" * 64,
)
ESTATICO = dict(
    kind="static",
    groups=["supervisao-atendimento"],
    dmn_definition_id=None,
    dmn_definition_key=None,
    dmn_definition_version=None,
    dmn_resource_digest=None,
)


def _vivo() -> tuple[dict[str, Any], dict[str, Any]]:
    """Catalogo e admissao vivos sinteticos: escalonamento v2 (2 tarefas) e uma tarefa de outro processo."""
    form = dict(_artifact("form-escalation"), artifact_ref="escalation")
    comum = dict(
        process_definition_key="SP-OP-ESCALATION-001",
        process_definition_version="2",
        process_definition_digest="1" * 64,
        form_key="escalation",
        form_version="1",
        form_digest=form["digest"],
        form_source_status="BPMN_FORMDATA",
        allowed_inputs=["resultado", "notas_resolucao"],
        required_roles=["escalonamento-tratar"],
        subject_policy=_pin("subject-policy"),
        consent_policy=_pin("consent-policy"),
        resource_policy=_pin("resource-policy"),
        disclosure_policy=_pin("disclosure-policy"),
        opaque_task_id_policy=_pin("opaque-task-id-policy"),
    )
    entradas = [
        dict(
            comum,
            process_definition_id=PID_V2,
            task_definition_key="UT_TratarEscalonamento",
            group_domain=DMN,
        ),
        dict(
            comum,
            process_definition_id=PID_V2,
            task_definition_key="UT_SupervisorAssume",
            group_domain=ESTATICO,
        ),
    ]
    catalogo = dict(
        schema="portal-read-catalog.v1",
        catalog_ref="catalog-staff",
        publisher_ref="portal-staff",
        entries=entradas,
        policies=[_artifact(p) for p in POLICIES],
        forms=[form],
        deployment_receipt_ref="SYN-deployment",
        deployment_receipt_digest="2" * 64,
    )
    classificacao = dict(
        classification_ref="SYN-classification-escalation",
        classification_digest=hashlib.sha256(b"SYN classification escalation").hexdigest(),
        policy_ref=_pin("disclosure-policy")["artifact_ref"],
        policy_digest=_pin("disclosure-policy")["digest"],
        projection="full_task_detail.v1",
        fields_digest=hashlib.sha256(b"SYN fields escalation").hexdigest(),
    )
    humanas = [
        dict(
            process_definition_id=PID_V2,
            task_definition_key=e["task_definition_key"],
            classification=classificacao,
            identity_policy=_pin("opaque-task-id-policy"),
            task_id_format="uuid",
            candidate_groups=list(e["group_domain"]["groups"]),  # type: ignore[index]
            user_candidates="refused",
        )
        for e in entradas
    ]
    adm = admission(datetime(2026, 10, 16, 13, 4, 34, tzinfo=UTC))
    adm["admission_revision"] = "7"
    adm["human"] = {"entries": humanas}
    adm["publishers"].append(
        dict(kind="resource", publisher_ref="portal-staff", source_ref_prefix="portal-resource:amh:task:")
    )
    adm["catalog"]["catalog_digest"] = hashlib.sha256(canonicalize(catalogo)).hexdigest()
    return json.loads(json.dumps(catalogo)), adm


def _conferir_vivo_passa() -> None:
    """Controle positivo do proprio fixture: o vivo sintetico e aceito pelo approver do `gerar`."""
    from tools.staff_materials import approver

    catalogo, adm = _vivo()
    approver.review_admission(canonicalize(adm), canonicalize(catalogo))


def test_o_vivo_sintetico_e_aceito_pelo_approver() -> None:
    _conferir_vivo_passa()


def test_montar_clona_as_2_tarefas_na_versao_nova_e_mantem_a_antiga() -> None:
    catalogo, adm = _vivo()
    novo, novas = esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)])
    assert len(novo["entries"]) == 4 and len(novas) == 2
    assert [e["process_definition_id"] for e in novo["entries"][:2]] == [PID_V2, PID_V2]  # in-flight seguem
    por_tarefa = {e["task_definition_key"]: e for e in novo["entries"][2:]}
    assert set(por_tarefa) == set(esc.TAREFAS)
    for e in por_tarefa.values():
        assert (
            e["process_definition_id"],
            e["process_definition_version"],
            e["process_definition_digest"],
        ) == (
            PID_V4,
            "4",
            "c" * 64,
        )
    assert por_tarefa["UT_TratarEscalonamento"]["group_domain"] == DMN
    assert por_tarefa["UT_SupervisorAssume"]["group_domain"] == ESTATICO
    assert {(h["process_definition_id"], h["task_definition_key"]) for h in novas} == {
        (PID_V4, t) for t in esc.TAREFAS
    }
    assert catalogo["entries"][0]["process_definition_id"] == PID_V2  # o vivo nao foi mutado
    medidas = esc.conferir(novo, adm, novas)
    assert medidas["review_admission"] == "OK" and medidas["entradas_human_novas"] == 2
    assert any(
        PID_V4 in linha and "UT_SupervisorAssume" in linha for linha in medidas["linhas_escalonamento"]
    )


def test_admissao_como_o_gerar_acrescenta_e_sobe_a_revisao() -> None:
    catalogo, adm = _vivo()
    novo, novas = esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)])
    raw = esc.admissao_como_o_gerar(adm, canonicalize(novo), novas)
    reg = json.loads(raw)
    assert reg["admission_revision"] == "8"
    assert reg["catalog"]["catalog_digest"] == hashlib.sha256(canonicalize(novo)).hexdigest()
    assert len(reg["human"]["entries"]) == 4


def test_duas_versoes_novas_intermediaria_e_atual() -> None:
    catalogo, adm = _vivo()
    pid3 = f"SP-OP-ESCALATION-001:3:{UUID_V4.replace('a', '3')}"
    novo, novas = esc.montar(catalogo, adm, [(pid3, 3, "d" * 64), (PID_V4, 4, "c" * 64)])
    assert len(novo["entries"]) == 6 and len(novas) == 4
    assert esc.conferir(novo, adm, novas)["review_admission"] == "OK"


@pytest.mark.parametrize(
    ("definicoes", "trecho"),
    [
        ([(f"SP-OP-ESCALATION-001:2:{UUID_V4}", 2, "c" * 64)], "nao e posterior"),
        ([(f"SP-OP-ESCALATION-001:1:{UUID_V4}", 1, "c" * 64)], "nao e posterior"),
        ([(PID_V2, 5, "c" * 64)], "ja esta admitido"),
        ([(PID_V4, 4, "c" * 64), (PID_V4, 4, "c" * 64)], "repetido"),
        ([], "ao menos uma"),
    ],
)
def test_montar_recusa_versao_que_nao_anda_para_a_frente(definicoes: Any, trecho: str) -> None:
    catalogo, adm = _vivo()
    esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)])  # controle positivo
    with pytest.raises(esc.Recusa, match=trecho):
        esc.montar(catalogo, adm, definicoes)


def test_montar_recusa_vivo_sem_as_2_tarefas_ou_sem_human() -> None:
    catalogo, adm = _vivo()
    so_uma = dict(catalogo, entries=catalogo["entries"][:1])
    with pytest.raises(esc.Recusa, match="exatamente as tarefas"):
        esc.montar(so_uma, adm, [(PID_V4, 4, "c" * 64)])
    sem_human = dict(adm, human={"entries": adm["human"]["entries"][:1]})
    with pytest.raises(esc.Recusa, match="entradas `human`"):
        esc.montar(catalogo, sem_human, [(PID_V4, 4, "c" * 64)])
    with pytest.raises(esc.Recusa, match="nenhuma tarefa"):
        esc.montar(dict(catalogo, entries=[]), adm, [(PID_V4, 4, "c" * 64)])


@pytest.mark.parametrize(
    "estraga",
    [
        lambda novo, novas: novas.pop(),  # catalogo sem a `human` correspondente
        lambda novo, novas: novo["entries"].pop(),  # `human` sem a entrada do catalogo
        lambda novo, novas: novas[0].update(candidate_groups=["fora-do-dominio"]),
        lambda novo, novas: novo["entries"][-1].update(process_definition_digest="X"),
        lambda novo, novas: novo["entries"][-1].update(form_key="cancel_execucao"),
    ],
)
def test_conferir_tem_dentes(estraga: Any) -> None:
    catalogo, adm = _vivo()
    novo, novas = esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)])
    esc.conferir(novo, adm, novas)  # controle positivo
    estraga(novo, novas)
    with pytest.raises(esc.Recusa):
        esc.conferir(novo, adm, novas)


def test_conferir_projeta_o_segredo_do_job_e_recusa_acima_do_limite() -> None:
    catalogo, adm = _vivo()
    novo, novas = esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)])
    vivo_raw = canonicalize(catalogo)
    ok = esc.conferir(novo, adm, novas, tamanho_segredo_job=30000, catalogo_vivo_raw=vivo_raw)
    assert 30000 < ok["segredo_job_projetado_bytes"] < esc.LIMITE_SEGREDO
    with pytest.raises(esc.Recusa, match="segredo do job"):
        esc.conferir(
            novo, adm, novas, tamanho_segredo_job=esc.LIMITE_SEGREDO - 10, catalogo_vivo_raw=vivo_raw
        )


def test_dmn_novo_troca_so_o_dominio_dinamico() -> None:
    catalogo, adm = _vivo()
    dmn = {"id": "escalation_routing:2:y", "version": "2", "sha256": "8" * 64}
    novo, _ = esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)], dmn=dmn)
    tratar, sup = novo["entries"][2], novo["entries"][3]
    assert tratar["group_domain"]["dmn_definition_id"] == "escalation_routing:2:y"
    assert tratar["group_domain"]["dmn_definition_version"] == "2"
    assert sup["group_domain"] == ESTATICO
    assert novo["entries"][0]["group_domain"] == DMN  # a versao antiga continua com o DMN antigo


@pytest.mark.parametrize(
    "texto",
    [
        "SP-OP-ESCALATION-001:4:" + UUID_V4,  # sem sha
        "SP-OP-CANCEL-001:4:" + UUID_V4 + "=" + "c" * 64,
        "SP-OP-ESCALATION-001:0:" + UUID_V4 + "=" + "c" * 64,
        "SP-OP-ESCALATION-001:4:nao-uuid=" + "c" * 64,
        "SP-OP-ESCALATION-001:4:" + UUID_V4 + "=" + "C" * 64,
    ],
)
def test_ler_definicao_recusa(texto: str) -> None:
    assert esc.ler_definicao(f"{PID_V4}={'c' * 64}") == (PID_V4, 4, "c" * 64)
    with pytest.raises(esc.Recusa):
        esc.ler_definicao(texto)


def _arquivos(tmp_path: Path) -> list[str]:
    catalogo, adm = _vivo()
    (tmp_path / "cat.json").write_bytes(canonicalize(catalogo))
    (tmp_path / "adm.json").write_bytes(canonicalize(adm))
    (tmp_path / "bpmn.bpmn").write_bytes(b"<bpmn dueDate/>")
    (tmp_path / "out").mkdir()
    return ["--catalogo-vivo", str(tmp_path / "cat.json"), "--admissao-viva", str(tmp_path / "adm.json")]


def test_cli_montar_escreve_insumos_e_confere_o_bpmn_do_repo(tmp_path: Path, capsys: Any) -> None:
    vivos = _arquivos(tmp_path)
    digest = hashlib.sha256(b"<bpmn dueDate/>").hexdigest()
    args = ["montar", *vivos, "--out", str(tmp_path / "out"), "--bpmn-repo", str(tmp_path / "bpmn.bpmn")]
    with pytest.raises(esc.Recusa, match="nao e o do repositorio"):
        esc.main([*args, "--definicao", f"{PID_V4}={'c' * 64}"])
    assert not (tmp_path / "out" / "task-catalog.json").exists()
    assert esc.main([*args, "--definicao", f"{PID_V4}={digest}"]) == 0
    resumo = json.loads(capsys.readouterr().out)
    cat = (tmp_path / "out" / "task-catalog.json").read_bytes()
    assert resumo["catalog_sha256"] == hashlib.sha256(cat).hexdigest()
    novas = json.loads((tmp_path / "out" / "entradas-humanas.json").read_bytes())
    assert {h["process_definition_id"] for h in novas} == {PID_V4}
    assert canonicalize(json.loads(cat)) == cat  # JCS, como o `gerar` le


def test_cli_ensaio_confere_sem_escrever(tmp_path: Path, capsys: Any) -> None:
    vivos = _arquivos(tmp_path)
    assert esc.main(["ensaio", *vivos, "--bpmn-repo", str(tmp_path / "bpmn.bpmn")]) == 0
    resumo = json.loads(capsys.readouterr().out)
    assert resumo["ensaio"] is True and resumo["versao_ensaiada"] == 3 and resumo["versao_admitida"] == 2
    assert list((tmp_path / "out").iterdir()) == []


def test_o_bpmn_do_repo_tem_as_2_tarefas_e_o_duedate() -> None:
    """O que o script clona e o que o BPMN do main tem (as 2 userTasks e o prazo da DL-0145)."""
    raiz = Path(__file__).resolve().parents[3]
    xml = (raiz / esc.BPMN_REPO).read_text(encoding="utf-8")
    for tarefa in esc.TAREFAS:
        assert f'<bpmn:userTask id="{tarefa}"' in xml
    assert 'camunda:dueDate="${roteamento.sla_resolucao}"' in xml


def test_saida_nao_tem_bytes_de_formulario_novos() -> None:
    """O escalonamento reaproveita o formulario vivo: nenhum `forms[]` novo, nenhuma politica nova."""
    catalogo, adm = _vivo()
    novo, _ = esc.montar(catalogo, adm, [(PID_V4, 4, "c" * 64)])
    assert novo["forms"] == catalogo["forms"] and novo["policies"] == catalogo["policies"]
    assert base64.b64decode(novo["forms"][0]["bytes_base64"]) == b"SYN form-escalation"

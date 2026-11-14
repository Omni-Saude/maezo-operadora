"""Repin do catalogo Q2 numa versao nova de processo ja admitido (SP-OP-ESCALATION-001 v3, 23/10/2026)."""

from __future__ import annotations

import pytest
from scripts.ops.staff_material_repin_catalogo import montar

_V2 = "SP-OP-ESCALATION-001:2:86c0ccc3-bae7-11f1-90e5-06fff85ea971"
_V3 = "SP-OP-ESCALATION-001:3:54460c89-ca4f-11f1-9fd8-02ffe2b01f3f"
_CANCEL = "SP-OP-CANCEL-001:2:8d4fa921-c8e5-11f1-8e85-06ffc63e4cc5"
_SHA = "8" * 64


def _entrada(pid: str, tarefa: str) -> dict[str, object]:
    chave, versao, _ = pid.split(":")
    return {
        "process_definition_id": pid,
        "process_definition_key": chave,
        "process_definition_version": versao,
        "process_definition_digest": "e" * 64,
        "task_definition_key": tarefa,
        "form_key": "escalation",
        "required_roles": ["escalonamento-tratar"],
    }


def _vivos() -> tuple[dict[str, object], dict[str, object]]:
    cat = {
        "entries": [
            _entrada(_V2, "UT_TratarEscalonamento"),
            _entrada(_V2, "UT_SupervisorAssume"),
            _entrada(_CANCEL, "UT_AnaliseRescisao"),
        ],
        "forms": [],
    }
    adm = {
        "human": {
            "entries": [
                {"process_definition_id": p, "task_definition_key": t, "candidate_groups": ["g"]}
                for p, t in (
                    (_V2, "UT_TratarEscalonamento"),
                    (_V2, "UT_SupervisorAssume"),
                    (_CANCEL, "UT_AnaliseRescisao"),
                )
            ]
        }
    }
    return cat, adm


def test_clona_so_a_versao_admitida_mais_nova_e_mantem_a_anterior() -> None:
    cat, adm = _vivos()
    novo, entradas = montar(cat, adm, _V3, _SHA)
    v3 = [e for e in novo["entries"] if e["process_definition_id"] == _V3]
    assert sorted(e["task_definition_key"] for e in v3) == ["UT_SupervisorAssume", "UT_TratarEscalonamento"]
    assert all(e["process_definition_version"] == "3" and e["process_definition_digest"] == _SHA for e in v3)
    assert all(e["form_key"] == "escalation" and e["required_roles"] == ["escalonamento-tratar"] for e in v3)
    assert len(novo["entries"]) == 5  # as 3 vivas ficam
    assert sorted(e["task_definition_key"] for e in entradas) == [
        "UT_SupervisorAssume",
        "UT_TratarEscalonamento",
    ]
    assert all(e["process_definition_id"] == _V3 and e["candidate_groups"] == ["g"] for e in entradas)
    assert cat["entries"][0]["process_definition_id"] == _V2  # entrada viva nao e alterada


@pytest.mark.parametrize(
    ("pid", "sha"),
    [
        (_V2, _SHA),  # ja no catalogo
        ("SP-OP-ESCALATION-001:1:54460c89-ca4f-11f1-9fd8-02ffe2b01f3f", _SHA),  # mais velha que a admitida
        ("SP-OP-NOVO-001:1:54460c89-ca4f-11f1-9fd8-02ffe2b01f3f", _SHA),  # processo nunca admitido
        (_V3, "xyz"),
        ("nao-e-id", _SHA),
    ],
)
def test_recusa(pid: str, sha: str) -> None:
    cat, adm = _vivos()
    with pytest.raises(SystemExit):
        montar(cat, adm, pid, sha)


def test_admissao_sem_as_tarefas_da_versao_anterior_recusa() -> None:
    cat, adm = _vivos()
    adm["human"]["entries"] = adm["human"]["entries"][:1]  # type: ignore[index]
    with pytest.raises(SystemExit):
        montar(cat, adm, _V3, _SHA)

"""tools/staff_ops/material: recusas e medidas que nao precisam de AWS (DL-0113)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tools.staff_ops import material
from tools.staff_ops.common import OpsError

from maezo.portal.engine.profile import canonicalize


def _entrada(tarefa: str) -> dict[str, Any]:
    return {
        "process_definition_id": "SP-OP-ESCALATION-001:1:x",
        "task_definition_key": tarefa,
        "candidate_groups": ["grupo-a"],
    }


def _saida(base: Path, entradas: int) -> Path:
    (base / "public").mkdir()
    (base / "ops-secrets").mkdir()
    registro = {
        "admission_revision": "7",
        "human": {"entries": [_entrada(f"UT_{i}") for i in range(entradas)]},
    }
    (base / "public" / "portal-read-admission.json").write_bytes(canonicalize(registro))
    (base / "public" / "designation.json").write_bytes(b"d" * 3000)
    (base / "public" / "installation-proof.json").write_bytes(b"p" * 400)
    (base / "ops-secrets" / "job.json").write_bytes(b"j" * 10)
    return base


def test_tamanhos_dos_documentos_assinados_e_dos_segredos(tmp_path: Path) -> None:
    medidas = material.medir(_saida(tmp_path, 1))
    assert medidas["segredo/job.json"] == 10
    assert medidas["mensagem_assinada/designacao"] == 3000
    assert medidas["mensagem_assinada/prova_instalacao"] == 400
    # dominio de assinatura (32 bytes) + o JCS da admissao
    assert medidas["mensagem_assinada/admissao_q2"] > 32


def test_projecao_do_cancel_acrescenta_4_entradas_e_cabe(tmp_path: Path) -> None:
    saida = _saida(tmp_path, 1)
    atual = material.medir(saida)["mensagem_assinada/admissao_q2"]
    projecao = material.projetar_cancel(saida)
    com_cancel = projecao["mensagem_assinada/admissao_q2_com_4_cancel"]
    assert com_cancel > atual
    assert projecao["folga_bytes"] == material.LIMITE_ADMISSAO - com_cancel
    assert projecao["folga_bytes"] > 0


def test_modo_desconhecido_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATERIAL_MODO", "apagar-tudo")
    assert material.main() == 1


def test_sem_modo_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATERIAL_MODO", raising=False)
    assert material.main() == 1


@pytest.mark.parametrize("valor", ["AB", "x", "-abc", "com espaco", "a" * 64, "../raiz"])
def test_identificador_recusa_o_que_nao_e_id(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv("MATERIAL_RAIZ_ID", valor)
    with pytest.raises(OpsError):
        material.identificador("MATERIAL_RAIZ_ID")


def test_identificador_aceita_id_de_raiz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATERIAL_RAIZ_ID", "raiz-20261016")
    assert material.identificador("MATERIAL_RAIZ_ID") == "raiz-20261016"


class _S3Falso:
    def __init__(self, existentes: set[str]) -> None:
        self.existentes = existentes
        self.puts: list[dict[str, Any]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.existentes:
            raise ClientError({"Error": {"Code": "404", "Message": "x"}}, "HeadObject")
        return {}

    def put_object(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)


def test_s3_nunca_sobrescreve_e_grava_com_a_cmk_de_estado(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("botocore")
    chave = "arn:aws:kms:sa-east-1:203312548462:key/00000000-0000-0000-0000-000000000000"
    monkeypatch.setenv("MATERIAL_KMS_KEY_ARN", chave)
    cliente = _S3Falso({"raiz/ja/installation-root.der"})
    with pytest.raises(OpsError, match="ja existe"):
        material._gravar_s3(cliente, "b", "raiz/ja/installation-root.der", b"x")
    assert cliente.puts == []
    material._gravar_s3(cliente, "b", "saida/novo/public/resumo.json", b"{}")
    assert cliente.puts[0]["ServerSideEncryption"] == "aws:kms"
    assert cliente.puts[0]["SSEKMSKeyId"] == chave


def test_argumentos_do_gerar_apontam_para_a_arvore_restaurada(tmp_path: Path) -> None:
    args = material.argumentos_gerar(
        tmp_path,
        sufixo="20261016",
        saida=tmp_path / "s",
        raiz_sha256="a" * 64,
        catalogo=None,
        entradas=None,
    )
    assert args.anterior == tmp_path / "estado"
    assert args.materials == tmp_path / "materials"
    assert args.approver == tmp_path / "aprov"
    assert args.generate_spec is None
    assert args.conexoes == tmp_path / "fontes" / "conexoes-staff.json"
    assert args.nova_raiz_sha256 == "a" * 64


def test_dispatch_conhece_o_comando_material(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.staff_ops.__main__ import main as dispatch

    monkeypatch.delenv("MATERIAL_MODO", raising=False)
    assert dispatch(["material"]) == 1  # recusa (sem MATERIAL_MODO), nao "comando desconhecido" (64)

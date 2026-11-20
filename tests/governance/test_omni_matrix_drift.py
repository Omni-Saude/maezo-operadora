"""E-SF-34r (EP-CORE-12c; CORE-12) — detector de deriva da matriz do portal: nunca promove linha.

Fixtures SINTETICAS escritas aqui (ids `IF-*`/`GAP-*` inventados para o teste; nada do omni e copiado).
A forma (listas `interfaces` e `proposedMissingSources`, chave `id`, `qualification`,
`compatibilityObjective.denominatorIds`) e a fixada pela CD-218.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts import omni_matrix_drift as drift
from scripts.omni_matrix_drift import (
    JANELAS,
    DriftRecusadoError,
    DriftRefusal,
    caminho_de_saida,
    comparar,
    ler_do_clone,
    main,
    serializar,
    sucessor_por_janela,
)

BASE = "a" * 40
ALVO = "b" * 40
_MODULO = Path(drift.__file__)


def _matriz(n_if: int = 3, n_gap: int = 2) -> dict[str, Any]:
    interfaces = [
        {"id": f"IF-{i:02d}", "qualification": "NOT_RUN", "detalhe": f"sintetico {i}"} for i in range(n_if)
    ]
    lacunas = [{"id": f"GAP-{i}", "qualification": "NOT_RUN"} for i in range(n_gap)]
    return {
        "interfaces": interfaces,
        "proposedMissingSources": lacunas,
        "compatibilityObjective": {"denominatorIds": [x["id"] for x in interfaces]},
    }


def _por_id(rel: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {x["id"]: x for x in rel["linhas"]}


# --- nomes do plano -------------------------------------------------------------------------------


def test_drift_report_pins_target_sha() -> None:
    rel = comparar(_matriz(), _matriz(), base_sha=BASE, target_sha=ALVO)
    assert rel["target_sha"] == ALVO
    assert rel["base_sha"] == BASE
    assert ALVO in rel["reproducao"] and BASE in rel["reproducao"]
    for ruim in ("", "b" * 39, "b" * 41, "B" * 40, "g" * 40, "HEAD", "main"):
        with pytest.raises(DriftRecusadoError) as exc:
            comparar(_matriz(), _matriz(), base_sha=BASE, target_sha=ruim)
        assert exc.value.motivo is DriftRefusal.SHA_INVALIDO
        with pytest.raises(DriftRecusadoError) as exc:
            comparar(_matriz(), _matriz(), base_sha=ruim, target_sha=ALVO)
        assert exc.value.motivo is DriftRefusal.SHA_INVALIDO


def test_drift_never_promotes_rows() -> None:
    base = _matriz()
    alvo = copy.deepcopy(base)
    alvo["interfaces"][0]["qualification"] = "QUALIFIED"
    rel = comparar(base, alvo, base_sha=BASE, target_sha=ALVO)
    assert rel["promove_linhas"] is False
    assert _por_id(rel)["IF-00"]["estado"] == "alterada"
    texto = serializar(rel).decode("utf-8")
    assert "qualification" not in texto
    assert "QUALIFIED" not in texto and "NOT_RUN" not in texto
    # as entradas nao sao mutadas pelo detector
    assert base == _matriz()
    assert alvo["interfaces"][0]["qualification"] == "QUALIFIED"


def test_module_never_writes_qualification() -> None:
    """Nenhuma atribuicao ou subscrito de escrita sobre `qualification` no modulo."""
    arvore = ast.parse(_MODULO.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and no.value == "qualification":
            pai_ok = False
            for outro in ast.walk(arvore):
                if (
                    isinstance(outro, ast.Assign)
                    and len(outro.targets) == 1
                    and isinstance(outro.targets[0], ast.Name)
                    and outro.targets[0].id == "CAMPO_QUALIFICACAO"
                    and outro.value is no
                ):
                    pai_ok = True
            assert pai_ok, "literal 'qualification' usado fora da constante declarativa"
    usos = [
        n
        for n in ast.walk(arvore)
        if isinstance(n, ast.Name) and n.id == "CAMPO_QUALIFICACAO" and not isinstance(n.ctx, ast.Store)
    ]
    assert usos == [], "CAMPO_QUALIFICACAO nao pode ser usado (nem lido nem escrito)"


# --- estados por linha ------------------------------------------------------------------------------


def test_states_equal_changed_missing_new() -> None:
    base = _matriz()
    alvo = copy.deepcopy(base)
    alvo["interfaces"][1]["detalhe"] = "mudou"
    removido = alvo["interfaces"].pop(2)
    alvo["compatibilityObjective"]["denominatorIds"].remove(removido["id"])
    alvo["proposedMissingSources"].append({"id": "GAP-9", "qualification": "NOT_RUN"})
    rel = comparar(base, alvo, base_sha=BASE, target_sha=ALVO)
    por = _por_id(rel)
    assert por["IF-00"]["estado"] == "igual"
    assert por["IF-00"]["sha256_base"] == por["IF-00"]["sha256_alvo"]
    assert por["IF-01"]["estado"] == "alterada"
    assert por["IF-01"]["sha256_base"] != por["IF-01"]["sha256_alvo"]
    assert por["IF-02"]["estado"] == "ausente" and por["IF-02"]["sha256_alvo"] is None
    assert por["GAP-9"]["estado"] == "nova" and por["GAP-9"]["sha256_base"] is None
    assert rel["totais"] == {"igual": 3, "alterada": 1, "ausente": 1, "nova": 1}


def test_row_hash_is_sha256_of_canonical_row() -> None:
    m = _matriz()
    rel = comparar(m, m, base_sha=BASE, target_sha=ALVO)
    item = m["interfaces"][0]
    esperado = hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert _por_id(rel)["IF-00"]["sha256_base"] == esperado
    reordenado = copy.deepcopy(m)
    reordenado["interfaces"][0] = dict(reversed(list(item.items())))
    assert comparar(m, reordenado, base_sha=BASE, target_sha=ALVO)["totais"]["alterada"] == 0


def test_reported_ids_match_denominator_ids() -> None:
    m = _matriz(n_if=51, n_gap=8)
    rel = comparar(m, m, base_sha=BASE, target_sha=ALVO)
    ids_interfaces = sorted(x["id"] for x in rel["linhas"] if x["lista_alvo"] == "interfaces")
    assert (
        ids_interfaces == rel["denominador"]["alvo"] == sorted(m["compatibilityObjective"]["denominatorIds"])
    )
    assert len(ids_interfaces) == 51
    assert sum(1 for x in rel["linhas"] if x["lista_alvo"] == "proposedMissingSources") == 8


def test_report_carries_only_ids_and_hashes() -> None:
    rel = comparar(_matriz(), _matriz(), base_sha=BASE, target_sha=ALVO)
    assert "sintetico" not in serializar(rel).decode("utf-8")
    for linha in rel["linhas"]:
        assert set(linha) == {"id", "lista_base", "lista_alvo", "estado", "sha256_base", "sha256_alvo"}


def test_report_is_deterministic() -> None:
    a = serializar(comparar(_matriz(), _matriz(), base_sha=BASE, target_sha=ALVO))
    b = serializar(comparar(_matriz(), _matriz(), base_sha=BASE, target_sha=ALVO))
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


# --- forma da matriz --------------------------------------------------------------------------------


def _sem_id(m: dict[str, Any]) -> None:
    del m["interfaces"][0]["id"]


def _id_vazio(m: dict[str, Any]) -> None:
    m["interfaces"][0]["id"] = ""


def _id_numerico(m: dict[str, Any]) -> None:
    m["proposedMissingSources"][0]["id"] = 7


def _id_duplicado(m: dict[str, Any]) -> None:
    m["proposedMissingSources"][0]["id"] = "IF-00"


def _sem_lista(m: dict[str, Any]) -> None:
    del m["proposedMissingSources"]


def _lista_errada(m: dict[str, Any]) -> None:
    m["interfaces"] = {"IF-00": {}}


def _item_nao_objeto(m: dict[str, Any]) -> None:
    m["interfaces"].append("IF-99")


def _sem_denominador(m: dict[str, Any]) -> None:
    del m["compatibilityObjective"]


def _denominador_fora(m: dict[str, Any]) -> None:
    m["compatibilityObjective"]["denominatorIds"].append("GAP-0")


def _denominador_duplicado(m: dict[str, Any]) -> None:
    m["compatibilityObjective"]["denominatorIds"].append("IF-00")


@pytest.mark.parametrize(
    "estragar",
    [
        _sem_id,
        _id_vazio,
        _id_numerico,
        _id_duplicado,
        _sem_lista,
        _lista_errada,
        _item_nao_objeto,
        _sem_denominador,
        _denominador_fora,
        _denominador_duplicado,
    ],
)
@pytest.mark.parametrize("lado", ["base", "alvo"])
def test_unknown_shape_refuses(estragar: Any, lado: str) -> None:
    ruim = _matriz()
    estragar(ruim)
    base, alvo = (ruim, _matriz()) if lado == "base" else (_matriz(), ruim)
    with pytest.raises(DriftRecusadoError) as exc:
        comparar(base, alvo, base_sha=BASE, target_sha=ALVO)
    assert exc.value.motivo is DriftRefusal.FORMA_DA_MATRIZ_DESCONHECIDA


def test_non_object_matrix_refuses() -> None:
    with pytest.raises(DriftRecusadoError) as exc:
        comparar([], _matriz(), base_sha=BASE, target_sha=ALVO)
    assert exc.value.motivo is DriftRefusal.FORMA_DA_MATRIZ_DESCONHECIDA


# --- estado fechado: sucessor por janela ------------------------------------------------------------


@pytest.mark.parametrize("janela", sorted(JANELAS))
def test_successor_per_window_is_closed(janela: str) -> None:
    with pytest.raises(DriftRecusadoError) as exc:
        sucessor_por_janela(janela)
    assert exc.value.motivo is DriftRefusal.REGISTRO_DE_ADMISSAO_AUSENTE


def test_successor_windows_are_the_planned_ones() -> None:
    assert dict(JANELAS) == {"D-34": ("A1", "A2", "A3"), "D-38": ("A6", "A10")}


def test_successor_unknown_window_refuses() -> None:
    with pytest.raises(DriftRecusadoError) as exc:
        sucessor_por_janela("D-99")
    assert exc.value.motivo is DriftRefusal.JANELA_DESCONHECIDA


def test_report_declares_successor_refused() -> None:
    rel = comparar(_matriz(), _matriz(), base_sha=BASE, target_sha=ALVO)
    assert rel["sucessor"] == {"estado": "RECUSADO", "motivo": "REGISTRO_DE_ADMISSAO_AUSENTE"}


def test_module_has_no_admission_record_reader_nor_flag() -> None:
    texto = _MODULO.read_text(encoding="utf-8")
    arvore = ast.parse(texto)
    importados = {
        a.name.split(".")[0] for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names
    } | {(n.module or "").split(".")[0] for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)}
    assert importados <= {
        "__future__",
        "argparse",
        "hashlib",
        "json",
        "re",
        "subprocess",
        "sys",
        "collections",
        "enum",
        "pathlib",
        "typing",
    }
    for proibido in ("os.environ", "getenv", "ADMISSION-RECORD", "urllib", "socket", "requests", "http"):
        assert proibido not in texto


# --- escrita e leitura ------------------------------------------------------------------------------


def test_output_only_inside_report_dir(tmp_path: Path) -> None:
    certo = Path(drift.RELATORIO_DIR_REL) / f"MATRIX-DRIFT-{ALVO}.json"
    assert caminho_de_saida(certo, ALVO, raiz=tmp_path) == (tmp_path / certo).resolve()
    for ruim in (
        Path(f"MATRIX-DRIFT-{ALVO}.json"),
        Path(drift.RELATORIO_DIR_REL) / f"MATRIX-DRIFT-{BASE}.json",
        Path(drift.RELATORIO_DIR_REL) / "PORTAL-MIRROR-PIN-C6-4.json",
        Path(drift.RELATORIO_DIR_REL) / ".." / f"MATRIX-DRIFT-{ALVO}.json",
        Path("/tmp") / f"MATRIX-DRIFT-{ALVO}.json",
    ):
        with pytest.raises(DriftRecusadoError) as exc:
            caminho_de_saida(ruim, ALVO, raiz=tmp_path)
        assert exc.value.motivo is DriftRefusal.SAIDA_FORA_DO_DIRETORIO


def _clone(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "portal"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    alvo_arq = repo / drift.MATRIZ_PATH
    alvo_arq.parent.mkdir(parents=True)
    alvo_arq.write_text(json.dumps(_matriz()), encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    sha_base = git("rev-parse", "HEAD")
    m = _matriz()
    m["interfaces"][0]["detalhe"] = "mudou"
    alvo_arq.write_text(json.dumps(m), encoding="utf-8")
    git("commit", "-qam", "alvo")
    sha_alvo = git("rev-parse", "HEAD")
    alvo_arq.write_text("nao e json", encoding="utf-8")  # arvore suja: a leitura e so por objeto git
    return repo, sha_base, sha_alvo


def test_reads_clone_by_git_object_only(tmp_path: Path) -> None:
    repo, sha_base, sha_alvo = _clone(tmp_path)
    rel = comparar(
        ler_do_clone(repo, sha_base), ler_do_clone(repo, sha_alvo), base_sha=sha_base, target_sha=sha_alvo
    )
    assert rel["totais"]["alterada"] == 1
    with pytest.raises(DriftRecusadoError) as exc:
        ler_do_clone(repo, "c" * 40)
    assert exc.value.motivo is DriftRefusal.LEITURA_DO_CLONE_FALHOU


def test_cli_stdout_and_out(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: Any) -> None:
    repo, sha_base, sha_alvo = _clone(tmp_path)
    assert main(["--omni-git-dir", str(repo), "--base", sha_base, "--target", sha_alvo]) == 0
    saida = capsys.readouterr().out
    assert json.loads(saida)["target_sha"] == sha_alvo
    raiz = tmp_path / "maezo"
    (raiz / drift.RELATORIO_DIR_REL).mkdir(parents=True)
    monkeypatch.setattr(drift, "REPO_ROOT", raiz)
    destino = raiz / drift.RELATORIO_DIR_REL / f"MATRIX-DRIFT-{sha_alvo}.json"
    rc = main(["--omni-git-dir", str(repo), "--base", sha_base, "--target", sha_alvo, "--out", str(destino)])
    assert rc == 0
    assert destino.read_bytes() == saida.encode("utf-8")
    fora = tmp_path / f"MATRIX-DRIFT-{sha_alvo}.json"
    assert (
        main(["--omni-git-dir", str(repo), "--base", sha_base, "--target", sha_alvo, "--out", str(fora)]) == 2
    )
    assert not fora.exists()


def test_no_drift_report_committed_by_this_line() -> None:
    """GAP G1 (CD-218): o relatorio MATRIX-DRIFT-<sha>.json nao e gerado no Maezo por esta linha."""
    pasta = Path(drift.REPO_ROOT) / drift.RELATORIO_DIR_REL
    assert sorted(p.name for p in pasta.glob("MATRIX-DRIFT-*.json")) == []

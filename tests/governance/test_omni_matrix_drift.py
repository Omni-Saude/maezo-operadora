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
    carregar_matriz,
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


# --- nomes fixados -------------------------------------------------------------------------------


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
    """Nenhuma leitura de VALOR nem escrita de `qualification`; so o teste de presenca `in` e permitido."""
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
    presencas = {
        id(n.left)
        for n in ast.walk(arvore)
        if isinstance(n, ast.Compare)
        and len(n.ops) == 1
        and isinstance(n.ops[0], (ast.In, ast.NotIn))
        and isinstance(n.comparators[0], ast.Name)
    }
    usos = [
        n
        for n in ast.walk(arvore)
        if isinstance(n, ast.Name)
        and n.id == "CAMPO_QUALIFICACAO"
        and not isinstance(n.ctx, ast.Store)
        and id(n) not in presencas
    ]
    assert usos == [], "CAMPO_QUALIFICACAO so pode aparecer em `CAMPO_QUALIFICACAO in item` (presenca)"
    assert len(presencas & {id(n) for n in ast.walk(arvore) if isinstance(n, ast.Name)}) >= 1


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


#: sha256 dos bytes serializados do relatorio da fixture fixa abaixo (calculado uma vez e fixado).
_GOLDEN_SHA256 = "daa5eebaf077bc348b00d72181958035b1a696cb189d0721add5c0e7d69237e6"


def test_report_golden_bytes() -> None:
    base = _matriz()
    alvo = copy.deepcopy(base)
    alvo["interfaces"][1]["detalhe"] = "mudou"
    alvo["proposedMissingSources"].append({"id": "GAP-9", "qualification": "NOT_RUN"})
    dados = serializar(comparar(base, alvo, base_sha=BASE, target_sha=ALVO))
    assert hashlib.sha256(dados).hexdigest() == _GOLDEN_SHA256


def test_row_hash_keeps_non_ascii() -> None:
    """Forma canonica com `ensure_ascii=False` (UTF-8): o hash difere do da forma escapada."""
    m = _matriz()
    m["interfaces"][0]["detalhe"] = "operação de saúde"
    item = m["interfaces"][0]
    utf8 = hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    escapado = hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert utf8 != escapado
    m["proposedMissingSources"].append({"id": "GAP-ação", "qualification": "NOT_RUN"})
    rel = comparar(m, m, base_sha=BASE, target_sha=ALVO)
    assert _por_id(rel)["IF-00"]["sha256_base"] == utf8
    # o relatorio tambem e UTF-8 cru (sem escapes \uXXXX) para ids nao ASCII
    assert "GAP-ação".encode() in serializar(rel)


def test_row_moved_between_lists_is_changed() -> None:
    """Linha com conteudo identico que muda de lista e `alterada` (o estado cobre lista + hash)."""
    base = _matriz()
    alvo = copy.deepcopy(base)
    movida = alvo["interfaces"].pop(2)
    alvo["compatibilityObjective"]["denominatorIds"].remove(movida["id"])
    alvo["proposedMissingSources"].append(movida)
    linha = _por_id(comparar(base, alvo, base_sha=BASE, target_sha=ALVO))["IF-02"]
    assert linha["estado"] == "alterada"
    assert linha["sha256_base"] == linha["sha256_alvo"]
    assert (linha["lista_base"], linha["lista_alvo"]) == ("interfaces", "proposedMissingSources")


def test_denominator_is_sorted() -> None:
    m = _matriz(n_if=5)
    m["compatibilityObjective"]["denominatorIds"] = ["IF-03", "IF-00", "IF-04", "IF-01", "IF-02"]
    rel = comparar(m, m, base_sha=BASE, target_sha=ALVO)
    assert rel["denominador"]["base"] == rel["denominador"]["alvo"] == [f"IF-{i:02d}" for i in range(5)]


def test_rows_sorted_by_id_regardless_of_input_order() -> None:
    m = _matriz(n_if=5, n_gap=3)
    embaralhada = copy.deepcopy(m)
    embaralhada["interfaces"] = [embaralhada["interfaces"][i] for i in (3, 0, 4, 2, 1)]
    embaralhada["proposedMissingSources"].reverse()
    rel = comparar(embaralhada, embaralhada, base_sha=BASE, target_sha=ALVO)
    ids = [x["id"] for x in rel["linhas"]]
    assert ids == sorted(ids)
    assert serializar(rel) == serializar(comparar(m, m, base_sha=BASE, target_sha=ALVO))


# --- forma da matriz --------------------------------------------------------------------------------


def _sem_id(m: dict[str, Any]) -> None:
    del m["interfaces"][0]["id"]


def _id_vazio(m: dict[str, Any]) -> None:
    m["interfaces"][0]["id"] = ""


def _id_numerico(m: dict[str, Any]) -> None:
    m["proposedMissingSources"][0]["id"] = 7


def _id_duplicado(m: dict[str, Any]) -> None:
    m["proposedMissingSources"][0]["id"] = "IF-00"


def _id_duplicado_fora_do_denominador(m: dict[str, Any]) -> None:
    m["proposedMissingSources"][1]["id"] = "GAP-0"


def _id_vazio_fora_do_denominador(m: dict[str, Any]) -> None:
    m["proposedMissingSources"][0]["id"] = ""


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


def _denominador_id_vazio(m: dict[str, Any]) -> None:
    m["compatibilityObjective"]["denominatorIds"].append("")


def _interface_fora_do_denominador(m: dict[str, Any]) -> None:
    m["compatibilityObjective"]["denominatorIds"].remove("IF-01")


def _sem_chave_qualificacao(m: dict[str, Any]) -> None:
    del m["interfaces"][0]["qualification"]


def _lacuna_sem_chave_qualificacao(m: dict[str, Any]) -> None:
    del m["proposedMissingSources"][1]["qualification"]


@pytest.mark.parametrize(
    "estragar",
    [
        _sem_id,
        _id_vazio,
        _id_numerico,
        _id_duplicado,
        _id_duplicado_fora_do_denominador,
        _id_vazio_fora_do_denominador,
        _sem_lista,
        _lista_errada,
        _item_nao_objeto,
        _sem_denominador,
        _denominador_fora,
        _denominador_duplicado,
        _denominador_id_vazio,
        _interface_fora_do_denominador,
        _sem_chave_qualificacao,
        _lacuna_sem_chave_qualificacao,
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


def test_qualification_value_is_not_inspected() -> None:
    """So a presenca da chave conta: qualquer valor (inclusive nulo) passa sem ser lido."""
    m = _matriz()
    m["interfaces"][0]["qualification"] = None
    m["interfaces"][1]["qualification"] = {"qualquer": "coisa"}
    assert comparar(m, m, base_sha=BASE, target_sha=ALVO)["totais"]["igual"] == 5


@pytest.mark.parametrize(
    "texto",
    [
        '{"a": 1, "a": 2}',
        '{"interfaces": [{"id": "IF-00", "id": "IF-01"}]}',
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": -Infinity}',
        '{"x": 1e999}',
        "nao e json",
        "",
    ],
)
def test_strict_json_refuses(texto: str) -> None:
    with pytest.raises(DriftRecusadoError) as exc:
        carregar_matriz(texto.encode("utf-8"))
    assert exc.value.motivo is DriftRefusal.FORMA_DA_MATRIZ_DESCONHECIDA


def test_strict_json_accepts_valid_matrix() -> None:
    m = _matriz()
    m["interfaces"][0]["peso"] = 1.5
    assert carregar_matriz(json.dumps(m).encode("utf-8")) == m


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
        "math",
        "os",
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
    assert caminho_de_saida(certo, ALVO, raiz=tmp_path) == tmp_path / certo
    assert caminho_de_saida(tmp_path / certo, ALVO, raiz=tmp_path) == tmp_path / certo
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


def test_output_refuses_symlinked_report_file(tmp_path: Path) -> None:
    raiz = tmp_path / "maezo"
    pasta = raiz / drift.RELATORIO_DIR_REL
    pasta.mkdir(parents=True)
    fora = tmp_path / "fora.json"
    (pasta / f"MATRIX-DRIFT-{ALVO}.json").symlink_to(fora)
    with pytest.raises(DriftRecusadoError) as exc:
        caminho_de_saida(pasta / f"MATRIX-DRIFT-{ALVO}.json", ALVO, raiz=raiz)
    assert exc.value.motivo is DriftRefusal.SAIDA_FORA_DO_DIRETORIO
    assert not fora.exists()


def test_output_refuses_symlinked_report_dir(tmp_path: Path) -> None:
    raiz = tmp_path / "maezo"
    pasta = raiz / drift.RELATORIO_DIR_REL
    pasta.parent.mkdir(parents=True)
    fora = tmp_path / "fora"
    fora.mkdir()
    pasta.symlink_to(fora, target_is_directory=True)
    with pytest.raises(DriftRecusadoError) as exc:
        caminho_de_saida(pasta / f"MATRIX-DRIFT-{ALVO}.json", ALVO, raiz=raiz)
    assert exc.value.motivo is DriftRefusal.SAIDA_FORA_DO_DIRETORIO
    assert list(fora.iterdir()) == []


def test_output_resolved_parent_guard_alone(tmp_path: Path, monkeypatch: Any) -> None:
    """Defesa em profundidade: mesmo sem a checagem por componente, o pai resolvido fora da raiz recusa."""
    raiz = tmp_path / "maezo"
    pasta = raiz / drift.RELATORIO_DIR_REL
    pasta.parent.mkdir(parents=True)
    fora = tmp_path / "fora"
    fora.mkdir()
    pasta.symlink_to(fora, target_is_directory=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    with pytest.raises(DriftRecusadoError) as exc:
        caminho_de_saida(pasta / f"MATRIX-DRIFT-{ALVO}.json", ALVO, raiz=raiz)
    assert exc.value.motivo is DriftRefusal.SAIDA_FORA_DO_DIRETORIO


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _clone(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "portal"
    repo.mkdir()

    def git(*args: str) -> str:
        return _git(repo, *args)

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


def test_read_refuses_invalid_sha(tmp_path: Path) -> None:
    repo, _, _ = _clone(tmp_path)
    for ruim in ("HEAD", "main", "c" * 39, "C" * 40, "--version"):
        with pytest.raises(DriftRecusadoError) as exc:
            ler_do_clone(repo, ruim)
        assert exc.value.motivo is DriftRefusal.SHA_INVALIDO


def test_read_refuses_invalid_json_in_clone(tmp_path: Path) -> None:
    repo, _, _ = _clone(tmp_path)  # arvore suja com "nao e json"
    _git(repo, "commit", "-qam", "quebrado")
    with pytest.raises(DriftRecusadoError) as exc:
        ler_do_clone(repo, _git(repo, "rev-parse", "HEAD"))
    assert exc.value.motivo is DriftRefusal.FORMA_DA_MATRIZ_DESCONHECIDA


@pytest.mark.parametrize(
    "chave_valor",
    [("extensions.partialClone", "origin"), ("remote.origin.promisor", "true")],
)
def test_read_refuses_partial_clone(tmp_path: Path, chave_valor: tuple[str, str]) -> None:
    repo, sha_base, _ = _clone(tmp_path)
    _git(repo, "config", *chave_valor)
    with pytest.raises(DriftRecusadoError) as exc:
        ler_do_clone(repo, sha_base)
    assert exc.value.motivo is DriftRefusal.CLONE_PARCIAL_RECUSADO


def test_read_refuses_subdirectory_of_clone(tmp_path: Path) -> None:
    repo, sha_base, _ = _clone(tmp_path)
    with pytest.raises(DriftRecusadoError) as exc:
        ler_do_clone(repo / "docs", sha_base)
    assert exc.value.motivo is DriftRefusal.LEITURA_DO_CLONE_FALHOU


def test_read_refuses_bare_repo(tmp_path: Path) -> None:
    repo, sha_base, _ = _clone(tmp_path)
    nu = tmp_path / "nu.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(nu)], check=True, capture_output=True)
    with pytest.raises(DriftRecusadoError) as exc:
        ler_do_clone(nu, sha_base)
    assert exc.value.motivo is DriftRefusal.LEITURA_DO_CLONE_FALHOU


def test_read_refuses_non_commit_object(tmp_path: Path) -> None:
    repo, sha_base, _ = _clone(tmp_path)
    arvore = _git(repo, "rev-parse", f"{sha_base}^{{tree}}")
    blob = _git(repo, "rev-parse", f"{sha_base}:{drift.MATRIZ_PATH}")
    for objeto in (arvore, blob):
        with pytest.raises(DriftRecusadoError) as exc:
            ler_do_clone(repo, objeto)
        assert exc.value.motivo is DriftRefusal.LEITURA_DO_CLONE_FALHOU


def test_read_ignores_caller_git_env(tmp_path: Path, monkeypatch: Any) -> None:
    repo, sha_base, _ = _clone(tmp_path)
    outro = tmp_path / "outro"
    outro.mkdir()
    _git(outro, "init", "-q")
    _git(outro, "commit", "-q", "--allow-empty", "-m", "vazio")
    monkeypatch.setenv("GIT_DIR", str(outro / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outro))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'extensions.partialclone'='origin'")
    assert ler_do_clone(repo, sha_base) == _matriz()


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


def test_cli_write_failure_is_typed_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: Any
) -> None:
    repo, sha_base, sha_alvo = _clone(tmp_path)
    raiz = tmp_path / "maezo"
    raiz.mkdir()
    monkeypatch.setattr(drift, "REPO_ROOT", raiz)
    destino = raiz / drift.RELATORIO_DIR_REL / f"MATRIX-DRIFT-{sha_alvo}.json"
    argv = ["--omni-git-dir", str(repo), "--base", sha_base, "--target", sha_alvo, "--out", str(destino)]
    assert main(argv) == 2  # portal-mirror ausente
    erro = capsys.readouterr().err
    assert erro.startswith("ESCRITA_DA_SAIDA_FALHOU") and "Traceback" not in erro
    destino.mkdir(parents=True)  # destino e diretorio: OSError na escrita
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("ESCRITA_DA_SAIDA_FALHOU")


def test_no_drift_report_committed_by_this_line() -> None:
    """GAP G1 (CD-218): o relatorio MATRIX-DRIFT-<sha>.json nao e gerado no Maezo por esta linha."""
    pasta = Path(drift.REPO_ROOT) / drift.RELATORIO_DIR_REL
    assert sorted(p.name for p in pasta.glob("MATRIX-DRIFT-*.json")) == []

"""E-SF-11 / EP-REG-21a (REG-21): nenhum sucesso de direito do titular (DSR) fabricado, nos dois lados.

Lado do motor: o worker `operadora.lgpd.execute_request` não tem caminho que devolva resultado; toda
entrada termina em recusa (incidente, `retries_left=0`) ou na eliminação diferida por legal-hold. Assim,
`Flow_Executar_Enviar` nunca é atravessado e nenhuma instância chega a `event_desfecho=atendida` por
uma execução que não aconteceu.

Lado do comprador/titular: hoje não existe superfície DSR no gateway comercial nem no contrato C6-4. O
único canal é o portal do titular (`POST /dsr`, DARK), cujo recibo é de ESTÁGIO ("received"). Estas
cercas fixam que nenhum schema, rota ou contrato publica um desfecho de DSR. A forma "o adapter devolve
`CONTRACT_PENDING`" depende de uma superfície DSR que ainda não existe (EP-REG-21d, bloqueada: freeze
sucessor do C6-4 e A2); quando ela nascer, esta cerca falha e a guarda de adapter entra com ela.

Habilitar algum direito (EP-REG-21c) e o canal humano do encarregado (EP-REG-21b) ficam fora: dependem
do registro do DPO, da matriz de retenção, do legal-hold e de ato do dono.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, get_args

import pytest

from maezo.platform.lifecycle.legal_hold import LegalHoldDecision, LegalHoldOutcome
from maezo.portal.contracts.dsr import DsrReceipt
from maezo.tools.workers import lgpd as lgpd_module
from maezo.tools.workers.harness import WorkerFailureError
from maezo.tools.workers.lgpd import ExecuteRequestWorker, LegalHoldErasureDeferredError

_ROOT = Path(__file__).resolve().parents[3]
_OPENAPI = _ROOT / "src/maezo/portal/web/openapi.json"
_C6_4 = _ROOT / "tests/candidate_artifacts/commercial/contract-c6-4.json"

#: Qualquer palavra que afirme desfecho de DSR. Nenhuma pode ser estado/valor publicado ao titular.
_DESFECHO_RE = re.compile(
    r"(?i)\b(atendid[ao]s?|conclu[ií]d[ao]s?|realizad[ao]s?|processad[ao]s?|efetivad[ao]s?|executad[ao]s?|"
    r"respondid[ao]s?|resolvid[ao]s?|encerrad[ao]s?|deferid[ao]s?|eliminad[ao]s?|exportad[ao]s?|"
    r"corrigid[ao]s?|fulfill?(?:ed|ment)?|done|completed?|finished|resolved|closed|granted|answered|"
    r"deleted|erased|exported|rectified|succe(?:ss|ssful|eded))\b"
)


def _afirma_desfecho(texto: str) -> re.Match[str] | None:
    """Procura desfecho depois de separar camelCase e trocar `_`, `-` e `.` por espaço, para que
    `request_fulfilled`, `dsrCompleted` e `status.atendida` também casem."""
    separado = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", texto)
    return _DESFECHO_RE.search(re.sub(r"[_\-.]", " ", separado))


#: Nome de comando que serviria a um direito do titular.
_COMANDO_DE_DIREITO = re.compile(
    r"(?i)privacy|dsr|titular|data[_-]?subject|lgpd|erasure|consent|portab|rights|personal[_-]?data|anonymi"
)
#: Estados terminais que um worker nunca pode devolver.
_ESTADOS_TERMINAIS = ("execution_completed", "atendida", "concluida", "completed", "done")

_TIPOS: tuple[object, ...] = (
    *sorted(lgpd_module._TIPO_REQUISICAO_PARA_DIREITO),
    "",
    "tipo_que_o_modelo_nao_declara",
    ["eliminacao"],
    {"tipo": "eliminacao"},
)
_DECISOES = ("EXECUTAR_E_ENVIAR", "NEGAR_FUNDAMENTADO", "APROVAR_ENVIO", "")
_HUMANOS: tuple[dict[str, object], ...] = (
    {},
    {"human_approved": None},
    {"human_approved": False},
    {"human_approved": "true"},
    {"human_approved": " "},
    {"human_approved": 1},
    {"human_approved": [1]},
    {"human_approved": {"a": 1}},
    {"human_approved": True},
)


class _Avaliador:
    def __init__(self, outcome: LegalHoldOutcome) -> None:
        self.decision = LegalHoldDecision(outcome, "sintetico", registro_sha256="a" * 64)

    def __call__(self, **_: object) -> LegalHoldDecision:
        return self.decision


def _workers() -> list[ExecuteRequestWorker]:
    """O worker como está registrado hoje e com o legal-hold ligado, em cada desfecho do registro."""
    workers = [ExecuteRequestWorker()]
    workers += [
        ExecuteRequestWorker(legal_hold_predicate=True, legal_hold_evaluator=_Avaliador(outcome))
        for outcome in LegalHoldOutcome
    ]
    return workers


# --- lado do motor -------------------------------------------------------------------------------


def test_execute_request_no_path_completes() -> None:
    exercidos = 0
    for worker in _workers():
        for tipo in _TIPOS:
            for decisao in _DECISOES:
                for humano in _HUMANOS:
                    process_vars: dict[str, object] = {
                        "tenant_id": "amh",
                        "titular_pseudo_id": "pseudo-abc",
                        "tipo_requisicao": tipo,
                        "decisao_dsr": decisao,
                        **humano,
                    }
                    try:
                        devolvido: Any = worker.run(process_vars)
                    except WorkerFailureError as exc:
                        assert exc.retries_left == 0, (tipo, decisao, humano)
                    except LegalHoldErasureDeferredError:
                        pass  # eliminação diferida (legal-hold ativo ou duvidoso): recusa auditada
                    else:
                        pytest.fail(
                            f"execute_request devolveu {devolvido!r} para {tipo!r}/{decisao!r}/{humano!r}"
                        )
                    exercidos += 1
    assert exercidos == len(_workers()) * len(_TIPOS) * len(_DECISOES) * len(_HUMANOS)


def test_execute_request_nao_tem_return_nem_estado_terminal() -> None:
    """Prova estrutural: o corpo de `execute` não tem `return` e nenhum literal de estado terminal."""
    fonte = Path(lgpd_module.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    classe = next(n for n in arvore.body if isinstance(n, ast.ClassDef) and n.name == "ExecuteRequestWorker")
    execute = next(n for n in classe.body if isinstance(n, ast.FunctionDef) and n.name == "execute")
    corpo = list(_sem_funcoes_aninhadas(execute))
    assert not [n for n in corpo if isinstance(n, ast.Return)]
    literais = {n.value for n in corpo if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not literais & set(_ESTADOS_TERMINAIS)
    assert [n for n in corpo if isinstance(n, ast.Raise)], "controle: o corpo recusa"


def _sem_funcoes_aninhadas(funcao: ast.FunctionDef) -> list[ast.AST]:
    """Os nós do corpo de `funcao`, sem descer em `def`/`lambda` aninhados (o `return` deles é deles)."""
    vistos: list[ast.AST] = []
    pilha: list[ast.AST] = list(funcao.body)
    while pilha:
        no = pilha.pop()
        vistos.append(no)
        if isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        pilha.extend(ast.iter_child_nodes(no))
    return vistos


# --- lado do comprador/titular -------------------------------------------------------------------


def test_buyer_dsr_view_never_fulfilled_without_source() -> None:
    # 1) O recibo do portal é de estágio: um único status, "received", e nenhum campo de desfecho.
    status = DsrReceipt.model_fields["status"].annotation
    assert get_args(status) == ("received",)
    assert not [campo for campo in DsrReceipt.model_fields if _afirma_desfecho(campo)]

    # 2) O OpenAPI publicado: todo schema de DSR sem campo, enum ou const de desfecho; o recibo só "received".
    openapi = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    schemas = openapi["components"]["schemas"]
    dsr = {nome: schema for nome, schema in schemas.items() if "dsr" in nome.lower()}
    assert {"DsrReceipt", "DsrSubmission", "PortalDsrError"} <= set(dsr), (
        "controle: os schemas de DSR existem"
    )
    for nome, schema in dsr.items():
        for chave, valor in _folhas(schema):
            if chave in {"description", "title", "pattern"}:
                continue
            assert not _afirma_desfecho(str(valor)), f"{nome}: {chave}={valor!r}"
        assert not [p for p in schema.get("properties", {}) if _afirma_desfecho(p)], nome
    assert schemas["DsrReceipt"]["properties"]["status"]["const"] == "received"

    # 3) Nenhuma rota de consulta de status de DSR: o único caminho é o POST de recebimento.
    rotas = {path: set(ops) for path, ops in openapi["paths"].items() if "dsr" in path.lower()}
    assert rotas, "controle: a rota de recebimento existe"
    assert all(ops <= {"post"} for ops in rotas.values()), rotas

    # 4) Nenhum contrato comercial candidato (o C6-4 e qualquer sucessor `contract-*.json`) tem comando
    # de direito do titular (só leitura; os arquivos são congelados).
    contratos = sorted(_C6_4.parent.glob("contract-*.json"))
    assert _C6_4 in contratos
    for contrato in contratos:
        comandos = _comandos_do_contrato(json.loads(contrato.read_text(encoding="utf-8")))
        assert len(comandos) >= 30, f"controle: a varredura acha os comandos de {contrato.name}"
        assert not [c for c in comandos if _COMANDO_DE_DIREITO.search(c)], contrato.name


def test_o_canal_dsr_do_portal_continua_escuro() -> None:
    """CD-206: o `POST /dsr` segue DARK por padrão; este teste não o liga, só lê o padrão."""
    from maezo.portal.api.config import PortalSettings

    assert PortalSettings.model_fields["lgpd_dsr_channel"].default is False
    # Nenhum arquivo de `deploy/` ou `config/` sequer menciona a chave: qualquer forma de ligá-la
    # (`X=true`, `name = "X", value = "true"` do Terraform/ECS, `- name: X` do k8s, `.hcl`, `.example`)
    # passa por citá-la, e hoje não há nenhuma citação.
    assert _mencoes_da_chave() == []


def _mencoes_da_chave(raizes: tuple[Path, ...] = (_ROOT / "deploy", _ROOT / "config")) -> list[str]:
    return [
        path.as_posix()
        for raiz in raizes
        for path in sorted(raiz.rglob("*"))
        if path.is_file()
        and re.search(r"(?i)LGPD_DSR_CHANNEL", path.read_text(encoding="utf-8", errors="ignore"))
    ]


def test_os_modulos_de_dsr_do_portal_nao_publicam_desfecho() -> None:
    modulos = [_ROOT / "src/maezo/portal/api/dsr.py", _ROOT / "src/maezo/portal/contracts/dsr.py"]
    for path in modulos:
        arvore = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(arvore)
        literais = [
            n.value
            for n in ast.walk(arvore)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
        ]
        assert literais, f"controle: {path.name} tem literais"
        achados = [lit for lit in literais if _afirma_desfecho(lit)]
        assert not achados, f"{path.name}: {achados}"


def test_os_detectores_nao_sao_vacuos(tmp_path: Path) -> None:
    tf = tmp_path / "service.tf"
    tf.write_text(
        'environment = [{ name = "MAEZO_PORTAL_LGPD_DSR_CHANNEL", value = "true" }]', encoding="utf-8"
    )
    assert _mencoes_da_chave((tmp_path,)) == [tf.as_posix()]
    aninhada = ast.parse(
        "def execute(self):\n    def ajuda():\n        return 1\n    raise ValueError\n"
    ).body[0]
    assert isinstance(aninhada, ast.FunctionDef)
    assert not [n for n in _sem_funcoes_aninhadas(aninhada) if isinstance(n, ast.Return)]
    for frase in ("Pedido atendido", "fulfilled", "execution_completed", "Dados eliminados", "concluída"):
        assert _afirma_desfecho(frase), frase
    assert not _afirma_desfecho("received")
    for valor in (
        "request_fulfilled",
        "dsrCompleted",
        "DSRCompleted",
        "fulfilment",
        "fulfilled_at",
        "status.atendida",
        "Pedido realizado",
    ):
        assert _afirma_desfecho(valor), valor
    for valor in ("human_approved", "received_at", "request_ref", "tipo_requisicao", "schema_version"):
        assert not _afirma_desfecho(valor), valor
    falso = {"properties": {"status": {"enum": ["received", "atendida"]}}}
    assert [v for k, v in _folhas(falso) if k != "description" and _afirma_desfecho(str(v))]
    assert _comandos_do_contrato({"x": {"command": {"enum": ["privacy.request"]}}}) == {"privacy.request"}


def _folhas(no: Any, chave: str = "") -> list[tuple[str, Any]]:
    if isinstance(no, dict):
        return [folha for k, v in no.items() for folha in _folhas(v, str(k))]
    if isinstance(no, list):
        return [folha for item in no for folha in _folhas(item, chave)]
    return [(chave, no)]


def _comandos_do_contrato(contrato: Any) -> set[str]:
    comandos: set[str] = set()
    for chave, valor in _folhas(contrato):
        if (
            chave in {"command", "enum", "const"}
            and isinstance(valor, str)
            and re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", valor)
        ):
            comandos.add(valor)
    return comandos


def _docstrings(arvore: ast.AST) -> set[int]:
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and no.body:
            primeiro = no.body[0]
            if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant):
                ids.add(id(primeiro.value))
    return ids

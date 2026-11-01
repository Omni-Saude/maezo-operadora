"""R-061 (decisao do dono 2026-10-31, #1085) — prova de que o collector OTel descarta PHI.

O dono liberou o egress OTLP de TODOS os pods, inclusive os da Zona PHI (`allow-otel-egress`,
PR #1098), COM a condicao de um teste que prove o scrub de PHI no collector. Este arquivo e' essa
prova, e avalia a configuracao REAL — nao uma copia dela:

1. renderiza `templates/configmap-otel-collector.yaml` com o `helm template` real (overlays
   staging e amh, `observability.collector.enabled=true`) e carrega o `config.yaml` embutido;
2. exige que TODO pipeline com receiver `otlp` (traces, logs E metricas) rode o processor
   `transform/phi_safety_net`, e que ele cubra os contextos resource, span, spanevent, log e
   datapoint (scope, status.message, nome de span event e span links NAO cobertos) mais o nome do
   span e o corpo do log;
3. EXECUTA as declaracoes OTTL desse processor sobre telemetria sintetica com PHI representativa
   (CPF, CNS, nome, ids/refs FHIR, wamid/wa_id, telefone, e-mail, texto livre) e afirma que
   nenhuma chave PHI sobrevive e que nenhum valor PHI aparece em lugar nenhum da saida.

POR QUE UM INTERPRETADOR DO SUBCONJUNTO OTTL, E POR QUE ELE E' FIEL. O binario do collector nao
existe no runner de testes unitarios (nem em CI), entao o passo 3 interpreta as declaracoes com
a semantica documentada das funcoes OTTL usadas — e SO delas:

- `delete_key(attributes, "k")` — remove a chave exata;
- `delete_matching_keys(attributes, "re")` — remove toda chave em que o regex CASA EM QUALQUER
  POSICAO (Go `regexp.MatchString` == Python `re.search`);
- `replace_all_patterns(attributes, "value", "re", "repl")` — substitui todas as ocorrencias nos
  valores string (Go `ReplaceAllString` == Python `re.sub`);
- `replace_pattern(<name|body>, "re", "repl")` — idem, num unico campo string.

FAIL-CLOSED: qualquer funcao fora dessa lista, qualquer clausula `where`, qualquer escape de
string OTTL alem de `\\\\`/`\\"`, qualquer regex com construcao em que Go RE2 e Python divergem
(lookaround, backreference, `\\d`/`\\s`/`\\w` — Unicode em Python, ASCII em RE2 —, `$` escapado ou
dentro de classe, ancoras
de inicio/fim de texto, propriedades Unicode e quoting, classes POSIX `[[:`, comentario `(?#`
e flags m/s/x/U) e qualquer
replacement com `$`/`\\` (expansao de grupo difere) FAZEM O TESTE FALHAR em vez de serem
ignorados. Divergencias TRATADAS (nao recusadas): `\\b`/`\\B` (o interpretador compila com
re.ASCII, igual ao RE2) e `$` (vira fim-de-texto, como no RE2 sem flag m — o `$` do Python tambem
casaria antes de um newline final). Assim, se alguem mudar o processor para algo que este
interpretador nao reproduz fielmente, a prova quebra em vermelho — nunca passa em silencio.

Validacao cruzada local (fora do CI, registrada no PR): as mesmas declaracoes foram executadas no
`transformprocessor` REAL do opentelemetry-collector-contrib (harness Go) sobre o mesmo corpus,
com saida identica a deste interpretador SOBRE O CORPUS DESTE ARQUIVO (evidencia fora do repo, no PR;
nao e' prova de equivalencia geral).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway.log_scrubber import PhiLogRedactor
from maezo.gateway.pseudonymizer import PHI_FIELDS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_OVERLAYS: tuple[str, ...] = ("values-staging.yaml", "values-amh.yaml")
_SCRUB = "transform/phi_safety_net"

#: Contextos OTTL que carregam `attributes` e que o scrub TEM de cobrir, por tipo de sinal.
_REQUIRED_ATTRIBUTE_CONTEXTS: dict[str, frozenset[str]] = {
    "trace_statements": frozenset({"resource", "span", "spanevent"}),
    "log_statements": frozenset({"resource", "log"}),
    "metric_statements": frozenset({"resource", "datapoint"}),
}
_SIGNAL_OF_PIPELINE: dict[str, str] = {
    "traces": "trace_statements",
    "logs": "log_statements",
    "metrics": "metric_statements",
}


# ---------------------------------------------------------------------------
# Config real
# ---------------------------------------------------------------------------


def _render_collector_config(overlay: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "helm",
            "template",
            "test-release",
            str(_CHART_DIR),
            "-f",
            str(_CHART_DIR / overlay),
            "--set",
            "observability.collector.enabled=true",
            "--show-only",
            "templates/configmap-otel-collector.yaml",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    configmap = yaml.safe_load(result.stdout)
    config = yaml.safe_load(configmap["data"]["config.yaml"])
    assert isinstance(config, dict)
    return config


@pytest.fixture(scope="module", params=_OVERLAYS)
def collector_config(request: pytest.FixtureRequest) -> dict[str, Any]:
    return _render_collector_config(request.param)


# ---------------------------------------------------------------------------
# Interpretador fail-closed do subconjunto OTTL
# ---------------------------------------------------------------------------

_STATEMENT_RE = re.compile(r"^([a-z_]+)\((.*)\)$")
#: Construcoes em que Go RE2 e Python `re` divergem (ou que RE2 nem aceita).
_DIVERGENT_REGEX = re.compile(r"\(\?[=!<P#]|\\[1-9]|\\[dDsSwWAZzGpPQEC]|\[\[:|\(\?[a-zA-Z]*[msxU]")


class UnsupportedOttlError(AssertionError):
    """Declaracao fora do subconjunto que este interpretador reproduz fielmente."""


def _tokenize_args(raw: str) -> list[str | tuple[str]]:
    """Divide os argumentos: string OTTL -> str desescapada; path -> tupla (nome,)."""
    args: list[str | tuple[str]] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch in " ,":
            i += 1
            continue
        if ch == '"':
            out: list[str] = []
            i += 1
            while True:
                if i >= len(raw):
                    raise UnsupportedOttlError(f"string OTTL sem fechamento: {raw!r}")
                c = raw[i]
                if c == "\\":
                    nxt = raw[i + 1] if i + 1 < len(raw) else ""
                    if nxt not in ('"', "\\"):
                        raise UnsupportedOttlError(f"escape OTTL fora do subconjunto: \\{nxt} em {raw!r}")
                    out.append(nxt)
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                out.append(c)
                i += 1
            args.append("".join(out))
            continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", raw[i:])
        if match is None:
            raise UnsupportedOttlError(f"argumento OTTL nao reconhecido em {raw!r}")
        args.append((match.group(0),))
        i += match.end()
    return args


def _checked_regex(pattern: str) -> re.Pattern[str]:
    if _DIVERGENT_REGEX.search(pattern) or "\\$" in pattern or re.search(r"\[[^\]]*\$", pattern):
        raise UnsupportedOttlError(f"regex com construcao divergente Go RE2 x Python: {pattern!r}")
    # Go RE2 sem flag m: `$` casa SO no fim do texto (== Python `\Z`; Python `$` tambem casa antes
    # de um `\n` final). `\b`/`\B`/`(?i)` em RE2 sao ASCII-ou-mais-largos: re.ASCII deixa o Python
    # igual (\b) ou mais estreito ((?i)) que o Go — nunca mais largo.
    return re.compile(pattern.replace("$", r"\Z"), re.ASCII)


def _checked_replacement(repl: str) -> str:
    if "$" in repl or "\\" in repl:
        raise UnsupportedOttlError(f"replacement com expansao de grupo (Go `$` x Python `\\`): {repl!r}")
    return repl


Signal = dict[str, Any]  # {"attributes": {...}, "name": str?, "body": str?}


def compile_statement(statement: str, context: str) -> Callable[[Signal], None]:
    """Compila UMA declaracao OTTL do subconjunto; qualquer coisa fora dele levanta."""
    if " where " in f" {statement} ":
        raise UnsupportedOttlError(f"clausula where nao interpretada: {statement!r}")
    match = _STATEMENT_RE.match(statement.strip())
    if match is None:
        raise UnsupportedOttlError(f"declaracao OTTL nao reconhecida: {statement!r}")
    func, args = match.group(1), _tokenize_args(match.group(2))

    if func in ("delete_key", "delete_matching_keys", "replace_all_patterns") and (
        not args or args[0] != ("attributes",)
    ):
        raise UnsupportedOttlError(f"{func} so e' interpretado sobre `attributes`: {statement!r}")
    if func == "delete_key" and len(args) == 2 and isinstance(args[1], str):
        key = args[1]

        def _delete_exact(sig: Signal) -> None:
            sig["attributes"].pop(key, None)

        return _delete_exact
    if func == "delete_matching_keys" and len(args) == 2 and isinstance(args[1], str):
        rx = _checked_regex(args[1])

        def _delete(sig: Signal) -> None:
            for k in [k for k in sig["attributes"] if rx.search(k)]:
                del sig["attributes"][k]

        return _delete
    if func == "replace_all_patterns" and len(args) == 4 and all(isinstance(a, str) for a in args[1:]):
        mode, rx_raw, repl_raw = args[1], args[2], args[3]
        assert isinstance(mode, str) and isinstance(rx_raw, str) and isinstance(repl_raw, str)
        if mode != "value":
            raise UnsupportedOttlError(f"replace_all_patterns modo {mode!r} nao interpretado")
        rx, repl = _checked_regex(rx_raw), _checked_replacement(repl_raw)

        def _replace_all(sig: Signal) -> None:
            for k, v in sig["attributes"].items():
                if isinstance(v, str):
                    sig["attributes"][k] = rx.sub(repl, v)

        return _replace_all
    if func == "replace_pattern" and len(args) == 3 and isinstance(args[0], tuple):
        field = args[0][0]
        allowed = {"span": "name", "log": "body"}
        if allowed.get(context) != field:
            raise UnsupportedOttlError(f"replace_pattern({field}) no contexto {context!r} nao interpretado")
        rx_raw, repl_raw = args[1], args[2]
        assert isinstance(rx_raw, str) and isinstance(repl_raw, str)
        rx, repl = _checked_regex(rx_raw), _checked_replacement(repl_raw)

        def _replace_field(sig: Signal) -> None:
            if isinstance(sig.get(field), str):
                sig[field] = rx.sub(repl, sig[field])

        return _replace_field
    raise UnsupportedOttlError(f"funcao/aridade OTTL fora do subconjunto: {statement!r}")


def _scrub_processor(config: dict[str, Any]) -> dict[str, Any]:
    proc = config["processors"][_SCRUB]
    assert proc.get("error_mode") == "ignore", "o scrub nao pode derrubar o pipeline (error_mode)"
    return proc


def apply_scrub(config: dict[str, Any], telemetry: dict[str, Signal]) -> dict[str, Signal]:
    """Aplica TODAS as declaracoes do processor, por sinal e contexto, numa copia da telemetria.

    `telemetry` mapeia contexto -> sinal; os contextos `resource` de cada sinal sao separados
    (`trace.resource`, `log.resource`, `metric.resource`) porque cada pipeline tem o seu.
    """
    out = json.loads(json.dumps(telemetry))
    prefix = {"trace_statements": "trace", "log_statements": "log", "metric_statements": "metric"}
    for group, groups in _scrub_processor(config).items():
        if group == "error_mode":
            continue
        if group not in prefix:
            raise UnsupportedOttlError(f"grupo de declaracoes nao interpretado: {group!r}")
        for block in groups:
            context = block["context"]
            target = out[f"{prefix[group]}.{context}"]
            for statement in block["statements"]:
                compile_statement(statement, context)(target)
    return out


# ---------------------------------------------------------------------------
# Corpus sintetico (dados FICTICIOS; CPF/CNS com DV valido mas inexistentes)
# ---------------------------------------------------------------------------

#: familia -> (chaves PHI, valores PHI que NAO podem aparecer na saida)
_PHI_KEY_FAMILIES: dict[str, dict[str, str]] = {
    "business_key": {
        "business_key": "CANCEL-amh-0001234",
        "_business_key": "INAD-amh-123456789",
        "cancel_business_key": "CANCEL-amh-0001235",
    },
    "cpf": {
        "cpf": "529.982.247-25",
        "beneficiario.cpf": "52998224725",
        "patient_cpf": "111.444.777-35",
        "beneficiarioCpf": "390.533.447-05",
        "cartaoCns": "700000000000005",
        "cns": "898001160123456",
    },
    "nome": {
        "nome": "Maria Ficticia da Silva",
        "beneficiario.nome": "Joana Exemplo",
        "nome_social": "Ana Teste",
        "nomeCompleto": "Carla Sintetica",
        "beneficiarioNome": "Beatriz Modelo",
        "patient.name": "John Synthetic Doe",
        "user.name": "jdoe.synthetic",
        "full_name": "Jane Fictional Roe",
    },
    "fhir_id": {
        "fhir.patient.id": "pat-0001-synth",
        "fhir_id": "res-9f8e7d-synth",
        "fhirResourceId": "obs-4242-synth",
        "patient_id": "p-77-synth",
        "patientId": "p-78-synth",
        "beneficiario_id": "ben-55-synth",
        "subject.reference": "subj-ref-synth",
    },
    "wamid": {
        "wamid": "wamid.HBgNNTUxMTk4ODg4Nzc3NxUCABIYFjNFQjBDMEY=",
        "whatsapp.message_id": "wamid.HBgNNTUyMTk3Nzc3NjY2NRUCABEYEjQ=",
        "wa_id": "5511988887777",
        "whatsapp.from": "5521977776666",
    },
    "telefone": {
        "telefone": "(11) 98888-7777",
        "beneficiario.telefone": "+55 21 97777-6666",
        "phone_number": "11 3333-4444",
        "celular": "11999990000",
        "msisdn": "5531966665555",
        "userPhone": "11 2222-3333",
    },
    "email": {
        "email": "maria.ficticia@example.com",
        "beneficiario.e-mail": "joana+teste@exemplo.com.br",
        "userEmail": "jdoe@synthetic.test",
    },
    "texto_livre": {
        "message.content": "Sou a Maria, meu exame deu positivo",
        "request.body": '{"nome": "Joana"}',
        "http.request.body": "paciente com dor toracica",
        "llm.prompt": "Resuma o caso da paciente Joana",
        "llm.completion": "A paciente Joana apresenta",
        "input.value": "diagnostico preliminar",
        "output.value": "conduta sugerida",
        "mensagem": "quero cancelar meu plano",
        "texto": "laudo anexo",
        "transcript": "ola, aqui e' a Maria",
        "db.query.text": "SELECT * FROM beneficiario WHERE nome = 'Maria'",
        "user_message": "minha filha esta' internada",
    },
}

#: chave NEUTRA (nao-PHI pelo nome) -> valor com PHI embutida -> marcador esperado.
_PHI_VALUE_PATTERNS: dict[str, tuple[str, str, str]] = {
    "cpf_mascarado": ("error.detail", "beneficiario 529.982.247-25 sem carencia", "529.982.247-25"),
    "cpf_cru": ("lookup.key", "chave=52998224725", "52998224725"),
    "email": ("error.detail", "falha ao notificar maria.ficticia@example.com", "maria.ficticia@example.com"),
    "telefone": ("error.detail", "destino +55 11 98888-7777 recusado", "98888-7777"),
    "wamid": ("messaging.message.id", "wamid.HBgNNTUxMTk4ODg4Nzc3NxUCABIYFjNFQjBDMEY=", "HBgNNTUxMTk4ODg4"),
    "fhir_ref": ("http.url", "https://hapi.svc/fhir/Patient/pat-0001-synth/_history/2", "pat-0001-synth"),
    "cns_cru": ("lookup.key", "cns 898001160123456", "898001160123456"[:5]),
    "cpf_espacos": ("error.detail", "cpf 529 982 247 25", "529 982"),
    "cnpj_mascarado": ("error.detail", "cnpj 11.222.333/0001-81", "11.222.333"),
    "celular_sem_ddd": ("error.detail", "ligar 98888-7777", "98888"),
    "cpf_pontos": ("error.detail", "cpf 529.982.247.25", "982.247"),
    "cpf_misto": ("error.detail", "cpf 529-982-247.25", "982-247"),
    "fixo_ddd_pontos": ("error.detail", "ligar 11.3333.4444", "3333"),
    "email_dominio_sublinhado": ("error.detail", "a@my_host.com", "my_host"),
    "email_percent": ("error.detail", "a%b@x.com", "a%"),
    # 12-13 digitos e a cauda do CNS cru: [0-9]{11,} roda antes do CNPJ (nada de prefixo/sufixo vazado)
    "digitos_12": ("lookup.key", "mat 123456789012", "12345"),
    "cns_cru_cauda": ("lookup.key", "cns 898001160123456", "]6"),
}

#: atributos operacionais que o scrub NAO pode apagar nem alterar (sem over-redaction cega).
_OPERATIONAL: dict[str, str] = {
    "service.name": "agent-runtime",
    "service.namespace": "maezo",
    "k8s.pod.name": "agent-runtime-7c9d-xk2lp",
    "k8s.namespace.name": "maezo-amh",
    "host.name": "ip-10-0-1-23",
    "http.route": "/v1/guias/{guia_id}",
    "http.method": "POST",
    "messaging.destination.name": "operadora.notifications.internal",
    "maezo.tenant": "amh",
    "maezo.agent_id": "rafael",
    "maezo.process_key": "SP-OP-AUTH-001",
    "beneficiario.pseudo_id": "hk1_3f9a1c0b7e2d4a6f",
    "error.type": "TimeoutError",
}

_ALL_CONTEXTS: tuple[str, ...] = (
    "trace.resource",
    "trace.span",
    "trace.spanevent",
    "log.resource",
    "log.log",
    "metric.resource",
    "metric.datapoint",
)


def _telemetry_with(attributes: dict[str, str], *, free_text: str = "") -> dict[str, Signal]:
    telemetry: dict[str, Signal] = {ctx: {"attributes": dict(attributes)} for ctx in _ALL_CONTEXTS}
    telemetry["trace.span"]["name"] = free_text or "GET /v1/guias/{guia_id}"
    telemetry["log.log"]["body"] = free_text or "guia recebida"
    return telemetry


# ---------------------------------------------------------------------------
# 1. Cobertura estrutural: todo pipeline OTLP passa pelo scrub, em todo contexto
# ---------------------------------------------------------------------------


def test_every_otlp_pipeline_runs_the_phi_scrub(collector_config: dict[str, Any]) -> None:
    pipelines = collector_config["service"]["pipelines"]
    otlp_pipelines = {name: p for name, p in pipelines.items() if "otlp" in p.get("receivers", [])}
    # traces, logs E metricas: a revisao R-061-OTEL-PHI-EGRESS apontou metricas sem scrub.
    assert {name.split("/")[0] for name in otlp_pipelines} == {"traces", "logs", "metrics"}
    for name, pipeline in otlp_pipelines.items():
        processors = pipeline["processors"]
        assert _SCRUB in processors, f"pipeline {name} recebe OTLP sem {_SCRUB}"
        # Antes do batch/export: nada sai do collector sem ter passado pelo scrub.
        assert processors.index(_SCRUB) < processors.index("batch"), name


def test_scrub_covers_every_attribute_context_of_every_otlp_signal(collector_config: dict[str, Any]) -> None:
    proc = _scrub_processor(collector_config)
    for pipeline_name, pipeline in collector_config["service"]["pipelines"].items():
        if "otlp" not in pipeline.get("receivers", []):
            continue
        group = _SIGNAL_OF_PIPELINE[pipeline_name.split("/")[0]]
        contexts = {block["context"] for block in proc.get(group, [])}
        missing = _REQUIRED_ATTRIBUTE_CONTEXTS[group] - contexts
        assert not missing, f"{group} sem scrub nos contextos {sorted(missing)}"


def test_every_statement_is_inside_the_interpreted_subset(collector_config: dict[str, Any]) -> None:
    """Fail-closed: o interpretador compila TODAS as declaracoes ou o teste falha."""
    proc = _scrub_processor(collector_config)
    count = 0
    for group, blocks in proc.items():
        if group == "error_mode":
            continue
        for block in blocks:
            for statement in block["statements"]:
                compile_statement(statement, block["context"])
                count += 1
    assert count >= 7 * 20  # nao-vacuidade: 7 contextos de atributos com o conjunto completo


# ---------------------------------------------------------------------------
# 2. Execucao: PHI entra, nada de PHI sai
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(_PHI_KEY_FAMILIES))
def test_phi_key_family_is_dropped_in_every_context(collector_config: dict[str, Any], family: str) -> None:
    phi = _PHI_KEY_FAMILIES[family]
    out = apply_scrub(collector_config, _telemetry_with({**phi, **_OPERATIONAL}))
    for ctx in _ALL_CONTEXTS:
        surviving = set(phi) & set(out[ctx]["attributes"])
        assert not surviving, f"{family}: chaves PHI sobreviveram em {ctx}: {sorted(surviving)}"
    dumped = json.dumps(out, ensure_ascii=False)
    for value in phi.values():
        assert value not in dumped, f"{family}: valor PHI {value!r} vazou na saida"


@pytest.mark.parametrize("case", sorted(_PHI_VALUE_PATTERNS))
def test_phi_shaped_value_under_neutral_key_is_redacted(collector_config: dict[str, Any], case: str) -> None:
    key, value, needle = _PHI_VALUE_PATTERNS[case]
    out = apply_scrub(collector_config, _telemetry_with({key: value}))
    for ctx in _ALL_CONTEXTS:
        scrubbed = out[ctx]["attributes"].get(key, "")
        assert needle not in scrubbed, f"{case}: {needle!r} sobreviveu em {ctx}.{key} = {scrubbed!r}"
        assert "REDACTED" in scrubbed, f"{case}: valor nao marcado como redigido em {ctx}"


@pytest.mark.parametrize("case", sorted(_PHI_VALUE_PATTERNS))
def test_phi_shaped_text_in_span_name_and_log_body(collector_config: dict[str, Any], case: str) -> None:
    _, value, needle = _PHI_VALUE_PATTERNS[case]
    out = apply_scrub(collector_config, _telemetry_with({}, free_text=value))
    assert needle not in out["trace.span"]["name"]
    assert needle not in out["log.log"]["body"]


def test_operational_attributes_survive_untouched(collector_config: dict[str, Any]) -> None:
    out = apply_scrub(collector_config, _telemetry_with(dict(_OPERATIONAL)))
    for ctx in _ALL_CONTEXTS:
        assert out[ctx]["attributes"] == _OPERATIONAL, ctx
    assert out["trace.span"]["name"] == "GET /v1/guias/{guia_id}"
    assert out["log.log"]["body"] == "guia recebida"


# ---------------------------------------------------------------------------
# 3. Paridade com a primeira linha (PhiLogRedactor / PHI_FIELDS, F-063, ADR-0006)
# ---------------------------------------------------------------------------


def test_every_canonical_phi_field_name_is_dropped_by_the_collector(collector_config: dict[str, Any]) -> None:
    """O que o app trata como campo PHI (`PHI_FIELDS`) ou texto livre (`_FREE_TEXT_FIELDS` do
    PhiLogRedactor) tambem e' apagado pelo collector, se escapar como atributo."""
    from maezo.gateway import log_scrubber
    from maezo.platform.privacy.key_scrubber import PHI_KEY_ANCHOR_LOG_FIELDS
    from maezo.tools.workers.phi_vars import PHI_FREE_TEXT_VARS, PHI_PROCESS_VARS

    # beatriz/graph.py::_PHI_KEYS (privado; espelhado literalmente)
    beatriz_phi_keys = {
        "cpf",
        "cnpj",
        "nome",
        "name",
        "nome_social",
        "cns",
        "rg",
        "telefone",
        "endereco",
        "email",
    }
    canonical = (
        set(PHI_FIELDS)
        | set(log_scrubber._FREE_TEXT_FIELDS)
        | set(PHI_PROCESS_VARS)
        | set(PHI_FREE_TEXT_VARS)
        | set(PHI_KEY_ANCHOR_LOG_FIELDS)
        | beatriz_phi_keys
    )
    canonical |= {f"maezo.{name}" for name in canonical}
    assert canonical >= {"cpf", "nome", "telefone", "email", "body", "mensagem"}
    attrs = {name: "valor-sintetico" for name in canonical}
    out = apply_scrub(collector_config, _telemetry_with(attrs))
    for ctx in _ALL_CONTEXTS:
        assert not set(out[ctx]["attributes"]) & canonical, ctx


@pytest.mark.parametrize(
    ("text", "app_redactor"),
    [
        ("cpf 529.982.247-25 negado", "log"),
        ("cpf 52998224725 negado", "log"),
        ("contato maria.ficticia@example.com", "log"),
        ("ligar (11) 98888-7777 hoje", "log"),
        ("ligar +55 21 97777-6666 hoje", "log"),
        # B2: padroes que so' a rede de texto livre dos workers (phi_vars) pega.
        ("cns 898001160123456", "phi_vars"),
        ("cpf 529 982 247 25", "phi_vars"),
        ("cnpj 11.222.333/0001-81", "phi_vars"),
        ("ligar 98888-7777", "phi_vars"),
    ],
)
def test_collector_redacts_what_the_app_redactor_redacts(
    collector_config: dict[str, Any], text: str, app_redactor: str
) -> None:
    from maezo.tools.workers.phi_vars import redact_free_text

    if app_redactor == "phi_vars":
        app_side = redact_free_text(text)
    else:
        app_side = PhiLogRedactor()(None, "info", {"detail": text})["detail"]
    assert app_side != text  # o corpus e' de fato PHI para a primeira linha
    out = apply_scrub(collector_config, _telemetry_with({"detail": text}))
    for ctx in _ALL_CONTEXTS:
        collector_side = out[ctx]["attributes"]["detail"]
        assert collector_side != text, ctx
        assert not re.search(r"[0-9]{4}", collector_side.replace("REDACTED", "")), (ctx, collector_side)
        assert "@" not in collector_side, (ctx, collector_side)


# ---------------------------------------------------------------------------
# 4. O interpretador e' fail-closed (cerca contra prova vacua)
# ---------------------------------------------------------------------------


def test_interpreter_matches_re2_on_trailing_newline_and_unicode_boundary() -> None:
    sig = {"attributes": {"laudo\n": "x", "k": "é52998224725é"}}
    compile_statement('delete_matching_keys(attributes, "(^|[._-])laudo$")', "span")(sig)
    compile_statement('replace_all_patterns(attributes, "value", "\\\\b[0-9]{11}\\\\b", "[X]")', "span")(sig)
    assert "laudo\n" in sig["attributes"]  # Go RE2 NAO apaga (`$` == fim do texto)
    assert sig["attributes"]["k"] == "é[X]é"  # Go RE2: `\b` ASCII


@pytest.mark.parametrize(
    "statement",
    [
        'set(attributes["cpf"], SHA256(attributes["cpf"]))',
        'delete_key(attributes, "cpf") where resource.attributes["x"] == "y"',
        'delete_matching_keys(attributes, "\\\\d{11}")',
        'delete_matching_keys(attributes, "(?=cpf)")',
        'replace_all_patterns(attributes, "value", "(cpf)", "$1")',
        'replace_all_patterns(attributes, "key", "cpf", "x")',
        'delete_key(attributes, "a\\tb")',
        'replace_pattern(body, "x", "y")',
        'delete_matching_keys(attributes, "foo\\\\Z")',
        'delete_matching_keys(attributes, "[[:alpha:]]x")',
        'delete_matching_keys(attributes, "(?s)a.b")',
    ],
)
def test_interpreter_refuses_anything_it_cannot_reproduce(statement: str) -> None:
    with pytest.raises(UnsupportedOttlError):
        compile_statement(statement, "span")


def test_removing_the_scrub_from_metrics_is_caught(collector_config: dict[str, Any]) -> None:
    """Mutacao: a configuracao anterior (metricas sem scrub) tem de reprovar a cerca estrutural."""
    mutated = json.loads(json.dumps(collector_config))
    procs = mutated["service"]["pipelines"]["metrics"]["processors"]
    procs.remove(_SCRUB)
    with pytest.raises(AssertionError, match="metrics"):
        test_every_otlp_pipeline_runs_the_phi_scrub(mutated)

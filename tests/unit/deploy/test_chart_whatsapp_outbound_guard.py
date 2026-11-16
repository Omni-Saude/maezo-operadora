"""E-SF-14 / GTM-E-44 (GT-GTM-077, GT-GTM-078): cerca de pre-voo do WhatsApp outbound no chart.

`whatsapp.phoneNumberId` vazio com `whatsapp.outbound.enabled` ou `whatsapp.lembrete.enabled`
ligado tem de FALHAR NO RENDER nomeando a chave; com as duas flags desligadas (default) vazio passa.
Nada e' provisionado por esta cerca e o default segue tudo desligado.

Duas camadas, de proposito:

1. Os dois testes nomeados (`test_chart_refuses_enabled_outbound_with_empty_phone_number_id`,
   `test_chart_allows_empty_phone_number_id_when_outbound_off`) NAO dependem do binario `helm`:
   leem o texto REAL de `_helpers.tpl` / `guard-whatsapp-outbound.yaml`, extraem as condicoes da
   cerca e as avaliam (`_guard_refuses`) contra os values REAIS do chart. Remover a cerca do
   template, trocar `or` por `and` ou apontar para outra chave quebra a extracao/avaliacao e o
   teste fica vermelho em qualquer maquina.
2. Os testes `*_rendered_by_helm` renderizam o chart real com `helm template` (mesmo padrao de
   `tests/unit/platform/test_webhook_receiver_whatsapp_env.py`; sem `skip`: sem helm falham alto).
   O job `unit (restante)` do CI os executa com o helm pre-instalado na imagem `ubuntu-latest`
   (mesmo precedente de `tests/unit/ci/test_helm_fence_tests_do_not_skip.py`); sem helm falham
   alto, nunca pulam.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES = _CHART_DIR / "values.yaml"
_VALUES_AMH = _CHART_DIR / "values-amh.yaml"
_HELPERS = _CHART_DIR / "templates" / "_helpers.tpl"
_GUARD_TEMPLATE = _CHART_DIR / "templates" / "guard-whatsapp-outbound.yaml"
_DEFINE = "maezo-tenant.whatsapp.requirePhoneNumberId"
_KEY = "whatsapp.phoneNumberId"


def _helper_body() -> str:
    text = _HELPERS.read_text(encoding="utf-8")
    start = text.find('{{- define "' + _DEFINE + '" -}}')
    assert start != -1, f"define {_DEFINE!r} ausente de _helpers.tpl: a cerca foi removida"
    # O corpo usa so' `end -}}` internos; o fechamento do define e' o primeiro `{{- end }}`.
    end = text.find("{{- end }}", start)
    assert end != -1
    return text[start:end]


def _guard_refuses(values: dict[str, Any]) -> str | None:
    """Avalia a cerca do template contra `values`: devolve a mensagem do `fail` ou None."""
    body = _helper_body()
    flags = re.search(r"\{\{-\s*if or (\$wa\.[\w.]+) (\$wa\.[\w.]+)\s*-\}\}", body)
    assert flags, "condicao `if or <outbound> <lembrete>` da cerca ausente/alterada"
    empty = re.search(r"if not \((\$wa\.[\w.]+) \| default \"\" \| toString \| trim\)", body)
    assert empty, (
        'teste de id vazio (`not ($wa.phoneNumberId | default "" | toString | trim)`) ausente/alterado'
    )
    fail = re.search(r"fail \(printf \"(?P<msg>[^\"]*)\"", body)
    assert fail, "chamada `fail` ausente da cerca"
    assert "{{- $wa := .Values.whatsapp -}}" in body

    def resolve(ref: str) -> Any:
        node: Any = values["whatsapp"]
        for part in ref.removeprefix("$wa.").split("."):
            node = node[part]
        return node

    if not (resolve(flags.group(1)) or resolve(flags.group(2))):
        return None
    if str(resolve(empty.group(1)) or "").strip():
        return None
    return fail.group("msg")


def _chart_values(
    *, outbound: bool = False, lembrete: bool = False, phone_number_id: str | None = ""
) -> dict[str, Any]:
    values: dict[str, Any] = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))
    wa = values["whatsapp"]
    wa["outbound"]["enabled"] = outbound
    wa["lembrete"]["enabled"] = lembrete
    wa["phoneNumberId"] = phone_number_id
    return values


def test_chart_refuses_enabled_outbound_with_empty_phone_number_id() -> None:
    for empty in ("", "   ", None):
        for kwargs in ({"outbound": True}, {"lembrete": True}, {"outbound": True, "lembrete": True}):
            message = _guard_refuses(_chart_values(phone_number_id=empty, **kwargs))
            assert message is not None, f"{kwargs} com phoneNumberId={empty!r} deveria falhar o render"
            assert _KEY in message, "a mensagem tem de nomear a chave whatsapp.phoneNumberId"
    # A cerca nao depende de webhookReceiver.enabled: o template que a dispara e' incondicional.
    guard = _GUARD_TEMPLATE.read_text(encoding="utf-8")
    assert guard.rstrip().splitlines()[-1] == '{{- include "' + _DEFINE + '" . -}}'
    assert re.sub(r"\{\{-? /\*.*?\*/ -?\}\}", "", guard, flags=re.S).strip() == (
        '{{- include "' + _DEFINE + '" . -}}'
    )


def test_chart_allows_empty_phone_number_id_when_outbound_off() -> None:
    shipped = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))["whatsapp"]
    assert shipped["phoneNumberId"] == ""
    assert shipped["outbound"]["enabled"] is False
    assert shipped["lembrete"]["enabled"] is False
    assert _guard_refuses(yaml.safe_load(_VALUES.read_text(encoding="utf-8"))) is None
    assert _guard_refuses(_chart_values()) is None, "tudo desligado + id vazio tem de passar"
    # Com id preenchido a flag ligada tambem passa (a cerca exige presenca, nao valida o valor).
    assert _guard_refuses(_chart_values(outbound=True, phone_number_id="100000000000001")) is None
    assert _guard_refuses(_chart_values(lembrete=True, phone_number_id="100000000000001")) is None


def test_values_overlays_do_not_enable_outbound_or_set_a_phone_number_id() -> None:
    """Nada e' provisionado: nenhum overlay do repo liga as flags nem fixa um id."""
    for path in sorted(_CHART_DIR.glob("values*.yaml")):
        wa = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("whatsapp") or {}
        assert not (wa.get("outbound") or {}).get("enabled"), path.name
        assert not (wa.get("lembrete") or {}).get("enabled"), path.name
        assert not str(wa.get("phoneNumberId", "")).strip(), path.name


def _helm_template(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), "-f", str(_VALUES_AMH), *extra_args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_chart_refuses_enabled_outbound_with_empty_phone_number_id_rendered_by_helm() -> None:
    for flag in ("outbound", "lembrete"):
        result = _helm_template("--set", f"whatsapp.{flag}.enabled=true")
        assert result.returncode != 0, f"whatsapp.{flag}.enabled=true + phoneNumberId vazio deveria falhar"
        assert _KEY in result.stderr, result.stderr
    ok = _helm_template(
        "--set", "whatsapp.outbound.enabled=true", "--set-string", "whatsapp.phoneNumberId=100000000000001"
    )
    assert ok.returncode == 0, ok.stderr


def test_chart_allows_empty_phone_number_id_when_outbound_off_rendered_by_helm() -> None:
    result = _helm_template()
    assert result.returncode == 0, result.stderr

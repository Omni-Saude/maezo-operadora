"""Guardas do harness de carga/chaos do AUTH (R-086) — sem engine, roda no ``make test``.

Cobre o que o teste de p95 e o locustfile ASSUMEM e que, se quebrar, os faria medir a coisa
errada ou apontar para o lugar errado: (1) a recusa de host nao-local (carga nunca em ambiente
compartilhado/remoto) — com casos negativos de bypass por prefixo/userinfo; (2) o percentil
nearest-rank; (3) a meta de 200 ms rastreavel ao texto de PLANS.md (M13); (4) as variaveis
sinteticas cobrindo TODAS as entradas de cada DMN do AUTH implantada; (5) a business key
espelhada igual ao compositor contratual real; (6) o tenant descartavel aceito pelo deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maezo.platform.deploy.engine_deploy import require_deploy_tenant
from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.process_business_keys import auth_business_key
from tests.load import auth_scenarios as scn

_REPO = Path(__file__).resolve().parents[3]
_DMN_NS = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/engine-rest",
        "http://127.0.0.1:8080/engine-rest",
        "http://[::1]:8080/engine-rest",
        "https://localhost/engine-rest",
    ],
)
def test_alvo_local_aceito(url: str) -> None:
    assert scn.assert_local_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost.evil.example:8080/engine-rest",  # prefixo "localhost" nao basta
        "http://127.0.0.1@evil.example:8080/engine-rest",  # userinfo nao e' host
        "http://127.0.0.1.nip.io/engine-rest",
        "http://10.0.0.5:8080/engine-rest",
        "http://0.0.0.0:8080/engine-rest",  # bind-all nao e' loopback
        "https://maezo-dev.example.com/engine-rest",
        "ftp://localhost/engine-rest",
        "file:///etc/hosts",
        "localhost:8080/engine-rest",  # sem esquema
        "",
    ],
)
def test_alvo_nao_local_recusado(url: str) -> None:
    with pytest.raises(ValueError):
        scn.assert_local_url(url)


def test_percentil_nearest_rank() -> None:
    amostras = [float(v) for v in range(1, 101)]
    assert scn.nearest_rank_percentile(amostras, 95) == 95.0
    assert scn.nearest_rank_percentile(amostras, 50) == 50.0
    assert scn.nearest_rank_percentile(amostras, 100) == 100.0
    assert scn.nearest_rank_percentile(list(reversed(amostras)), 95) == 95.0
    assert scn.nearest_rank_percentile([7.0], 95) == 7.0
    assert scn.nearest_rank_percentile([float(v) for v in range(1, 31)], 95) == 29.0
    # 200 amostras: p95 e' a 190a ordenada (o teste de integracao usa SAMPLES=200).
    assert scn.nearest_rank_percentile([float(v) for v in range(1, 201)], 95) == 190.0


@pytest.mark.parametrize("pct", [0, -1, 100.1])
def test_percentil_fora_do_dominio_recusado(pct: float) -> None:
    with pytest.raises(ValueError):
        scn.nearest_rank_percentile([1.0], pct)


def test_percentil_sem_amostra_recusado() -> None:
    with pytest.raises(ValueError):
        scn.nearest_rank_percentile([], 95)


def test_meta_p95_vem_do_criterio_do_m13_em_plans() -> None:
    """A meta nao e' inventada: e' o numero do criterio de Verificacao do M13 em PLANS.md."""
    linhas = (_REPO / "PLANS.md").read_text(encoding="utf-8").splitlines()
    m13 = next(i for i, linha in enumerate(linhas) if linha.startswith("### M13"))
    fim = next(i for i in range(m13 + 1, len(linhas)) if linhas[i].startswith("### "))
    criterios = [linha for linha in linhas[m13:fim] if "p95" in linha and "AUTH" in linha]
    assert len(criterios) == 1, criterios
    numero = re.search(r"p95\s*<\s*(\d+)\s*ms", criterios[0])
    assert numero is not None, criterios[0]
    assert float(numero.group(1)) == scn.P95_BOUND_MS
    assert scn.P95_BOUND_SOURCE.startswith("PLANS.md:")


def _inputs_da_dmn(chave: str) -> set[str]:
    raiz = parse_xml_without_dtd((_REPO / "spec" / "processes" / "dmn" / f"{chave}.dmn").read_bytes())
    decisao = next(d for d in raiz.iter(f"{_DMN_NS}decision") if d.get("id") == chave)
    textos = {
        (t.text or "").strip()
        for t in decisao.iter(f"{_DMN_NS}inputExpression")
        for t in t.iter(f"{_DMN_NS}text")
    }
    assert textos, chave
    return textos


@pytest.mark.parametrize("chave", sorted(scn.DMN_SCENARIOS))
def test_cenario_sintetico_cobre_todas_as_entradas_da_dmn(chave: str) -> None:
    assert set(scn.DMN_SCENARIOS[chave]) == _inputs_da_dmn(chave)


def test_cenarios_cobrem_as_dmns_implantadas_com_o_processo() -> None:
    implantadas = {Path(rel).stem for rel in scn.AUTH_ARTIFACTS if rel.startswith("dmn/")}
    assert set(scn.DMN_SCENARIOS) == implantadas
    assert all(p.is_file() for p in scn.auth_artifact_paths())


def test_start_cobre_entradas_da_admissibilidade() -> None:
    assert _inputs_da_dmn("auth_admissibility") <= set(scn.start_variables())


def test_variaveis_camunda_tipadas_e_nao_escalar_recusado() -> None:
    assert scn.camunda_variables({"a": True, "b": 3, "c": "x"}) == {
        "a": {"value": True, "type": "Boolean"},
        "b": {"value": 3, "type": "Long"},
        "c": {"value": "x", "type": "String"},
    }
    with pytest.raises(TypeError):
        scn.camunda_variables({"d": {"aninhado": 1}})


def test_business_key_espelhada_igual_ao_compositor_contratual() -> None:
    for _ in range(5):
        tenant, guia = scn.synthetic_tenant(), scn.synthetic_guia()
        assert scn.auth_business_key(tenant, guia) == auth_business_key(
            tenant_id=tenant, numero_guia_tiss=guia
        )


def test_tenant_descartavel_aceito_pelo_deploy_e_sem_separador() -> None:
    tenant = scn.synthetic_tenant()
    assert require_deploy_tenant(tenant) == tenant
    assert "-" not in tenant
    assert scn.synthetic_tenant() != tenant

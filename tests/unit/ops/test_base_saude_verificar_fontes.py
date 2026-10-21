"""O verificador de fontes da base de saude (`scripts/ops/base_saude_verificar_fontes.py`, ADR-0064) sem rede:
o download e' injetado. Prova revisao vencida/proxima, fonte fora do ar, texto que diverge do trecho coletado
e fonte que mudou."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from scripts.ops.base_saude_verificar_fontes import (
    achados_de_fonte,
    achados_de_revisao,
    main,
    sha256_do_trecho,
    texto_da_pagina,
)

from maezo.conhecimento import base_saude as b

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "base_saude"


def _base_com_hash(tmp_path: Path, *, trecho: str) -> b.BaseSaude:
    dados = yaml.safe_load((_FIXTURE / "exame" / "exa-psa.yaml").read_text(encoding="utf-8"))
    dados["fontes"][0]["sha256_trecho"] = sha256_do_trecho(trecho)
    (tmp_path / "exame").mkdir()
    (tmp_path / "exame" / "exa-psa.yaml").write_text(
        yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8"
    )
    return b.carregar_base(tmp_path)


def test_revisao_vencida_e_proxima() -> None:
    base = b.carregar_base(_FIXTURE)
    assert achados_de_revisao(base, hoje=date(2026, 10, 17), dias=30) == []
    proximos = achados_de_revisao(base, hoje=date(2027, 4, 1), dias=30)
    assert {a.tipo for a in proximos} == {"revisao_proxima"}
    vencidos = achados_de_revisao(base, hoje=date(2027, 4, 18), dias=30)
    assert {a.tipo for a in vencidos} == {"revisao_vencida"} and len(vencidos) == 3


def test_fonte_que_ainda_tem_o_trecho_passa(tmp_path: Path) -> None:
    base = _base_com_hash(tmp_path, trecho=b.carregar_base(_FIXTURE).assuntos["exa-psa"].texto)
    psa = base.assuntos["exa-psa"]
    pagina = f"<html><script>x()</script><p>{psa.texto}</p></html>"
    assert achados_de_fonte(base, baixar=lambda url: (200, pagina)) == []


def test_fonte_que_mudou_fora_do_ar_e_texto_divergente(tmp_path: Path) -> None:
    base = _base_com_hash(tmp_path, trecho="um trecho antigo que nao e mais o texto")
    tipos = {a.tipo for a in achados_de_fonte(base, baixar=lambda url: (200, "<p>outra coisa</p>"))}
    assert tipos == {"texto_diverge_do_coletado", "fonte_mudou"}
    fora = achados_de_fonte(base, baixar=lambda url: (None, ""))
    assert "fonte_fora_do_ar" in {a.tipo for a in fora}


def test_o_hash_e_do_texto_normalizado_e_a_pagina_perde_as_tags() -> None:
    assert sha256_do_trecho("PSA é  uma Proteína") == sha256_do_trecho("psa e uma proteina")
    assert texto_da_pagina("<b>Olá</b> &amp; <style>.x{}</style>mundo") == "ola mundo"


def test_main_sem_rede_e_o_hash(capsys: object) -> None:
    assert main(["--raiz", str(_FIXTURE), "--sem-rede"]) == 0
    assert main(["--hash", "texto"]) == 0

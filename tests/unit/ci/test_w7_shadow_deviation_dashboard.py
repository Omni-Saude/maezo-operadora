"""Provas do gerador do dashboard de desvios shadow (W7 7.5; muro 2027-02-09).

A alegação sob teste é estreita e total: **o dashboard só diz o que uma fonte rastreada diz, e
sabe ficar vermelho quando a fonte se move ou o prazo passa.** Tudo aqui é uma face disso —

  (a) `classify_deadline` distingue NO-PRAZO / VENCIDO / SEM-DATA, herda o muro, acende o aviso
      na MESMA janela de 14 dias do gate `check_deviation_expiry.py` e isenta o item resolvido;
  (b) o gerador lê o registro de autonomia PELO LOADER TIPADO — manifestos SINTÉTICOS rendem as
      mesmas linhas que a árvore real, inclusive o caso VENCIDO e o caso flip (isento);
  (c) âncora desalinhada (linha movida, token ausente, arquivo ilegível) é DEFECT NOMEADO, não
      silêncio — a citação `arquivo:linha` é verificada, não decorada;
  (d) a saída é DETERMINÍSTICA: mesmas entradas ⇒ mesmos bytes (markdown e JSON), sem relógio;
  (e) a árvore REAL segue íntegra: as 32 flags enumeradas estão de pé, os grupos batem com o
      doc §3 e a divergência de soma do headline (31 × 32) está registrada, não reproduzida;
  (f) `main()` devolve os exit codes do contrato (0/2/3/4) contra a árvore real.

Datas HARDCODED de propósito nas provas de status (pinam o comportamento, não o YAML — a mesma
postura de `test_check_deviation_expiry.py`, irmão desta suíte).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "w7"))

import shadow_deviation_dashboard as dash  # noqa: E402 - path shim acima, espelha os irmãos de ci/

from maezo.gateway.action_execution import (  # noqa: E402
    _load_cached,
    load_action_approvals,
)

_HOJE = date(2026, 10, 27)  # dia do pacote — congela a aritmética das provas
_MURO = dash.MURO_SHADOW


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    """`action_approvals` é lru_cached no caminho resolvido; entrada velha falsificaria um passe."""
    _load_cached.cache_clear()
    yield
    _load_cached.cache_clear()


# ---------------------------------------------------------------------------------------------
# (a) A função pura de status — o único lugar onde um status nasce.
# ---------------------------------------------------------------------------------------------


def test_vencido_no_dia_seguinte_ao_prazo() -> None:
    prazo = _HOJE
    effective, dias, status, aviso = dash.classify_deadline(prazo, False, _HOJE + timedelta(days=1))
    assert status == dash.STATUS_VENCIDO
    assert dias == -1
    assert effective == prazo


def test_no_prazo_no_proprio_dia_e_na_vspera() -> None:
    _, _, status_vespera, _ = dash.classify_deadline(_HOJE, False, _HOJE - timedelta(days=1))
    _, _, status_dia, _ = dash.classify_deadline(_HOJE, False, _HOJE)
    assert {status_vespera, status_dia} == {dash.STATUS_NO_PRAZO}


def test_sem_data_quando_nao_ha_prazo_proprio_nem_muro() -> None:
    effective, dias, status, aviso = dash.classify_deadline(None, False, _HOJE)
    assert (effective, dias, status, aviso) == (None, None, dash.STATUS_SEM_DATA, "")


def test_muro_herdado_quando_nao_ha_prazo_proprio() -> None:
    effective, dias, status, _ = dash.classify_deadline(None, True, _HOJE)
    assert (effective, status) == (_MURO, dash.STATUS_NO_PRAZO)
    assert dias == (_MURO - _HOJE).days == 105


def test_prazo_proprio_vence_o_muro() -> None:
    prazo = date(2027, 1, 1)  # antes do muro — é o caso OP21 (Seção D)
    effective, dias, status, _ = dash.classify_deadline(prazo, True, _HOJE)
    assert (effective, dias, status) == (prazo, (prazo - _HOJE).days, dash.STATUS_NO_PRAZO)


def test_aviso_acende_na_mesma_janela_de_14_dias_do_gate() -> None:
    dentro = dash.classify_deadline(_HOJE + timedelta(days=dash.WARNING_WINDOW_DAYS), False, _HOJE)
    fora = dash.classify_deadline(_HOJE + timedelta(days=dash.WARNING_WINDOW_DAYS + 1), False, _HOJE)
    assert dentro[3] == "≤14 dias"
    assert fora[3] == ""


def test_item_resolvido_flip_e_isento_do_vencido() -> None:
    prazo_passado = _HOJE - timedelta(days=30)
    _, _, status, _ = dash.classify_deadline(prazo_passado, False, _HOJE, resolvido=True)
    assert status == dash.STATUS_NO_PRAZO  # desvio encerrado — histórico, não violação
    _, _, status_vencido, _ = dash.classify_deadline(prazo_passado, False, _HOJE)
    assert status_vencido == dash.STATUS_VENCIDO  # e sem o flip, vencia


# ---------------------------------------------------------------------------------------------
# (b) O loader tipado alimenta a Seção A — manifestos sintéticos, casos VENCIDO e flip.
# ---------------------------------------------------------------------------------------------


def _synthetic(tmp_path: Path, name: str, *, root_expires: str, review_by: str, class_value: str) -> Path:
    path = tmp_path / name / "action-approvals.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "status": "DRAFT",
        "modo": "shadow",
        "enforcement_padrao_nao_mapeado": "shadow",
        "deviation": {
            "owner_role": "dono sintético",
            "ratified_on": "2026-01-01",
            "ratified_by": "teste",
            "expires": root_expires,
            "checkpoint": "2026-03-01",
            "criteria_ref": "self-check",
        },
        "acoes": {
            "leitura_phi_clinica": {
                "enforcement": class_value,
                "deviation": {
                    "owner_role": "dono sintético",
                    "ratified_on": "2026-01-01",
                    "ratified_by": "teste",
                    "review_by": review_by,
                    "checkpoint": "2027-01-01",
                    "criteria_ref": "self-check",
                },
            },
        },
    }
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_manifesto_sintetico_vencido_rende_linha_vencida(tmp_path: Path) -> None:
    manifest = _synthetic(
        tmp_path, "vencido", root_expires="2026-01-31", review_by="2099-01-01", class_value="shadow"
    )
    items, problems = dash.build_secao_gateway(load_action_approvals(manifest), date(2026, 2, 1))
    assert problems == []
    q2 = next(i for i in items if i.item_id.endswith(dash._SLOT_Q2))
    assert (q2.status, q2.deadline_field) == (dash.STATUS_VENCIDO, "expires")
    assert q2.dias_restantes == -1
    q10 = next(i for i in items if i.item_id.endswith(dash._SLOT_Q10))
    assert q10.status == dash.STATUS_NO_PRAZO  # review_by em 2099


def test_manifesto_sintetico_vivo_rende_campos_exatos(tmp_path: Path) -> None:
    manifest = _synthetic(
        tmp_path, "vivo", root_expires="2099-01-31", review_by="2099-06-01", class_value="shadow"
    )
    items, problems = dash.build_secao_gateway(load_action_approvals(manifest), date(2026, 2, 1))
    assert problems == []
    q2 = next(i for i in items if i.item_id.endswith(dash._SLOT_Q2))
    assert q2.own_deadline == date(2099, 1, 31)
    assert q2.status == dash.STATUS_NO_PRAZO
    # o modo e a classe estão lá, com bound herdado do muro:
    modo = next(i for i in items if i.item_id == "A-modo")
    assert modo.muro_bound and modo.effective_deadline == _MURO
    # a classe COM bloco próprio é representada pela linha Q-10 e não repete como linha de classe:
    assert not any(i.item_id == "A-classe-leitura_phi_clinica" for i in items)


def test_classe_flipada_a_enforcing_e_isenta_e_some_da_sombra(tmp_path: Path) -> None:
    manifest = _synthetic(
        tmp_path, "flip", root_expires="2099-01-31", review_by="2099-06-01", class_value="enforcing"
    )
    items, problems = dash.build_secao_gateway(load_action_approvals(manifest), date(2026, 2, 1))
    assert problems == []
    q10 = next(i for i in items if i.item_id.endswith(dash._SLOT_Q10))
    assert q10.status == dash.STATUS_NO_PRAZO  # isenta — o desvio acabou
    assert "ENCERRADO" in q10.obs or "enforcing" in q10.estado
    assert not any(i.item_id == "A-classe-leitura_phi_clinica" for i in items)


def test_bloco_malformado_vira_problema_nomeado(tmp_path: Path) -> None:
    path = tmp_path / "quebrado" / "action-approvals.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "status": "DRAFT",
        "modo": "shadow",
        "enforcement_padrao_nao_mapeado": "shadow",
        "deviation": {"owner_role": "só isso — falta o resto"},
        "acoes": {},
    }
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    _, problems = dash.build_secao_gateway(load_action_approvals(path), date(2026, 2, 1))
    assert problems, "bloco malformado tem que virar problema nomeado, nunca silêncio"
    assert any("malformado" in p for p in problems)


def test_classe_shadow_sem_bloco_vira_problema(tmp_path: Path) -> None:
    path = tmp_path / "sembloco" / "action-approvals.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "status": "DRAFT",
        "modo": "shadow",
        "enforcement_padrao_nao_mapeado": "shadow",
        "deviation": {
            "owner_role": "dono",
            "ratified_on": "2026-01-01",
            "ratified_by": "t",
            "expires": "2099-01-01",
            "checkpoint": "2026-03-01",
            "criteria_ref": "t",
        },
        "acoes": {"leitura_phi_clinica": {"enforcement": "shadow"}},
    }
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    _, problems = dash.build_secao_gateway(load_action_approvals(path), date(2026, 2, 1))
    assert any("sem bloco" in p for p in problems)


# ---------------------------------------------------------------------------------------------
# (c) Âncoras — drift é nomeado; a citação arquivo:linha é verificada.
# ---------------------------------------------------------------------------------------------


def _probe(tmp_path: Path, text: str) -> Path:
    probe = tmp_path / "fonte.txt"
    probe.write_text(text, encoding="utf-8")
    return tmp_path


def test_ancora_na_dica_verifica(tmp_path: Path) -> None:
    root = _probe(tmp_path, "um\ndois com token\n")
    v = dash.verify_anchor(root, dash.Anchor("fonte.txt", "token", 2))
    assert v.ok and v.fonte == "fonte.txt:2"


def test_ancora_movida_e_drift_nomeado_com_a_linha_nova(tmp_path: Path) -> None:
    root = _probe(tmp_path, "um\n\n\nquatro com token\n")
    v = dash.verify_anchor(root, dash.Anchor("fonte.txt", "token", 2))
    assert not v.ok
    assert "movida" in v.note and v.found_line == 4


def test_ancora_com_token_ausente_e_drift(tmp_path: Path) -> None:
    root = _probe(tmp_path, "um\ndois\n")
    v = dash.verify_anchor(root, dash.Anchor("fonte.txt", "token-inexistente", 1))
    assert not v.ok and "ausente" in v.note and v.found_line is None


def test_ancora_com_arquivo_ausente_e_drift(tmp_path: Path) -> None:
    v = dash.verify_anchor(tmp_path, dash.Anchor("inexistente.txt", "token", 1))
    assert not v.ok and "ilegível" in v.note


def test_ancora_sem_dica_acha_primeira_ocorrencia(tmp_path: Path) -> None:
    root = _probe(tmp_path, "a\nb token\nc token\n")
    v = dash.verify_anchor(root, dash.Anchor("fonte.txt", "token"))
    assert v.ok and v.found_line == 2


# ---------------------------------------------------------------------------------------------
# (d) Determinismo — mesmas entradas ⇒ mesmos bytes.
# ---------------------------------------------------------------------------------------------


def _demo_item() -> dash.ShadowItem:
    return dash.ShadowItem(
        item_id="X-1",
        secao="X",
        rotulo="demo | pipe",
        estado="e",
        owner="o",
        own_deadline=None,
        deadline_field="—",
        muro_bound=True,
        deadline_basis=dash.MURO_BASIS,
        effective_deadline=_MURO,
        dias_restantes=(_MURO - _HOJE).days,
        status=dash.STATUS_NO_PRAZO,
        aviso="",
        checkpoint=date(2026, 9, 12),
        checkpoint_vencido=True,
        fonte="f:1",
        fonte_prazo="p:1",
        obs="o",
    )


def test_markdown_e_json_byte_estaveis() -> None:
    def build() -> dash.Dashboard:
        d = dash.Dashboard(hoje=_HOJE)
        d.add("X", "demo", [_demo_item()])
        return d

    assert build().to_markdown() == build().to_markdown()
    assert build().to_json() == build().to_json()


def test_pipe_de_celula_e_escapado_e_checkpoint_vencido_anotado() -> None:
    d = dash.Dashboard(hoje=_HOJE)
    d.add("X", "demo", [_demo_item()])
    md = d.to_markdown()
    assert "| demo \\| pipe |" in md
    assert "2026-09-12 (vencido)" in md
    assert d.counts() == {dash.STATUS_NO_PRAZO: 1, dash.STATUS_VENCIDO: 0, dash.STATUS_SEM_DATA: 0}


# ---------------------------------------------------------------------------------------------
# (e/f) A árvore REAL: inventário íntegro, divergência registrada, exit codes do contrato.
# ---------------------------------------------------------------------------------------------


def test_inventario_de_flags_bate_com_os_parciais_do_doc() -> None:
    soma = {"A": 5, "B": 3, "C": 3, "D": 1, "E": 19, "F": 1}
    atual = {g: sum(1 for r in dash.FLAG_ROWS if r.grupo == g) for g in soma}
    assert atual == soma
    assert sum(atual.values()) == 32


def test_divergencia_do_headline_do_doc_esta_registrada_viva() -> None:
    headline, verdict = dash.flags_doc_headline()
    assert verdict.ok
    assert headline == "31 interruptores em 6 grupos"  # o número do headline…
    assert (
        len(dash.FLAG_ROWS) == 32
    )  # …não é o tamanho do inventário enumerado (5+3+3+1+19+1; DL-0158 removeu B-F1)


def test_secao_b_na_arvore_real_rende_muro_herdado_e_ancoras_ok() -> None:
    items, verdicts, divergencia = dash.build_secao_flags(_HOJE)
    assert len(items) == 32
    assert all(i.status == dash.STATUS_NO_PRAZO for i in items)
    assert all(i.effective_deadline == _MURO and i.muro_bound for i in items)
    assert all(v.ok for v in verdicts), [v.note for v in verdicts if not v.ok]
    assert "32" in divergencia and "31" in divergencia


def test_secao_c_e_lacuna_declarada_sem_data_inventada() -> None:
    items, verdicts = dash.build_secao_candidates(_HOJE)
    assert len(items) == 6
    assert all(i.status == dash.STATUS_SEM_DATA for i in items)
    assert all(i.effective_deadline is None and not i.muro_bound for i in items)
    assert all("lacuna declarada" in i.deadline_basis for i in items)
    assert all(v.ok for v in verdicts), [v.note for v in verdicts if not v.ok]


def test_secao_d_extra_prazo_da_fonte_viva() -> None:
    items, verdicts = dash.build_secao_op21(_HOJE)
    assert all(v.ok for v in verdicts), [v.note for v in verdicts if not v.ok]
    dflag = next(i for i in items if i.item_id == "D-flag")
    assert dflag.own_deadline == date(2027, 1, 1)  # extraído de plan doc :17, não congelado
    assert dflag.dias_restantes == 66 and dflag.status == dash.STATUS_NO_PRAZO


def test_secao_e_deriva_e_checkpoint_vencido_anotados() -> None:
    items, verdicts, problems = dash.build_secao_governance(_HOJE)
    assert problems == []
    assert all(v.ok for v in verdicts), [v.note for v in verdicts if not v.ok]
    muro = next(i for i in items if i.item_id == "E-muro")
    assert (muro.own_deadline, muro.status) == (_MURO, dash.STATUS_NO_PRAZO)
    r131 = next(i for i in items if i.item_id == "E-r131")
    assert (r131.own_deadline, r131.dias_restantes) == (date(2026, 11, 11), 15)


def test_checkpoint_do_q2_vencido_e_anotacao_nao_status(tmp_path: Path) -> None:
    # Na árvore real: Q-2 tem checkpoint 2026-09-12 (< hoje) — anotado, nunca VENCIDO.
    items, problems = dash.build_secao_gateway(load_action_approvals(), _HOJE)
    assert problems == []
    q2 = next(i for i in items if i.item_id.endswith(dash._SLOT_Q2))
    assert q2.checkpoint == date(2026, 9, 12) and q2.checkpoint_vencido
    assert q2.status == dash.STATUS_NO_PRAZO  # o prazo dele (renovado) é 2027-02-09
    assert q2.own_deadline == date(2027, 2, 9)


def test_dashboard_completo_da_arvore_real_e_integro() -> None:
    d, verdicts, problems = dash.build_dashboard(_HOJE, None)
    assert problems == []
    assert all(v.ok for v in verdicts), [f"{v.anchor}: {v.note}" for v in verdicts if not v.ok]
    contagem = d.counts()
    assert contagem[dash.STATUS_SEM_DATA] == 8  # 6 candidatos + 2 derivas — lacunas declaradas
    assert contagem[dash.STATUS_VENCIDO] == 0
    assert sum(contagem.values()) == 68
    md = d.to_markdown()
    assert "SÓ dados rastreados" in md
    assert "OP-REGISTRY" in md  # a lacuna da fonte gitignored está declarada no próprio artefato


def test_main_exit_codes_do_contrato_na_arvore_real(tmp_path: Path) -> None:
    out = tmp_path / "dash.md"
    assert dash.main(["--today", _HOJE.isoformat(), "--out", str(out)]) == dash.EXIT_OK
    assert out.exists() and "Dashboard de desvios shadow" in out.read_text(encoding="utf-8")
    # json:
    out_json = tmp_path / "dash.json"
    assert dash.main(["--today", _HOJE.isoformat(), "--format", "json", "--out", str(out_json)]) == 0
    assert '"muro_shadow": "2027-02-09"' in out_json.read_text(encoding="utf-8")
    # self-check:
    assert dash.main(["--self-check"]) == dash.EXIT_OK


def test_main_exit_2_quando_ancora_dessalinha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Quebra UMA âncora de código (token renomeado na árvore) sem tocar no resto do repo real:
    real = dash.verify_anchor

    def sabotado(root: Path, anchor: dash.Anchor) -> dash.AnchorVerdict:
        if anchor.path == dash.WA_SETTINGS:
            return dash.AnchorVerdict(anchor, False, None, "token ausente no arquivo — fonte mudou de forma")
        return real(root, anchor)

    monkeypatch.setattr(dash, "verify_anchor", sabotado)
    code = dash.main(["--today", _HOJE.isoformat(), "--out", str(tmp_path / "d.md")])
    assert code == dash.EXIT_ANCHOR_DRIFT

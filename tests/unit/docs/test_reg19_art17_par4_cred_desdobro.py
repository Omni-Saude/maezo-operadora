"""REG-1.9 — cerca estática do desdobro do art. 17 § 4º da Lei 9.656/1998 em SP-OP-CRED-001.

A auditoria `REG-RESIDUAL-AUDIT-v1.md` §3.8 (bundle `docs/audits/JOURNEYS-COMPLETENESS-2026-10/`,
gitignored; consumido com cp+sha256 `5f25e8a4490eb26eccea0ff52efcd51f184d52bc32714ce85db3f301154c0597`)
classificou REG-1.9 como GAP-REAL: nenhuma jornada modela o passo de autorização expressa ANS para
redução de rede hospitalar. O pacote desdobra a citação genérica "Lei 9.656/1998 art. 17" do
contrato `docs/processes/contracts/SP-OP-CRED-001.md` até o § 4º (verbatim + os 4 incisos como
dados exigidos + regra fail-closed) — DARK, spec/contrato apenas: NENHUM elemento BPMN, DMN,
worker ou variável de engine é criado.

Este módulo é uma cerca de TEXTO ESTÁTICO (mesmo espírito de `test_ans_cron_contract_citations.py`
e `test_contract_bpmn_dmn_citations.py` — checagem estrutural sobre a prosa commitada, não um
parser semântico de contrato) e recusa as três formas de apodrecimento do desdobro:

  1. DESDOBRO AUSENTE/MUTILADO — a seção §REG-1.9 do contrato desaparece, perde o verbatim do
     caput, perde a fonte primária (URL + data de acesso) ou perde um dos 4 incisos do dossiê
     (conjunto FECHADO — inciso a mais ou a menos recusa).
  2. FAIL-CLOSED PERDIDO — a regra "falta QUALQUER inciso ⇒ confirmação RECUSADA" é removida
     (o passo viraria confirmável com dossiê incompleto — exatamente o defeito que o § 4º proíbe).
  3. DARK VIOLADO SEM GATE — algum `ans_reducao_*` aparece em `src/`, `spec/`, `config/` ou
     `deploy/` ANTES da confirmação jurídica registrada no bloco §CONFIRMAÇÃO PENDENTE. Se esta
     asserção um dia falha, alguém ligou o passo em produção sem o gate humano: REVERTER, ou
     promover com a confirmação em mãos (e aí atualizar o bloco, nunca removê-lo).

Nada aqui ratifica norma: a cerca não tira o DRAFT/verify — ela RECUSA a saída dele (um agente não
ratifica; ver `test_section_stays_draft_verify_and_names_the_human_queue`).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT = _REPO_ROOT / "docs" / "processes" / "contracts" / "SP-OP-CRED-001.md"
_MATRIX_YAML = _REPO_ROOT / "docs" / "sme-dispatch" / "regulatorio" / "rn-currency-matrix.yaml"

# Superfícies de runtime/wiring — o desdobro é DARK: nenhuma delas pode citar os campos do dossiê
# enquanto a confirmação humana pendente (fila jurídica/R-CRED) não existir.
_WIRED_SURFACES: tuple[Path, ...] = (
    _REPO_ROOT / "src",
    _REPO_ROOT / "spec",
    _REPO_ROOT / "config",
    _REPO_ROOT / "deploy",
)

_SECTION_HEADER = "## REG-1.9 — Desdobro Lei 9.656/1998 art. 17 § 4º"
_PENDING_HEADER = "### CONFIRMAÇÃO PENDENTE (fila jurídica/R-CRED) — AGENTES NÃO RATIFICAM"

#: Verbatim do caput do art. 17 § 4º (fonte primária Planalto, acesso 2026-10-27).
_CAPUT_VERBATIM = (
    "Em caso de redimensionamento da rede hospitalar por redução, "
    "as empresas deverão solicitar a ANS autorização expressa"
)
_SOURCE_URL = "https://www.planalto.gov.br/ccivil_03/leis/l9656.htm"
_ACCESS_DATE = "2026-10-27"

#: Conjunto FECHADO dos 4 incisos → campo do dossiê mínimo de solicitação à ANS (desenho DARK).
#: Inciso a mais ou a menos ⇒ recusa (a lei não deixa o dossiê a critério da operadora).
_INCISO_FIELDS: dict[str, str] = {
    "I": "ans_reducao_entidade_excluida",
    "II": "ans_reducao_capacidade_operacional_reduzida",
    "III": "ans_reducao_impacto_base_beneficiarios",
    "IV": "ans_reducao_justificativa_cobertura_equivalente",
}
_INCISOS_ROMANOS = frozenset(_INCISO_FIELDS)

#: Ocorrência do token COM ou SEM backticks: em superfícies de wiring real (py/yaml/json/bpmn/dmn)
#: o campo aparece como chave/constante/atributo SEM backticks — a guarda DARK precisa vê-lo dos
#: dois jeitos (prova por mutação: `X = "ans_reducao_entidade_excluida"` em src/*.py e a chave
#: `ans_reducao_entidade_excluida:` em config/*.yaml atravessavam a versão só-backtick;
#: lookbehind/lookahead evitam falso positivo em identificadores compostos como `my_ans_reducao_x`).
_FIELD_TOKEN_RE = re.compile(r"(?<!\w)ans_reducao_[a-z0-9_]+(?!\w)")


def _contract_text() -> str:
    assert _CONTRACT.is_file(), f"contrato ausente: {_CONTRACT}"
    return _CONTRACT.read_text(encoding="utf-8")


def _section_text() -> str:
    """Texto da seção §REG-1.9 (do header da seção até o próximo header `## ` ou fim)."""
    text = _contract_text()
    start = text.index(_SECTION_HEADER)
    rest = text[start + len(_SECTION_HEADER) :]
    nxt = rest.find("\n## ")
    return rest if nxt == -1 else rest[:nxt]


def test_desdobro_section_exists_exactly_once() -> None:
    text = _contract_text()
    assert text.count(_SECTION_HEADER) == 1, (
        f"seção §REG-1.9 deve existir exatamente 1x (achadas {text.count(_SECTION_HEADER)})"
    )


def test_caput_verbatim_and_primary_source_are_anchored() -> None:
    section = _section_text()
    assert _CAPUT_VERBATIM in section, (
        "verbatim do caput do art. 17 § 4º ausente da seção §REG-1.9 — o desdobro sem o texto "
        "da fonte primária vira citação de segunda mão (mesmo defeito da classe F-M10)"
    )
    assert _SOURCE_URL in section, "URL da fonte primária (Planalto) ausente da seção §REG-1.9"
    assert _ACCESS_DATE in section, "data de acesso da fonte primária ausente da seção §REG-1.9"


def test_the_four_incisos_are_a_closed_required_set() -> None:
    """O dossiê declara EXATAMENTE os 4 incisos (I-IV), cada um obrigatório — nem um a mais
    (o § 4º não tem 5º inciso), nem um a menos, nenhum opcional."""
    section = _section_text()
    rows: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == 4, f"linha do dossiê com forma inesperada: {line!r}"
        field = cells[0].strip("`")
        assert field not in rows, f"campo duplicado no dossiê §REG-1.9: {field}"
        rows[field] = (cells[1], cells[3])  # (inciso, obrigatório)
    expected = set(_INCISO_FIELDS.values())
    assert set(rows) == expected, (
        f"dossiê §REG-1.9 desviou do conjunto FECHADO dos 4 incisos: "
        f"a mais={sorted(set(rows) - expected)}, a menos={sorted(expected - set(rows))}"
    )
    wrong = {
        field: (inciso, obrig)
        for field, (inciso, obrig) in rows.items()
        if inciso not in _INCISOS_ROMANOS or obrig != "sim"
    }
    assert not wrong, f"linhas do dossiê sem inciso I-IV ou sem obrigatório 'sim': {wrong}"
    pairs = {inciso: field for inciso, field in _INCISO_FIELDS.items()}
    for inciso, field in pairs.items():
        assert rows[field][0] == inciso, f"{field} deve declarar o inciso {inciso}, não {rows[field][0]}"


def test_no_fifth_inciso_field_anywhere_in_the_contract() -> None:
    """Nenhum `ans_reducao_*` fora do conjunto FECHADO pode existir em NENHUMA parte do contrato
    (não só na seção) — um 5º campo inventado passaria a sugerir exigência legal inexistente."""
    found = set(_FIELD_TOKEN_RE.findall(_contract_text()))
    extras = sorted(found - set(_INCISO_FIELDS.values()))
    assert not extras, (
        f"campos `ans_reducao_*` fora do conjunto FECHADO dos 4 incisos: {extras} — o § 4º "
        "não tem 5º inciso; remover o campo ou ancorá-lo em norma confirmada pelo jurídico"
    )


def test_fail_closed_recusal_rule_is_declared() -> None:
    """A regra do desdobro: falta QUALQUER inciso ⇒ a confirmação do passo é RECUSADA (o passo
    não pode ser declarado cumprido com dossiê incompleto)."""
    section = _section_text()
    assert "FECHADO" in section, "marcação de conjunto FECHADO ausente da seção §REG-1.9"
    assert "Falta QUALQUER um" in section, "cláusula 'Falta QUALQUER um' ausente da regra fail-closed"
    assert "RECUSADA" in section, (
        "consequência de recusa ausente — sem ela o passo seria confirmável com dossiê incompleto"
    )
    assert "Nenhum caminho automatizado obtém" in section, (
        "a seção deve declarar que nenhum caminho automatizado obtém/infere/presume a autorização"
    )


def test_dark_no_runtime_wiring_before_the_human_gate() -> None:
    """Guarda negativa do DARK: nenhum `ans_reducao_*` em src/spec/config/deploy enquanto o
    desdobro não passa pelo gate humano. Falhou? Alguém ligou o passo em produção sem a
    confirmação da fila jurídica/R-CRED — reverter, ou promover COM o gate cumprido."""
    wired: list[str] = []
    for surface in _WIRED_SURFACES:
        if not surface.is_dir():
            continue
        for path in surface.rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".bpmn",
                ".dmn",
                ".yaml",
                ".yml",
                ".json",
            }:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for token in _FIELD_TOKEN_RE.findall(content):
                wired.append(f"{token} @ {path.relative_to(_REPO_ROOT)}")
    assert not wired, (
        "DARK violado: o desdobro REG-1.9 apareceu em superfície de runtime/wiring ANTES da "
        f"confirmação humana (fila jurídica/R-CRED): {sorted(set(wired))} — reverter o wiring, ou "
        "promover com a confirmação registrada no bloco §CONFIRMAÇÃO PENDENTE do contrato"
    )


def test_pending_block_names_the_human_queue_and_is_not_self_ratified() -> None:
    section = _section_text()
    assert _PENDING_HEADER in section, (
        "bloco §CONFIRMAÇÃO PENDENTE ausente — agentes não ratificam; o pacote sem o bloco "
        "seria uma ratificação unilateral"
    )
    assert "docs/review-queue.md" in section, "fila humana (docs/review-queue.md) não referenciada"
    assert "docs/review-queue.md:163" in section, (
        "linha da fila R do CRED não referenciada — a pendência deve apontar para a fila"
    )
    # O BPMN spec e o test-spec de engine ficam FORA deste pacote (spec/contrato por briefing):
    # a seção deve DISPOR a defesa, não fingi-la feita.
    assert "SP-OP-CRED-001_Descredenciamento.bpmn" in section, (
        "a seção deve dispor explicitamente a âncora BPMN spec como fora do escopo deste pacote"
    )


def test_section_stays_draft_verify_and_names_the_human_queue() -> None:
    """A cerca não ratifica: a seção deve CONTINUAR DRAFT/verify — o teste recusa a SAÍDA do
    DRAFT/verify feita por agente (só a confirmação humana a promove)."""
    section = _section_text()
    assert "DRAFT/verify" in section, (
        "seção §REG-1.9 deixou de se declarar DRAFT/verify — status só muda com confirmação humana"
    )


def test_rn_currency_matrix_anchors_into_this_contract_are_preserved() -> None:
    """A rn-currency-matrix ancora linhas DESTE contrato (`:5` → RN 566/RN 567; `:270` → RN 259).
    O desdobro foi desenhado para não deslocá-las (emenda in-place na :5/:278 e inserção DEPOIS
    da :270); a cerca espelha a própria matriz para recusar deslocamento futuro por edição desta
    seção — re-ancorar a matriz é ato explícito da PR, nunca efeito silencioso do desdobro."""
    assert _MATRIX_YAML.is_file(), f"matriz ausente: {_MATRIX_YAML}"
    rows = yaml.safe_load(_MATRIX_YAML.read_text(encoding="utf-8"))["rows"]
    lines = _contract_text().splitlines()
    checked = 0
    for row in rows:
        if row["arquivo"] != "docs/processes/contracts/SP-OP-CRED-001.md":
            continue
        token, line_no = row["norma_token"], row["linha"]
        assert 1 <= line_no <= len(lines), f"âncora fora do range: {row['arquivo']}:{line_no}"
        assert token in lines[line_no - 1], (
            f"âncora da rn-currency-matrix deslocada: {row['arquivo']}:{line_no} deve conter "
            f"{token!r} — re-ancore a matriz nesta PR (gate test_rn_currency_matrix), não deixe "
            "o desdobro movê-la em silêncio"
        )
        checked += 1
    assert checked >= 3, (
        f"esperava >= 3 âncoras da matriz neste contrato, achei {checked} — parser/paths mudaram?"
    )

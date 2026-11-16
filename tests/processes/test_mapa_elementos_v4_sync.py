"""Guarda D (EP-PROC-03): MAPA-ELEMENTOS-V4.md em sincronia com os 4 `CA_*_v4_adaptado.bpmn`.

O MAPA e camada de julgamento (CASA/LACUNA, D-02) sobre um inventario GERADO
(`scripts/docs/gerar_inventario_ca.py` -> `INVENTARIO-CA.generated.json`). Este modulo so prova que
o texto do MAPA nao mente sobre o XML; nao julga nenhum estado.

- D1 `test_denominadores_do_mapa_batem_com_a_contagem_dos_bpmn`: §1 (tabela, totais, agregados
  "relatados a parte", IDs duplicados, delegateExpressions), §2 ("Cada um dos N"), titulos §3-§6,
  bloco do §7 (igual ao `--resumo` do gerador) e total do §7 batem com a contagem do XML.
- D2 `test_somas_inline_do_mapa_sao_aritmeticamente_verdadeiras`: toda linha "Soma CA_x" avalia para
  o total declarado; total = titulo da secao; CASA/LACUNA declarados = elementos citados nas linhas
  CASA/LACUNA; o total do §7 e a soma dos titulos.
- D3 `test_todo_elemento_contado_e_citado_exatamente_uma_vez`: cada ID do denominador aparece
  exatamente uma vez na 1a coluna das tabelas §3-§6 (na secao do seu arquivo) e nada alem deles.
- sha256 de cada `.bpmn` == coluna sha256 do §1 == JSON; JSON versionado == regeneracao.
- Flips (§7.1): nenhum flip aplicado sem disposicao `adotado` do dono de D-02.

Cada modo de falha tem caso negativo em fixture (`tmp_path` ou texto mutado em memoria).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from scripts.docs import gerar_inventario_ca as gen

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTED = REPO_ROOT / gen.DIR_REL
MAPA_PATH = ADAPTED / "MAPA-ELEMENTOS-V4.md"

_SECOES_ELEMENTOS = ("3", "4", "5", "6")
_ID_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_]*)`")
_SHA_RE = re.compile(r"`([0-9a-f]{64})`")
_TITULO_RE = re.compile(r"^## §(\d+) (CA_\d) .*?\((\d+) = (\d+) CASA \+ (\d+) LACUNA\)", re.M)
_SOMA_RE = re.compile(
    r"^Soma (CA_\d)[^:]*: (.*?)\*\*(\d+)\*\* ✓ \(CASA (\d+), LACUNA (\d+)\)",
    re.M,
)
_SUBEQ_RE = re.compile(r"(?<![\w.])(\d+(?:\+\d+)*)=(\d+)")
_ABREV_TASK = {
    "uT": "userTask",
    "sT": "serviceTask",
    "rT": "receiveTask",
    "cA": "callActivity",
    "task": "task",
}
_ABREV_GW = {"X": "exclusiveGateway", "P": "parallelGateway", "EB": "eventBasedGateway"}
_ORDEM_EVENTS = gen.EVENTS  # start + end + catch + throw + boundary
_NOMES_AGREGADOS = {chave for chave, _, _ in gen.AGREGADOS} - {"flowNodeRef", "BPMNPlane"}


# --------------------------------------------------------------------------- utilitarios de parse


def _inventario_head() -> dict[str, Any]:
    return gen.montar_inventario(gen.ler_fontes(REPO_ROOT))


def _mapa() -> str:
    return MAPA_PATH.read_text(encoding="utf-8")


def _secao(texto: str, numero: str) -> str:
    m = re.search(rf"^## §{numero} .*$", texto, re.M)
    assert m is not None, f"MAPA sem a secao §{numero}"
    fim = re.search(r"^## ", texto[m.end() :], re.M)
    return texto[m.start() : m.end() + fim.start()] if fim else texto[m.start() :]


def _celulas(linha: str) -> list[str]:
    corpo = linha.strip()
    return [c.strip() for c in re.split(r"(?<!\\)\|", corpo[1:-1])]


def _tabelas(trecho: str) -> list[list[list[str]]]:
    """Todas as tabelas markdown do trecho (sem cabecalho nem separador)."""
    blocos: list[list[str]] = []
    atual: list[str] = []
    for ln in trecho.splitlines():
        if ln.startswith("|"):
            atual.append(ln)
        elif atual:
            blocos.append(atual)
            atual = []
    if atual:
        blocos.append(atual)
    return [[_celulas(ln) for ln in b[2:]] for b in blocos if len(b) >= 2]


def _linhas_de_dados(trecho: str) -> list[list[str]]:
    return [cel for tabela in _tabelas(trecho) for cel in tabela]


def _por_chave(inv: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["chave"]: a for a in inv["arquivos"]}


def _int(celula: str) -> int:
    m = re.match(r"^\**(\d+)", celula)
    assert m is not None, f"celula sem inteiro inicial: {celula!r}"
    return int(m.group(1))


def _estado(celula: str) -> str | None:
    tem_casa, tem_lacuna = "CASA" in celula, "LACUNA" in celula
    if tem_casa == tem_lacuna:
        return None
    return "CASA" if tem_casa else "LACUNA"


def _citacoes(texto: str) -> dict[str, list[tuple[str, str, str | None]]]:
    """ID -> [(secao, arquivo, estado da linha)] para a 1a coluna das tabelas de §3-§6."""
    out: dict[str, list[tuple[str, str, str | None]]] = {}
    for num in _SECOES_ELEMENTOS:
        sec = _secao(texto, num)
        titulo = _TITULO_RE.search(sec)
        assert titulo is not None, f"§{num} sem titulo '(N = a CASA + b LACUNA)'"
        arquivo = titulo.group(2)
        for tabela in _tabelas(sec):
            for cel in tabela:
                for el_id in _ID_RE.findall(cel[0]):
                    out.setdefault(el_id, []).append((num, arquivo, _estado(cel[-1])))
    return out


def _avaliar_soma(expr: str) -> int | None:
    """Avalia a expressao de uma linha 'Soma'; None se nao for so inteiros, '+', parenteses e 'a=b'."""
    expr = expr.strip().rstrip("=→").strip()
    subeqs = _SUBEQ_RE.findall(expr)
    if subeqs:
        total = 0
        for lhs, rhs in subeqs:
            if sum(int(x) for x in lhs.split("+")) != int(rhs):
                return -1
            total += int(rhs)
        return total
    try:
        arvore = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    return _somar_ast(arvore.body)


def _somar_ast(no: ast.expr) -> int | None:
    """Avalia uma arvore que so contem '+' e inteiros; qualquer outro no devolve None."""
    if isinstance(no, ast.Constant) and type(no.value) is int:
        return no.value
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        esq = _somar_ast(no.left)
        dir_ = _somar_ast(no.right)
        if esq is None or dir_ is None:
            return None
        return esq + dir_
    return None


# --------------------------------------------------------------------------- verificacoes puras


def problemas_d1(texto: str, inv: dict[str, Any]) -> list[str]:
    prob: list[str] = []
    arqs = _por_chave(inv)
    tot = inv["totais"]

    linhas_tabela: dict[str, list[str]] = {}
    for cel in _linhas_de_dados(_secao(texto, "1")):
        m_arq = re.search(r"`(CA_\d)_[^`]*\.bpmn`", cel[0])
        if m_arq:
            linhas_tabela[m_arq.group(1)] = cel
        elif "TOTAL" in cel[0]:
            linhas_tabela["TOTAL"] = cel
    if set(linhas_tabela) != {*arqs, "TOTAL"}:
        prob.append(f"§1: linhas {sorted(linhas_tabela)} != arquivos {sorted(arqs)} + TOTAL")
        return prob
    curtas = sorted(k for k, cel in linhas_tabela.items() if len(cel) != 7)
    if curtas:
        prob.append(
            f"§1: linhas {curtas} sem as 7 colunas "
            "(arquivo, sha256, processos, tasks, gateways, events, denominador)"
        )

    for chave, arq in arqs.items():
        cel = linhas_tabela[chave]
        den = arq["denominador"]
        decl = [_int(c) for c in cel[-5:]]
        real = [
            den["processos"],
            den["tasks"]["total"],
            den["gateways"]["total"],
            den["events"]["total"],
            den["total"],
        ]
        if decl != real:
            prob.append(f"§1 {chave}: declarado proc/tasks/gw/events/den={decl}, XML={real}")
        tasks_decl = {_ABREV_TASK[a]: int(n) for n, a in re.findall(r"(\d+) (uT|sT|rT|cA|task)\b", cel[-4])}
        tasks_real = {t: n for t, n in den["tasks"]["por_tipo"].items() if n}
        if tasks_decl != tasks_real:
            prob.append(f"§1 {chave}: tasks por tipo {tasks_decl} != XML {tasks_real}")
        gw_decl = {_ABREV_GW[a]: int(n) for n, a in re.findall(r"(\d+)(X|P|EB)\b", cel[-3])}
        gw_real = {t: n for t, n in den["gateways"]["por_tipo"].items() if n}
        if gw_decl != gw_real:
            prob.append(f"§1 {chave}: gateways por tipo {gw_decl} != XML {gw_real}")
        m_ev = re.search(r"\((\d+(?:\+\d+){4})\)", cel[-2])
        ev_decl = [int(x) for x in m_ev.group(1).split("+")] if m_ev else None
        ev_real = [den["events"]["por_tipo"][t] for t in _ORDEM_EVENTS]
        if ev_decl != ev_real:
            prob.append(f"§1 {chave}: events start+end+catch+throw+boundary {ev_decl} != XML {ev_real}")

    cel = linhas_tabela["TOTAL"]
    d = tot["denominador"]
    decl_t = [_int(c) for c in cel[-5:]]
    real_t = [d["processos"], d["tasks"]["total"], d["gateways"]["total"], d["events"]["total"], d["total"]]
    if decl_t != real_t:
        prob.append(f"§1 TOTAL: declarado {decl_t}, XML {real_t}")

    m_rel = re.search(r"^\*\*Relatados à parte[^*]*:\*\* (.*)$", _secao(texto, "1"), re.M)
    if m_rel is None:
        prob.append("§1: linha 'Relatados à parte' ausente")
    else:
        vistos: set[str] = set()
        for seg in m_rel.group(1).split(" · "):
            m = re.match(r"^(\w+|declarações) (\d+)(?: \((\d+(?:\+\d+)+)\))?", seg)
            if m is None:
                continue
            nome, valor = m.group(1), int(m.group(2))
            if nome == "declarações":
                real = tot["declaracoes"]["total"]
                partes_real = None
            elif nome in _NOMES_AGREGADOS:
                real = tot["agregados"][nome]
                partes_real = [arqs[k]["agregados"][nome] for k in sorted(arqs)]
            else:
                prob.append(f"§1: agregado desconhecido {nome!r}")
                continue
            vistos.add(nome)
            if valor != real:
                prob.append(f"§1: {nome} declarado {valor}, XML {real}")
            if (
                m.group(3)
                and partes_real is not None
                and [int(x) for x in m.group(3).split("+")] != partes_real
            ):
                prob.append(f"§1: {nome} parcelas {m.group(3)}, XML {partes_real}")
        faltando = (_NOMES_AGREGADOS | {"declarações"}) - vistos
        if faltando:
            prob.append(f"§1: agregados nao declarados: {sorted(faltando)}")
        m_dup = re.search(r"IDs duplicados: (\d+)", m_rel.group(1))
        if m_dup is None or int(m_dup.group(1)) != tot["ids_duplicados_por_arquivo"] + len(
            tot["ids_de_denominador_duplicados_no_corpus"]
        ):
            prob.append("§1: contagem de IDs duplicados ausente ou divergente")
        m_del = re.search(
            r"delegateExpression`: (\d+) distintos no corpus \((\d+) ocorrências\)", m_rel.group(1)
        )
        de = tot["delegate_expressions"]
        if m_del is None or (int(m_del.group(1)), int(m_del.group(2))) != (
            de["distintos_no_corpus"],
            de["ocorrencias"],
        ):
            prob.append("§1: delegateExpressions ausente ou divergente")

    m_cada = re.search(r"Cada um dos (\d+) elementos", _secao(texto, "2"))
    if m_cada is None or int(m_cada.group(1)) != tot["denominador"]["total"]:
        prob.append("§2: 'Cada um dos N elementos' ausente ou divergente")

    for num in _SECOES_ELEMENTOS:
        t = _TITULO_RE.search(_secao(texto, num))
        if t is None:
            prob.append(f"§{num}: titulo sem '(N = a CASA + b LACUNA)'")
        elif int(t.group(3)) != arqs[t.group(2)]["denominador"]["total"]:
            prob.append(
                f"§{num} {t.group(2)}: titulo declara {t.group(3)}, "
                f"XML {arqs[t.group(2)]['denominador']['total']}"
            )

    sec7 = _secao(texto, "7")
    m_bloco = re.search(r"```\n(A\. CONTAGENS POR ARQUIVO.*?)```", sec7, re.S)
    if m_bloco is None or m_bloco.group(1) != gen.resumo_texto(inv):
        prob.append(
            "§7: bloco 'A. CONTAGENS...' difere de `python -m scripts.docs.gerar_inventario_ca --resumo`"
        )
    m_tot = re.search(r"\*\*Total: (\d+) CASA \+ (\d+) LACUNA = (\d+)", sec7)
    if m_tot is None or int(m_tot.group(3)) != tot["denominador"]["total"]:
        prob.append("§7: 'Total: a CASA + b LACUNA = N' ausente ou N divergente do XML")
    return prob


def problemas_d2(texto: str) -> list[str]:
    prob: list[str] = []
    citacoes = _citacoes(texto)
    soma_casa = soma_lacuna = 0
    for num in _SECOES_ELEMENTOS:
        sec = _secao(texto, num)
        t = _TITULO_RE.search(sec)
        if t is None:
            prob.append(f"§{num}: titulo ausente")
            continue
        arquivo, n, casa, lacuna = t.group(2), int(t.group(3)), int(t.group(4)), int(t.group(5))
        soma_casa += casa
        soma_lacuna += lacuna
        if casa + lacuna != n:
            prob.append(f"§{num} titulo: {casa} + {lacuna} != {n}")
        somas = [s for s in _SOMA_RE.finditer(sec) if s.group(1) == arquivo]
        if len(somas) != 1:
            prob.append(f"§{num}: esperado 1 linha 'Soma {arquivo}', achadas {len(somas)}")
            continue
        s = somas[0]
        declarado, s_casa, s_lacuna = int(s.group(3)), int(s.group(4)), int(s.group(5))
        avaliado = _avaliar_soma(s.group(2))
        if avaliado != declarado:
            prob.append(f"§{num} Soma {arquivo}: expressao avalia {avaliado}, declara {declarado}")
        if declarado != n:
            prob.append(f"§{num} Soma {arquivo}: declara {declarado}, titulo {n}")
        if (s_casa, s_lacuna) != (casa, lacuna):
            prob.append(f"§{num} Soma {arquivo}: CASA/LACUNA {s_casa}/{s_lacuna} != titulo {casa}/{lacuna}")
        estados = [e for usos in citacoes.values() for (sec_n, _, e) in usos if sec_n == num]
        if None in estados:
            prob.append(f"§{num}: linha com estado ambiguo (nem so CASA nem so LACUNA)")
        if (estados.count("CASA"), estados.count("LACUNA")) != (casa, lacuna):
            prob.append(
                f"§{num}: linhas citam {estados.count('CASA')} CASA + {estados.count('LACUNA')} LACUNA, "
                f"titulo declara {casa} + {lacuna}"
            )
    m_tot = re.search(r"\*\*Total: (\d+) CASA \+ (\d+) LACUNA = (\d+)", _secao(texto, "7"))
    if m_tot is None:
        prob.append("§7: linha de Total ausente")
    else:
        c, lac, n = (int(x) for x in m_tot.groups())
        if c + lac != n or (c, lac) != (soma_casa, soma_lacuna):
            prob.append(f"§7 Total {c} + {lac} = {n} nao fecha com os titulos ({soma_casa} + {soma_lacuna})")
    return prob


def problemas_d3(texto: str, inv: dict[str, Any]) -> list[str]:
    prob: list[str] = []
    citacoes = _citacoes(texto)
    esperado = {e["id"]: a["chave"] for a in inv["arquivos"] for e in a["elementos"]}
    for el_id, arquivo in sorted(esperado.items()):
        usos = citacoes.get(el_id, [])
        if len(usos) != 1:
            prob.append(f"{arquivo}#{el_id}: citado {len(usos)}x em §3-§6 (esperado 1)")
        elif usos[0][1] != arquivo:
            prob.append(f"{arquivo}#{el_id}: citado na secao de {usos[0][1]}")
    for el_id in sorted(set(citacoes) - set(esperado)):
        prob.append(f"`{el_id}` citado em §3-§6 mas nao e elemento de denominador")
    return prob


def problemas_sha(texto: str, raiz: Path, inv: dict[str, Any]) -> list[str]:
    prob: list[str] = []
    declarados: dict[str, str] = {}
    for cel in _linhas_de_dados(_secao(texto, "1")):
        m_arq = re.search(r"`(CA_\d)_[^`]*\.bpmn`", cel[0])
        if m_arq:
            m_sha = _SHA_RE.search(cel[1])
            if m_sha is None:
                prob.append(f"§1 {m_arq.group(1)}: coluna sha256 sem 64 hex")
            else:
                declarados[m_arq.group(1)] = m_sha.group(1)
    for nome, dados in gen.ler_fontes(raiz):
        chave = gen._chave_arquivo(nome)
        real = hashlib.sha256(dados).hexdigest()
        if declarados.get(chave) != real:
            prob.append(f"{nome}: sha256 {real} != cabecalho do MAPA {declarados.get(chave)}")
        if _por_chave(inv)[chave]["sha256"] != real:
            prob.append(f"{nome}: sha256 {real} != JSON")
    return prob


def problemas_flips(texto: str) -> list[str]:
    prob: list[str] = []
    m = re.search(r"^### §7\.1 .*?(?=^## |\Z)", texto, re.M | re.S)
    if m is None:
        return ["§7.1 (tabela de flips) ausente"]
    tabelas = _tabelas(m.group(0))
    if len(tabelas) != 1 or len(tabelas[0]) != 8:
        return [f"§7.1: esperada 1 tabela com 8 flips, achado {[len(t) for t in tabelas]}"]
    citacoes = _citacoes(texto)
    alvo_no_eixo = {"DO": "LACUNA", "ADE": "LACUNA", "IMPL": "CASA"}
    for cel in tabelas[0]:
        ids = _ID_RE.findall(cel[1])
        disposicao = cel[-1].strip()
        if len(ids) != 1 or disposicao not in {"adotado", "rejeitado", "pendente"}:
            prob.append(f"§7.1 linha malformada ou disposicao fora do vocabulario: {cel!r}")
            continue
        usos = citacoes.get(ids[0], [])
        if len(usos) != 1:
            prob.append(f"§7.1 {ids[0]}: nao citado exatamente 1x em §3-§6")
            continue
        estado_atual = usos[0][2]
        alvo = alvo_no_eixo.get(cel[4].strip())
        if alvo is None or cel[3].strip() == alvo:
            prob.append(f"§7.1 {ids[0]}: Reconciliacao {cel[4].strip()!r} nao e flip de {cel[3].strip()!r}")
            continue
        esperado = alvo if disposicao == "adotado" else cel[3].strip()
        if estado_atual != esperado:
            prob.append(
                f"§7.1 {ids[0]}: disposicao {disposicao!r} exige estado {esperado}, MAPA tem {estado_atual}"
            )
    return prob


def _copiar_fontes(destino: Path) -> Path:
    alvo = destino / gen.DIR_REL
    alvo.mkdir(parents=True)
    for p in sorted(ADAPTED.glob(gen.GLOB_FONTE)):
        shutil.copy2(p, alvo / p.name)
    shutil.copy2(REPO_ROOT / gen.SAIDA_REL, destino / gen.SAIDA_REL)
    return alvo


def _mutar(texto: str, velho: str, novo: str) -> str:
    assert texto.count(velho) == 1, f"fixture de mutacao ambigua/ausente: {velho!r}"
    return texto.replace(velho, novo)


# --------------------------------------------------------------------------- guardas (head real)


def test_denominadores_do_mapa_batem_com_a_contagem_dos_bpmn() -> None:
    assert problemas_d1(_mapa(), _inventario_head()) == []


def test_somas_inline_do_mapa_sao_aritmeticamente_verdadeiras() -> None:
    assert problemas_d2(_mapa()) == []


def test_todo_elemento_contado_e_citado_exatamente_uma_vez() -> None:
    assert problemas_d3(_mapa(), _inventario_head()) == []


def test_sha256_dos_bpmn_batem_com_o_cabecalho_do_mapa() -> None:
    assert problemas_sha(_mapa(), REPO_ROOT, _inventario_head()) == []


def test_json_commitado_e_igual_a_regeneracao() -> None:
    assert (REPO_ROOT / gen.SAIDA_REL).read_text(encoding="utf-8") == gen.gerar(REPO_ROOT)
    assert gen.main(["--check"]) == 0


def test_nenhum_flip_aplicado_sem_disposicao_adotada_do_dono() -> None:
    assert problemas_flips(_mapa()) == []


def test_changelog_preserva_os_numeros_da_base_e_a_causa() -> None:
    texto = _mapa()
    m = re.search(r"^## Changelog\n(.*)\Z", texto, re.M | re.S)
    assert m is not None, "MAPA sem '## Changelog' no fim"
    antes = {cel[0]: _int(cel[1]) for t in _tabelas(m.group(1)) for cel in t if len(cel) == 4}
    assert {
        "Denominador do corpus": 359,
        "Denominador CA_2": 68,
        "sequenceFlow": 314,
        "dataObject": 24,
        "dataOutputAssociation": 3,
        "conditionExpression": 53,
        "BPMNShape": 477,
        "BPMNEdge": 398,
    }.items() <= antes.items()
    assert "LA-02" in m.group(1) and "f57f08219" in m.group(1)


def test_inventario_reproduz_os_numeros_do_criterio() -> None:
    """EP-PROC-03 criterio 1 — se o XML mudar, o numero novo vence e este pin e atualizado no PR."""
    inv = _inventario_head()
    tot = inv["totais"]
    assert [a["denominador"]["total"] for a in inv["arquivos"]] == [98, 104, 76, 89]
    assert tot["denominador"]["total"] == 367
    ag = tot["agregados"]
    assert [
        ag[k] for k in ("sequenceFlow", "dataObject", "dataOutputAssociation", "conditionExpression")
    ] == [
        322,
        27,
        6,
        54,
    ]
    assert [ag["BPMNShape"], ag["BPMNEdge"]] == [488, 406]
    assert [ag["lane"], ag["participant"], ag["messageFlow"], tot["declaracoes"]["total"]] == [57, 34, 36, 42]
    assert tot["ids_duplicados_por_arquivo"] == 0 and tot["ids_de_denominador_duplicados_no_corpus"] == []
    assert tot["delegate_expressions"]["distintos_no_corpus"] == 44
    for arq in inv["arquivos"]:
        assert arq["lanes"], f"{arq['chave']}: sem lanes"
        assert sum(len(lane["flow_node_refs"]) for lane in arq["lanes"]) == arq["agregados"]["flowNodeRef"]


def test_gerador_e_deterministico_e_nao_toca_os_bpmn() -> None:
    antes = {p.name: p.read_bytes() for p in ADAPTED.glob(gen.GLOB_FONTE)}
    assert gen.gerar(REPO_ROOT) == gen.gerar(REPO_ROOT)
    assert {p.name: p.read_bytes() for p in ADAPTED.glob(gen.GLOB_FONTE)} == antes


# --------------------------------------------------------------------------- casos negativos


def test_negativo_d1_denominador_declarado_diverge_do_xml() -> None:
    texto = _mutar(_mapa(), "| 42 (8+16+2+9+7) | **76** |", "| 42 (8+16+2+9+7) | **68** |")
    assert any("§1 CA_2" in p for p in problemas_d1(texto, _inventario_head()))


def test_negativo_d1_xml_ganha_elemento_que_o_mapa_nao_conta(tmp_path: Path) -> None:
    alvo = _copiar_fontes(tmp_path)
    ca2 = alvo / "CA_2_UTILIZACAO_v4_adaptado.bpmn"
    xml = ca2.read_text(encoding="utf-8")
    marcador = '<bpmn:endEvent id="U4_End_PrazoLegalVencido"'
    ca2.write_text(
        _mutar(xml, marcador, '<bpmn:endEvent id="U4_End_Fixture" />\n    ' + marcador), encoding="utf-8"
    )
    inv = gen.montar_inventario(gen.ler_fontes(tmp_path))
    assert any("CA_2" in p for p in problemas_d1(_mapa(), inv))
    assert any("U4_End_Fixture" in p for p in problemas_d3(_mapa(), inv))


def test_negativo_d1_agregado_relatado_a_parte_diverge() -> None:
    texto = _mutar(_mapa(), "sequenceFlow 322 (91+91+63+77)", "sequenceFlow 314 (91+91+55+77)")
    assert any("sequenceFlow" in p for p in problemas_d1(texto, _inventario_head()))


def test_negativo_d1_bloco_do_secao7_diverge_do_resumo() -> None:
    texto = _mutar(_mapa(), "   CA_2: DENOMINADOR = 76 ", "   CA_2: DENOMINADOR = 68 ")
    assert any("§7" in p for p in problemas_d1(texto, _inventario_head()))


def test_negativo_d2_soma_inline_falsa() -> None:
    texto = _mutar(_mapa(), "+ (3+2+3) = **76** ✓", "+ (3+2+2) = **76** ✓")
    assert any("expressao avalia 75" in p for p in problemas_d2(texto))


def test_negativo_d2_soma_por_subequacoes_falsa() -> None:
    texto = _mutar(_mapa(), "DS 4+0=4 → **98**", "DS 4+0=3 → **98**")
    assert any("Soma CA_0" in p for p in problemas_d2(texto))


def test_negativo_d2_estado_das_linhas_nao_fecha_com_o_titulo() -> None:
    linha = (
        "| `U4_T_Pesquisa` | userTask (`ced:pos-servico`, cliente) | "
        "fila humana SP-OP (NPS/form = wiring futuro) | — (C-7) | C-7 | **CASA** |"
    )
    texto = _mutar(_mapa(), linha, linha.replace("| **CASA** |", "| LACUNA |"))
    assert any("linhas citam 44 CASA + 32 LACUNA" in p for p in problemas_d2(texto))


def test_negativo_d3_elemento_contado_e_nao_citado() -> None:
    texto = _mutar(_mapa(), "`U4_T_AuditarContas`, `U4_GW_MergePgto` |", "`U4_T_AuditarContas` |")
    assert any("U4_GW_MergePgto: citado 0x" in p for p in problemas_d3(texto, _inventario_head()))


def test_negativo_d3_elemento_citado_duas_vezes() -> None:
    texto = _mutar(
        _mapa(),
        "`U4_T_AuditarContas`, `U4_GW_MergePgto` |",
        "`U4_T_AuditarContas`, `U4_GW_MergePgto`, `U4_End` |",
    )
    assert any("U4_End: citado 2x" in p for p in problemas_d3(texto, _inventario_head()))


def test_negativo_d3_id_citado_que_nao_existe_no_xml() -> None:
    texto = _mutar(
        _mapa(),
        "`U4_T_AuditarContas`, `U4_GW_MergePgto` |",
        "`U4_T_AuditarContas`, `U4_GW_MergePgto`, `U4_X_Fantasma` |",
    )
    assert any("U4_X_Fantasma" in p for p in problemas_d3(texto, _inventario_head()))


def test_negativo_sha256_do_bpmn_diverge_do_cabecalho(tmp_path: Path) -> None:
    alvo = _copiar_fontes(tmp_path)
    ca0 = alvo / "CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn"
    ca0.write_bytes(ca0.read_bytes() + b"\n")
    probs = problemas_sha(_mapa(), tmp_path, _inventario_head())
    assert any("CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn" in p and "cabecalho" in p for p in probs)


def test_negativo_sha256_do_cabecalho_nao_e_64_hex() -> None:
    sha = "b77619a1bdc997b0598627c36ff8273dc6402ac05702acbcad59b0b06381a0e7"
    texto = _mutar(_mapa(), f"| `{sha}` |", "| `a01b1a3e…` |")
    assert any("sem 64 hex" in p for p in problemas_sha(texto, REPO_ROOT, _inventario_head()))


def test_negativo_json_commitado_diverge_da_regeneracao(tmp_path: Path) -> None:
    _copiar_fontes(tmp_path)
    saida = tmp_path / gen.SAIDA_REL
    dados = json.loads(saida.read_text(encoding="utf-8"))
    dados["totais"]["denominador"]["total"] = 359
    saida.write_text(gen.renderizar(dados), encoding="utf-8")
    assert gen.main(["--check", "--root", str(tmp_path)]) == 1


def test_negativo_bpmn_muda_sem_regenerar_o_json(tmp_path: Path) -> None:
    alvo = _copiar_fontes(tmp_path)
    assert gen.main(["--check", "--root", str(tmp_path)]) == 0
    ca3 = alvo / "CA_3_SUPORTE_v4_adaptado.bpmn"
    ca3.write_bytes(ca3.read_bytes() + b"\n")
    assert gen.main(["--check", "--root", str(tmp_path)]) == 1


def test_negativo_flip_aplicado_sem_disposicao_adotada() -> None:
    linha = (
        "| `CP_Start_NovoBeneficiario` | startEvent | cadastro de beneficiário | "
        "— (C-1 `cadastro.py`) | C-1 | **CASA** |"
    )
    texto = _mutar(_mapa(), linha, linha.replace("| **CASA** |", "| LACUNA |"))
    assert any("CP_Start_NovoBeneficiario" in p for p in problemas_flips(texto))


def test_negativo_disposicao_fora_do_vocabulario() -> None:
    texto = _mutar(
        _mapa(),
        "| máquina determinística de consentimento `acesso.py` | pendente |",
        "| máquina determinística de consentimento `acesso.py` | aceito |",
    )
    assert any("vocabulario" in p for p in problemas_flips(texto))


def test_negativo_gerador_recusa_flow_node_de_tipo_desconhecido() -> None:
    xml = (
        b'<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">'
        b'<bpmn:process id="P"><bpmn:transaction id="T1"/></bpmn:process></bpmn:definitions>'
    )
    with pytest.raises(gen.FonteInvalidaError, match="desconhecido"):
        gen.inventariar_arquivo("CA_9_FIXTURE_v4_adaptado.bpmn", xml)


def test_negativo_flip_aplicado_com_estado_vigente_reescrito() -> None:
    linha = (
        "| `CP_Start_NovoBeneficiario` | startEvent | cadastro de beneficiário | "
        "— (C-1 `cadastro.py`) | C-1 | **CASA** |"
    )
    texto = _mutar(_mapa(), linha, linha.replace("| **CASA** |", "| LACUNA |"))
    texto = _mutar(
        texto,
        "| 1 | `CP_Start_NovoBeneficiario` | CA_1 | CASA | DO |",
        "| 1 | `CP_Start_NovoBeneficiario` | CA_1 | LACUNA | DO |",
    )
    assert any("CP_Start_NovoBeneficiario" in p for p in problemas_flips(texto))

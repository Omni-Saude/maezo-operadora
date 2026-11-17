"""Pacote K1 SINTETICO para testar o leitor do manifesto, o gate do pin e o runtime (MZO-2a).

Nenhum byte vem do rco: duas tabelas inventadas e inocuas (`rco_sint_a`, `rco_sint_b`), com a forma
do contrato `regras-contratos-operadora/docs/programa/contrato-k1.md` §3 (manifesto canonico, digests
`sha256:<hex>`, DMN plano em `spec/processes/dmn/rco/`, anexos em `config/integrations/rco/k1/`). A
tabela `rco_sint_b` tem uma saida extra (`acaoSintetica`) listada no seu `saidas[]` (contrato §6).
O pacote tem um exemplo listado no manifesto e NAO vendorizado (fora do lock, contrato §3).

Formato padrao: `formato_manifesto` 2.1.0 com uma prova sintetica (`com_provas`): o pin real exige
`provas[]` (decisao da ROOT, 16/11/2026). Regras com `ativacao`/`exige_aditivo` e `vocabulario.json` com
o bloco `ativacao` (valores + parametros, numeros inventados — nao sao os provisorios do rco).
`montar(raiz, provas=False)` gera o 2.0.0 e `montar(raiz, major=1)` o 1.0.0: o leitor os le, o pin os
recusa (`pin-sem-provas`). `com_composicoes` promove a 2.2.0 com `composicoes[]` (DL-0173).

`montar(raiz)` escreve a arvore e o pin em estado `fixado`; `regravar(raiz, manifesto)` reescreve o
manifesto em bytes canonicos e atualiza `manifest_pin.sha256`, para testar uma mutacao por vez.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

RULES_DIR = "spec/processes/dmn/rco"
K1_DIR = "config/integrations/rco/k1"
LOCK_REL = "config/integrations/rco/rules.lock.json"
COMMIT = "0" * 39 + "1"
REPO = "exemplo/rco-sintetico"
SAIDAS_OBRIGATORIAS = ["decisao", "motivoTab38", "idRegra", "fila", "classeAdverso"]

_DMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="defs_{id}" name="{id}"
  namespace="http://example.invalid/rco-sintetico">
  <decision id="{id}" name="{id} (sintetico)">
    <decisionTable id="dt_{id}" hitPolicy="UNIQUE">
      <input id="in_{id}">
        <inputExpression typeRef="string"><text>tipoSintetico</text></inputExpression>
      </input>
{outputs}
      <rule id="r1_{id}"><inputEntry><text>"A"</text></inputEntry>
{celulas_a}
      </rule>
      <rule id="r2_{id}"><inputEntry><text>not("A")</text></inputEntry>
{celulas_b}
      </rule>
    </decisionTable>
  </decision>
</definitions>
"""


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonico(valor: Any) -> bytes:
    return json.dumps(valor, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def dmn_bytes(decision_id: str, saidas: list[str], linhas: list[list[str]]) -> bytes:
    outputs = "\n".join(f'      <output id="o_{s}" name="{s}" typeRef="string"/>' for s in saidas)

    def celulas(linha: list[str]) -> str:
        return "\n".join(f"        <outputEntry><text>{c}</text></outputEntry>" for c in linha)

    return _DMN.format(
        id=decision_id, outputs=outputs, celulas_a=celulas(linhas[0]), celulas_b=celulas(linhas[1])
    ).encode("utf-8")


_TABELAS: dict[str, tuple[list[str], list[list[str]], list[str]]] = {
    # id_original: (saidas, linhas, idRegra emitidos)
    "sint_a": (
        SAIDAS_OBRIGATORIAS,
        [
            ['"liberar"', '""', '"SINT-A-0001"', '""', '""'],
            ['"negar"', '"3171"', '"SINT-A-0002"', '"cadastro"', '"administrativo"'],
        ],
        ["SINT-A-0001", "SINT-A-0002"],
    ),
    "sint_b": (
        [*SAIDAS_OBRIGATORIAS, "acaoSintetica"],
        [
            ['"liberar"', '""', '""', '""', '""', '"nada"'],  # idRegra "" = vazio canonico
            ['"informar"', '""', '"SINT-B-0001"', '"cadastro"', '""', '"avisar"'],
        ],
        ["SINT-B-0001"],
    ),
}


#: Numeros inventados para o bloco `ativacao.parametros` (so forma; nao sao os provisorios do rco).
PARAMETROS_ATIVACAO: dict[str, int | float] = {
    "revisao_inicial_decisoes": 11,
    "revisao_inicial_prazo_dias": 3,
    "amostra_posterior_pct": 2.5,
    "limiar_reversao_pct": 9,
    "limiar_reversao_min_decisoes": 13,
    "conferencia_negativa_admin_decisoes": 17,
}
ATIVACOES = ["plena", "conferida", "em_correcao"]
#: `combinacao` de `vocabulario.json` (contrato §6), so as chaves que o leitor usa.
COMBINACAO: dict[str, list[str]] = {
    "adversa_exige_preenchidos": ["classeAdverso", "fila", "idRegra", "motivoTab38"],
    "adversas": ["glosar", "negar", "recomendar_negativa"],
}


def _regra(id_regra: str, versao: str, major: int, ativacao: tuple[str, bool]) -> dict[str, Any]:
    regra: dict[str, Any] = {
        "classe_adverso": "",
        "idRegra": id_regra,
        "sha256_yaml": sha(f"yaml sintetico {id_regra} {versao}".encode()),
        "situacao": "vigente",
        "validacao_status": "nao_validada_modo_sombra",
        "versaoRegra": versao,
    }
    if major == 1:
        regra["ratificavel_automatico"] = False
    else:
        regra["ativacao"], regra["exige_aditivo"] = ativacao
    return regra


def vocabulario_bytes(major: int = 2, **ativacao: Any) -> bytes:
    vocab: dict[str, Any] = {
        "combinacao": dict(COMBINACAO),
        "saidas_obrigatorias": SAIDAS_OBRIGATORIAS,
        "vazio": "",
    }
    if major == 2:
        vocab["ativacao"] = {"parametros": dict(PARAMETROS_ATIVACAO), "valores": list(ATIVACOES), **ativacao}
    return canonico(vocab)


def montar(
    raiz: Path,
    *,
    major: int = 2,
    ativacoes: dict[str, tuple[str, bool]] | None = None,
    provas: bool = True,
) -> dict[str, Any]:
    """Escreve o pacote sintetico e o pin `fixado` em `raiz`. Devolve o manifesto (dict).

    `ativacoes`: `idRegra` -> (`ativacao`, `exige_aditivo`) no MAJOR 2; o padrao e `plena` sem aditivo
    em todas (o unico caso que o consumidor executa hoje). `provas` (so no MAJOR 2): promove a 2.1.0
    com a prova sintetica (`com_provas`); sem ela o pacote fica 2.0.0, que o pin recusa.
    """
    ativacoes = ativacoes or {}
    k1 = raiz / K1_DIR
    rco = raiz / RULES_DIR
    k1.mkdir(parents=True, exist_ok=True)
    rco.mkdir(parents=True, exist_ok=True)
    (rco / "README.md").write_text("# rco (teste)\n", encoding="utf-8")

    vocabulario = vocabulario_bytes(major)
    entradas = canonico({"entradas": [], "sha256_conteudo": sha(b"entradas"), "versao": "1.0.0"})
    k6 = canonico({"filas": [], "versao": "1.0.0"})
    (k1 / "vocabulario.json").write_bytes(vocabulario)
    (k1 / "entradas.json").write_bytes(entradas)
    (k1 / "k6-filas.json").write_bytes(k6)

    decisoes = []
    for original, (saidas, linhas, ids) in _TABELAS.items():
        did = f"rco_{original}"
        raw = dmn_bytes(did, saidas, linhas)
        (rco / f"{original}.dmn").write_bytes(raw)
        decisoes.append(
            {
                "arquivo": f"dmn/{original}.dmn",
                "caminho_consumidor": f"{RULES_DIR}/{original}.dmn",
                "dominio": "sintetico",
                "entradas": [
                    {
                        "dominio_valores": "{A, B}",
                        "nome": "tipoSintetico",
                        "nome_maezo": None,
                        "nome_snake": "tipo_sintetico",
                        "origem": "fora_tiss",
                        "typeRef": "string",
                    }
                ],
                "history_ttl": "P180D",
                "hit_policy": "UNIQUE",
                "id": did,
                "id_original": original,
                "regras": [
                    _regra(i, f"1.0.{n}", major, ativacoes.get(i, ("plena", False)))
                    for n, i in enumerate(ids)
                ],
                "saidas": [{"nome": s, "nome_snake": s.lower(), "typeRef": "string"} for s in saidas],
                "sha256": sha(raw),
            }
        )
    manifesto = {
        "decisoes": sorted(decisoes, key=lambda d: d["id"]),
        "dicionario_entradas": {
            "path": "entradas.json",
            "sha256": sha(b"entradas"),
            "sha256_arquivo": sha(entradas),
            "versao": "1.0.0",
        },
        "exemplos_sinteticos": [
            {
                "caso": "C001",
                "classe_adverso": "",
                "decisao": "rco_sint_a",
                "path": "exemplos/rco_sint_a.json",
                "sha256": sha(b"exemplo"),
            }
        ],
        "formato_manifesto": f"{major}.0.0",
        "k6": {"path": "k6-filas.json", "sha256": sha(k6), "versao": "1.0.0"},
        "origem": {"commit_sha": COMMIT, "repo": REPO},
        "prefixo_decisao": "rco_",
        "versao_pacote": "1.0.0",
        "vocabulario": {"path": "vocabulario.json", "sha256": sha(vocabulario)},
    }
    lock = {
        "lock_format": {"name": "rco-rules-lock", "rules_dir": RULES_DIR, "version": "2.0.0"},
        "estado": "fixado",
        "provenance": {"rco_commit_sha": COMMIT, "rco_repo": REPO, "status": "PUBLISHED"},
        "manifest_pin": {"path": f"{K1_DIR}/manifesto.json", "sha256": ""},
        "artifacts": [
            {"path": d["caminho_consumidor"], "sha256": d["sha256"]} for d in manifesto["decisoes"]
        ],
        "exemplos_sinteticos": [],
    }
    (raiz / LOCK_REL).write_text(json.dumps(lock, indent=2), encoding="utf-8")
    regravar(raiz, manifesto)
    if provas and major == 2:
        com_provas(raiz, manifesto)
    return copy.deepcopy(manifesto)


def lock(raiz: Path) -> dict[str, Any]:
    return json.loads((raiz / LOCK_REL).read_text(encoding="utf-8"))


def gravar_lock(raiz: Path, valor: dict[str, Any]) -> None:
    (raiz / LOCK_REL).write_text(json.dumps(valor, indent=2), encoding="utf-8")


def regravar(raiz: Path, manifesto: dict[str, Any], *, raw: bytes | None = None) -> bytes:
    """Grava o manifesto (canonico, ou `raw`) e repina `manifest_pin.sha256` para esses bytes."""
    dados = canonico(manifesto) if raw is None else raw
    (raiz / K1_DIR / "manifesto.json").write_bytes(dados)
    atual = lock(raiz)
    atual["manifest_pin"]["sha256"] = sha(dados)
    gravar_lock(raiz, atual)
    return dados


def trocar_vocabulario(raiz: Path, manifesto: dict[str, Any], raw: bytes) -> None:
    """Grava `vocabulario.json` e repina o hash no manifesto e o manifesto no lock."""
    (raiz / K1_DIR / "vocabulario.json").write_bytes(raw)
    manifesto["vocabulario"]["sha256"] = sha(raw)
    regravar(raiz, manifesto)


PROVA_ID = "prova-sint"
#: Decisao da prova sintetica (sem o prefixo `rco_`, como as do rco): FIRST, linha 1 = celula testada
#: (`true`) -> "casa"; linha 2 = `-` -> "nao_casa" (forma da fixture `teste-unario-booleano`).
PROVA_DECISAO = "prova_sint_c1"
PROVA_DMN = f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="defs_{PROVA_DECISAO}"
  name="{PROVA_DECISAO}" namespace="http://example.invalid/rco-sintetico-prova">
  <decision id="{PROVA_DECISAO}" name="{PROVA_DECISAO} (sintetico)">
    <decisionTable id="dt_{PROVA_DECISAO}" hitPolicy="FIRST">
      <input id="in_{PROVA_DECISAO}">
        <inputExpression typeRef="boolean"><text>flag</text></inputExpression>
      </input>
      <output id="o_{PROVA_DECISAO}" name="resultado" typeRef="string"/>
      <rule id="r1_{PROVA_DECISAO}"><inputEntry><text>true</text></inputEntry>
        <outputEntry><text>"casa"</text></outputEntry></rule>
      <rule id="r2_{PROVA_DECISAO}"><inputEntry><text>-</text></inputEntry>
        <outputEntry><text>"nao_casa"</text></outputEntry></rule>
    </decisionTable>
  </decision>
</definitions>
""".encode()
#: Casos da prova: `flag` true casa, false nao casa.
PROVA_CASOS: dict[str, Any] = {
    "casos": [
        {"entradas": {"flag": True}, "esperado": {"resultado": "casa"}, "id": "C1T"},
        {"entradas": {"flag": False}, "esperado": {"resultado": "nao_casa"}, "id": "C1F"},
    ],
    "decisao": PROVA_DECISAO,
    "dmn": "dmn/prova-sint.dmn",
}
_PROVA_ARQUIVOS: dict[str, bytes] = {
    "dmn/prova-sint.dmn": PROVA_DMN,
    "validacao/casos/c1.json": canonico(PROVA_CASOS),
}


def com_provas(raiz: Path, manifesto: dict[str, Any]) -> dict[str, Any]:
    """Promove o pacote a `formato_manifesto` 2.1.0 com uma prova sintetica e grava os arquivos dela."""
    manifesto["formato_manifesto"] = "2.1.0"
    arquivos = []
    for rel, raw in _PROVA_ARQUIVOS.items():
        destino = raiz / K1_DIR / "provas" / PROVA_ID / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(raw)
        arquivos.append({"caminho": f"provas/{PROVA_ID}/{rel}", "sha256": sha(raw)})
    manifesto["provas"] = [
        {
            "arquivos": arquivos,
            "descricao": "prova sintetica",
            "esperado": f"provas/{PROVA_ID}/validacao/casos",
            "id": PROVA_ID,
        }
    ]
    regravar(raiz, manifesto)
    return manifesto


def provas_vazias(raiz: Path, manifesto: dict[str, Any]) -> dict[str, Any]:
    """Pacote 2.1.0 com `provas: []` e sem a pasta `provas/` (so a lista vazia difere do pacote valido)."""
    manifesto["formato_manifesto"] = "2.1.0"
    manifesto["provas"] = []
    shutil.rmtree(raiz / K1_DIR / "provas", ignore_errors=True)
    regravar(raiz, manifesto)
    return manifesto


#: Grupo sintetico de `composicoes[]` (formato 2.2.0, DL-0173) sobre as duas tabelas do pacote:
#: `rco_sint_a` avaliada primeiro, `rco_sint_b` e o padrao (a ultima).
COMPOSICAO_NOME = "grupo_sint"
COMPOSICAO: dict[str, Any] = {
    "decisoes": ["rco_sint_a", "rco_sint_b"],
    "nome": COMPOSICAO_NOME,
    "padrao": "rco_sint_b",
    "regra": "primeira_com_idRegra",
}


def com_composicoes(
    raiz: Path,
    manifesto: dict[str, Any],
    composicoes: list[dict[str, Any]] | None = None,
    *,
    campo: bool = True,
) -> dict[str, Any]:
    """Promove o pacote (ja 2.1.0 com provas) a `formato_manifesto` 2.2.0 com `composicoes[]`.

    `composicoes` padrao: `[COMPOSICAO]`. `campo=False`: 2.2.0 sem o campo (vale lista vazia).
    """
    if "provas" not in manifesto:
        com_provas(raiz, manifesto)
    manifesto["formato_manifesto"] = "2.2.0"
    if campo:
        manifesto["composicoes"] = copy.deepcopy([COMPOSICAO] if composicoes is None else composicoes)
    else:
        manifesto.pop("composicoes", None)
    regravar(raiz, manifesto)
    return manifesto


#: Terceira tabela sintetica (so com `com_tabela_c`), para um grupo na forma da ortopedia: duas
#: decisoes anteriores (`rco_sint_a`, `rco_sint_c`) e o padrao (`rco_sint_b`).
_TABELA_C: tuple[list[str], list[list[str]], list[str]] = (
    SAIDAS_OBRIGATORIAS,
    [
        ['"glosar"', '"1801"', '"SINT-C-0001"', '"cadastro"', '"administrativo"'],
        ['"liberar"', '""', '""', '""', '""'],
    ],
    ["SINT-C-0001"],
)
COMPOSICAO_TRES: dict[str, Any] = {
    "decisoes": ["rco_sint_a", "rco_sint_c", "rco_sint_b"],
    "nome": "grupo_sint_tres",
    "padrao": "rco_sint_b",
    "regra": "primeira_com_idRegra",
}


def com_tabela_c(raiz: Path, manifesto: dict[str, Any]) -> dict[str, Any]:
    """Acrescenta a tabela `rco_sint_c` ao pacote (DMN, `decisoes[]` e `artifacts` do lock) e repina."""
    saidas, linhas, ids = _TABELA_C
    raw = dmn_bytes("rco_sint_c", saidas, linhas)
    (raiz / RULES_DIR / "sint_c.dmn").write_bytes(raw)
    modelo = copy.deepcopy(manifesto["decisoes"][0])
    modelo.update(
        {
            "arquivo": "dmn/sint_c.dmn",
            "caminho_consumidor": f"{RULES_DIR}/sint_c.dmn",
            "id": "rco_sint_c",
            "id_original": "sint_c",
            "regras": [_regra(i, f"1.0.{n}", 2, ("plena", False)) for n, i in enumerate(ids)],
            "saidas": [{"nome": s, "nome_snake": s.lower(), "typeRef": "string"} for s in saidas],
            "sha256": sha(raw),
        }
    )
    manifesto["decisoes"] = sorted([*manifesto["decisoes"], modelo], key=lambda d: d["id"])
    atual = lock(raiz)
    atual["artifacts"] = [
        {"path": d["caminho_consumidor"], "sha256": d["sha256"]} for d in manifesto["decisoes"]
    ]
    gravar_lock(raiz, atual)
    regravar(raiz, manifesto)
    return manifesto

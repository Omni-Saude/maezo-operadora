"""Pacote K1 SINTETICO para testar o leitor do manifesto, o gate do pin e o runtime (MZO-2a).

Nenhum byte vem do rco: duas tabelas inventadas e inocuas (`rco_sint_a`, `rco_sint_b`), com a forma
do contrato `regras-contratos-operadora/docs/programa/contrato-k1.md` §3 (manifesto canonico, digests
`sha256:<hex>`, DMN plano em `spec/processes/dmn/rco/`, anexos em `config/integrations/rco/k1/`). A
tabela `rco_sint_b` tem uma saida extra (`acaoSintetica`) listada no seu `saidas[]` (contrato §6).
O pacote tem um exemplo listado no manifesto e NAO vendorizado (fora do lock, contrato §3).

`montar(raiz)` escreve a arvore e o pin em estado `fixado`; `regravar(raiz, manifesto)` reescreve o
manifesto em bytes canonicos e atualiza `manifest_pin.sha256`, para testar uma mutacao por vez.
"""

from __future__ import annotations

import copy
import hashlib
import json
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
            ['"informar"', '""', '"SINT-A-0002"', '"cadastro"', '""'],
        ],
        ["SINT-A-0001", "SINT-A-0002"],
    ),
    "sint_b": (
        [*SAIDAS_OBRIGATORIAS, "acaoSintetica"],
        [
            ['"liberar"', '""', '"SINT-B-0001"', '""', '""', '"nada"'],
            ['"informar"', '""', '"SINT-B-0001"', '"cadastro"', '""', '"avisar"'],
        ],
        ["SINT-B-0001"],
    ),
}


def _regra(id_regra: str, versao: str) -> dict[str, Any]:
    return {
        "classe_adverso": "",
        "idRegra": id_regra,
        "ratificavel_automatico": False,
        "sha256_yaml": sha(f"yaml sintetico {id_regra} {versao}".encode()),
        "situacao": "vigente",
        "validacao_status": "nao_validada_modo_sombra",
        "versaoRegra": versao,
    }


def montar(raiz: Path) -> dict[str, Any]:
    """Escreve o pacote sintetico e o pin `fixado` em `raiz`. Devolve o manifesto (dict)."""
    k1 = raiz / K1_DIR
    rco = raiz / RULES_DIR
    k1.mkdir(parents=True, exist_ok=True)
    rco.mkdir(parents=True, exist_ok=True)
    (rco / "README.md").write_text("# rco (teste)\n", encoding="utf-8")

    vocabulario = canonico({"saidas_obrigatorias": SAIDAS_OBRIGATORIAS, "vazio": ""})
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
                "regras": [_regra(i, f"1.0.{n}") for n, i in enumerate(ids)],
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
        "formato_manifesto": "1.0.0",
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

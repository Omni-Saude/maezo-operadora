"""Forma do catálogo de regras `maezo-regras-catalogo.v1` e validação por campo (blueprint §3.2).

A MESMA validação serve ao CI, ao `regras-sync` e ao portal: tudo que é regra de forma mora aqui.
`validar_catalogo` devolve o catálogo tipado (dataclasses congeladas) e a lista de achados; com
qualquer achado, o catálogo não serve (falha fechada). Recalcular o hash é `hash.regra_sha256`.

Nada aqui escreve, aprova ou infere aprovação: `aprovacoes`/`liberacoes`/`comprovacoes` só são
LIDAS e conferidas na forma. Quem escreve é o exportador (transcrição de recibo) ou, na fase 1, a
transcrição manual de uma PESSOA do time técnico (DL-0194, PROPOSTA; ADR-0007).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from maezo.regras.canonico import normalizar_fim_de_linha, sha256_hex
from maezo.regras.catalogo.extratores import CHAVES_DO_LOCALIZADOR, TIPOS_DE_FONTE

SCHEMA_CATALOGO: Final[str] = "maezo-regras-catalogo.v1"

#: Papéis fechados (mesmo conjunto do registro DL-0148 e do gate de signoff).
PAPEIS: Final[frozenset[str]] = frozenset(
    {"medico-auditor", "juridico", "dpo", "regulatorio", "financas", "po", "dono"}
)
ESTADOS_DECLARADOS: Final[tuple[str, ...]] = ("legado_pendente", "ativa", "em_remocao", "removida")
VALIDADES: Final[tuple[str, ...]] = ("P1Y",)  # decisão 7: único valor aceito agora
TIPOS_EDITAVEL: Final[tuple[str, ...]] = (
    "prazo",
    "valor",
    "percentual",
    "numero",
    "opcao",
    "sim_nao",
    "texto",
)
UNIDADES_DE_PRAZO: Final[frozenset[str]] = frozenset({"dias_uteis", "dias_corridos", "horas"})
DECISOES_TRANSCRITAS: Final[frozenset[str]] = frozenset({"de_acordo", "de_acordo_com_ressalva", "importada"})
TIPOS_DE_RESSALVA: Final[frozenset[str]] = frozenset({"observacao", "condicao"})

_RE_PROCESSO: Final[re.Pattern[str]] = re.compile(r"^SP-OP-[A-Z0-9-]+-\d{3}$")
_RE_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]*(\.[A-Z0-9_]+){1,4}$")
_ID_MAX: Final[int] = 96
_RE_ALIAS: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2,4}-[A-Z]{0,4}-?\d{2}$")
_RE_SHA: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_RE_NORMA_REF: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9.-]{3,40}$")
_RE_CAMPO: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_RE_VARIAVEL: Final[re.Pattern[str]] = re.compile(r"\{[a-z_]+\}")
_RE_RECIBO: Final[re.Pattern[str]] = re.compile(r"^rcb_[0-9A-HJKMNP-TV-Z]{26}$")
_RE_COMMIT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_PASTA_NORMAS: Final[str] = "spec/regras/normas/"
_CONTRATOS: Final[str] = "docs/processes/contracts"

#: Dado pessoal que não pode aparecer em regra/exemplo (exemplos usam nomes fictícios). Mesmos
#: padrões do redator de log do gateway (`maezo.gateway.log_scrubber._PHI_PATTERNS`), copiados para
#: a biblioteca não depender do gateway.
_PII: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("CPF", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    ("e-mail", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("telefone", re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?[\s-]?9?\d{4}[\s-]?\d{4}\b")),
)

#: Limites de tamanho (caracteres) dos textos de negócio: (mínimo, máximo).
LIMITES_DE_TEXTO: Final[Mapping[str, tuple[int, int]]] = {
    "titulo": (5, 120),
    "regra": (10, 2000),
    "exemplo": (10, 1500),
    "orientacao": (0, 1500),
    "o_que_e": (10, 600),
    "o_que_nao_e": (10, 600),
}

_CHAVES_CATALOGO: Final[frozenset[str]] = frozenset({"schema", "processo", "regras"})
_CHAVES_REGRA_OBRIGATORIAS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "estado_declarado",
        "tipo",
        "fonte",
        "sha256",
        "aprovam",
        "ciencia",
        "titulo",
        "regra",
        "exemplo",
        "orientacao",
        "o_que_e",
        "o_que_nao_e",
        "norma",
        "validade",
        "editavel",
        "historico_de_mudanca",
        "aprovacoes",
        "liberacoes",
        "comprovacoes",
    }
)
_CHAVES_REGRA: Final[frozenset[str]] = _CHAVES_REGRA_OBRIGATORIAS | {"alias"}


@dataclass(frozen=True, slots=True)
class Norma:
    ref: str
    arquivo: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Regra:
    id: str
    alias: str | None
    estado_declarado: str
    tipo: str
    fonte: tuple[Mapping[str, Any], ...]
    sha256: str
    aprovam: tuple[str, ...]
    ciencia: tuple[str, ...]
    titulo: str
    regra: str
    exemplo: str
    orientacao: str
    o_que_e: str
    o_que_nao_e: str
    norma: Norma | None
    validade: str
    editavel: tuple[Mapping[str, Any], ...]
    historico_de_mudanca: tuple[Mapping[str, Any], ...]
    aprovacoes: tuple[Mapping[str, Any], ...]
    liberacoes: tuple[Mapping[str, Any], ...]
    comprovacoes: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class Catalogo:
    processo: str
    arquivo: str
    regras: tuple[Regra, ...]


def somar_validade(inicio: date, validade: str) -> date:
    """`inicio + validade` para `P1Y` (29/02 vira 28/02 no ano seguinte)."""
    if validade != "P1Y":
        raise ValueError(f"validade {validade!r} nao suportada")
    try:
        return inicio.replace(year=inicio.year + 1)
    except ValueError:
        return inicio.replace(year=inicio.year + 1, day=28)


def sha256_arquivo_normalizado(caminho: Path) -> str:
    return sha256_hex(normalizar_fim_de_linha(caminho.read_bytes()))


class _Coletor:
    def __init__(self, prefixo: str) -> None:
        self.prefixo = prefixo
        self.achados: list[str] = []

    def add(self, onde: str, msg: str) -> None:
        self.achados.append(f"{self.prefixo}{onde}: {msg}")


def _texto(c: _Coletor, onde: str, campo: str, valor: Any, minimo: int, maximo: int) -> str:
    if not isinstance(valor, str):
        c.add(onde, f"`{campo}` tem de ser texto")
        return ""
    if not minimo <= len(valor.strip()) <= maximo:
        c.add(onde, f"`{campo}` com {len(valor.strip())} caracteres (aceito {minimo}..{maximo})")
    for nome, padrao in _PII:
        if padrao.search(valor):
            c.add(
                onde, f"`{campo}` parece conter {nome} — exemplos usam so' nomes ficticios, sem dado pessoal"
            )
    return valor


def _lista_de_papeis(c: _Coletor, onde: str, campo: str, valor: Any, minimo: int) -> tuple[str, ...]:
    if not isinstance(valor, list) or not all(isinstance(p, str) for p in valor):
        c.add(onde, f"`{campo}` tem de ser lista de papeis")
        return ()
    if not minimo <= len(valor) <= len(PAPEIS):
        c.add(onde, f"`{campo}` com {len(valor)} papel(is) (aceito {minimo}..{len(PAPEIS)})")
    repetidos = sorted({p for p in valor if valor.count(p) > 1})
    if repetidos:
        c.add(onde, f"`{campo}` repete {repetidos}")
    desconhecidos = sorted(set(valor) - PAPEIS)
    if desconhecidos:
        c.add(onde, f"`{campo}` com papel(is) desconhecido(s) {desconhecidos} (validos: {sorted(PAPEIS)})")
    return tuple(valor)


def _data(c: _Coletor, onde: str, campo: str, valor: Any, hoje: date) -> date | None:
    if isinstance(valor, date):
        quando = valor
    elif isinstance(valor, str):
        try:
            quando = date.fromisoformat(valor)
        except ValueError:
            c.add(onde, f"`{campo}={valor!r}` nao e' data ISO (AAAA-MM-DD)")
            return None
    else:
        c.add(onde, f"`{campo}` ausente ou nao e' data")
        return None
    if quando > hoje:
        c.add(onde, f"`{campo}` {quando.isoformat()} esta' no futuro")
    return quando


def _sha(c: _Coletor, onde: str, campo: str, valor: Any) -> str:
    if not isinstance(valor, str) or not _RE_SHA.match(valor):
        c.add(onde, f"`{campo}` tem de ser 64 hex minusculos")
        return ""
    return valor


def _https(c: _Coletor, onde: str, campo: str, valor: Any) -> None:
    if not isinstance(valor, str) or not valor.startswith("https://") or len(valor) > 500:
        c.add(onde, f"`{campo}` tem de ser URL https de ate' 500 caracteres")


def _mapa_fechado(
    c: _Coletor, onde: str, campo: str, valor: Any, obrigatorias: frozenset[str], opcionais: frozenset[str]
) -> Mapping[str, Any] | None:
    if not isinstance(valor, dict) or not all(isinstance(k, str) for k in valor):
        c.add(onde, f"`{campo}` tem de ser um mapa")
        return None
    faltam = sorted(obrigatorias - set(valor))
    sobram = sorted(set(valor) - obrigatorias - opcionais)
    if faltam:
        c.add(onde, f"`{campo}` sem {faltam}")
    if sobram:
        c.add(onde, f"`{campo}` com chave(s) fora do formato {sobram}")
    return valor if not faltam and not sobram else None


def _fontes(c: _Coletor, onde: str, tipo: str, valor: Any, raiz: Path) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(valor, list) or not 1 <= len(valor) <= 10:
        c.add(onde, "`fonte` tem de ser lista com 1..10 itens")
        return ()
    chaves = CHAVES_DO_LOCALIZADOR.get(tipo)
    if chaves is None:
        return ()
    fontes: list[Mapping[str, Any]] = []
    for i, item in enumerate(valor, start=1):
        fonte = _mapa_fechado(c, onde, f"fonte[{i}]", item, chaves, frozenset())
        if fonte is None:
            continue
        if not isinstance(fonte["arquivo"], str) or not fonte["arquivo"]:
            c.add(onde, f"`fonte[{i}].arquivo` tem de ser texto")
            continue
        if "caminho" in chaves:
            caminho = fonte["caminho"]
            if (
                not isinstance(caminho, list)
                or not caminho
                or not all(isinstance(p, str) or type(p) is int for p in caminho)
            ):
                c.add(onde, f"`fonte[{i}].caminho` tem de ser lista nao vazia de chaves/indices")
                continue
        for chave in chaves - {"arquivo", "caminho"}:
            if not isinstance(fonte[chave], str) or not fonte[chave].strip():
                c.add(onde, f"`fonte[{i}].{chave}` tem de ser texto nao vazio")
        fontes.append(fonte)
    vistos = [repr(sorted(f.items())) for f in fontes]
    if len(set(vistos)) != len(vistos):
        c.add(onde, "`fonte` repete o mesmo localizador")
    return tuple(fontes)


def _norma(c: _Coletor, onde: str, valor: Any, raiz: Path) -> Norma | None:
    if valor is None:
        return None
    norma = _mapa_fechado(c, onde, "norma", valor, frozenset({"ref", "arquivo", "sha256"}), frozenset())
    if norma is None:
        return None
    ref, arquivo = norma["ref"], norma["arquivo"]
    if not isinstance(ref, str) or not _RE_NORMA_REF.match(ref):
        c.add(onde, "`norma.ref` fora do formato ^[A-Z0-9.-]{3,40}$")
    sha = _sha(c, onde, "norma.sha256", norma["sha256"])
    if not isinstance(arquivo, str) or not arquivo.startswith(_PASTA_NORMAS) or ".." in arquivo:
        c.add(onde, f"`norma.arquivo` tem de estar em {_PASTA_NORMAS}")
        return None
    caminho = raiz / arquivo
    if not caminho.is_file():
        c.add(onde, f"`norma.arquivo` {arquivo} nao existe")
        return None
    atual = sha256_arquivo_normalizado(caminho)
    if sha and atual != sha:
        c.add(
            onde,
            f"a norma mudou: `norma.sha256` declarado {sha[:12]}…, arquivo tem {atual} — a regra revalida",
        )
    return Norma(ref=str(ref), arquivo=arquivo, sha256=sha)


def _inteiro(valor: Any) -> bool:
    return type(valor) is int


def _editavel(c: _Coletor, onde: str, valor: Any) -> tuple[Mapping[str, Any], ...]:
    """Bloco `editavel` (7 tipos). A semântica do `endereco` (apontar o lugar exato) é conferida
    pela onda 6, junto com o formulário; aqui a forma e as relações entre campos."""
    if not isinstance(valor, list):
        c.add(onde, "`editavel` tem de ser lista")
        return ()
    comuns = frozenset({"campo", "tipo", "endereco"})
    relacoes = frozenset({"menor_que", "maior_que"})
    especificos: Mapping[str, frozenset[str]] = {
        "prazo": frozenset({"unidades", "minimo", "maximo"}),
        "valor": frozenset({"minimo", "maximo"}),
        "percentual": frozenset({"minimo", "maximo"}),
        "numero": frozenset({"minimo", "maximo"}),
        "opcao": frozenset({"opcoes"}),
        "sim_nao": frozenset(),
        "texto": frozenset({"max", "valor_atual"}),
    }
    itens: list[Mapping[str, Any]] = []
    tipos: dict[str, str] = {}
    for i, bruto in enumerate(valor, start=1):
        rotulo = f"editavel[{i}]"
        if not isinstance(bruto, dict):
            c.add(onde, f"`{rotulo}` tem de ser mapa")
            continue
        tipo = bruto.get("tipo")
        if not isinstance(tipo, str) or tipo not in especificos:
            c.add(onde, f"`{rotulo}.tipo` fora de {list(TIPOS_EDITAVEL)}")
            continue
        opcionais = relacoes | ({"variaveis_travadas"} if tipo == "texto" else frozenset())
        item = _mapa_fechado(c, onde, rotulo, bruto, comuns | especificos[tipo], frozenset(opcionais))
        if item is None:
            continue
        campo = item["campo"]
        if not isinstance(campo, str) or not _RE_CAMPO.match(campo):
            c.add(onde, f"`{rotulo}.campo` fora de ^[a-z][a-z0-9_]{{1,40}}$")
            continue
        if campo in tipos:
            c.add(onde, f"`{rotulo}.campo` {campo!r} repetido")
            continue
        tipos[campo] = str(tipo)
        if not isinstance(item["endereco"], dict) or not item["endereco"]:
            c.add(onde, f"`{rotulo}.endereco` tem de ser mapa nao vazio (lugar exato do valor)")
        if tipo in ("prazo", "valor", "percentual", "numero"):
            mn, mx = item["minimo"], item["maximo"]
            if not (_inteiro(mn) and _inteiro(mx)) or mn > mx:
                c.add(onde, f"`{rotulo}`: minimo/maximo tem de ser inteiros com minimo <= maximo")
            elif tipo == "prazo" and mn < 1:
                c.add(onde, f"`{rotulo}`: prazo tem minimo >= 1")
            elif tipo == "percentual" and not 0 <= mn <= mx <= 100:
                c.add(onde, f"`{rotulo}`: percentual exige 0 <= minimo <= maximo <= 100")
        if tipo == "prazo":
            unidades = item["unidades"]
            if not isinstance(unidades, list) or not unidades or not set(unidades) <= UNIDADES_DE_PRAZO:
                c.add(onde, f"`{rotulo}.unidades` subconjunto nao vazio de {sorted(UNIDADES_DE_PRAZO)}")
        if tipo == "opcao":
            opcoes = item["opcoes"]
            if (
                not isinstance(opcoes, list)
                or not 2 <= len(opcoes) <= 10
                or len(set(map(str, opcoes))) != len(opcoes)
            ):
                c.add(onde, f"`{rotulo}.opcoes` com 2..10 opcoes unicas")
        if tipo == "texto":
            mx_txt, atual = item["max"], item["valor_atual"]
            if not _inteiro(mx_txt) or not 1 <= mx_txt <= 2000:
                c.add(onde, f"`{rotulo}.max` inteiro em 1..2000")
            travadas = item.get("variaveis_travadas", [])
            if not isinstance(travadas, list) or not all(
                isinstance(v, str) and _RE_VARIAVEL.fullmatch(v) for v in travadas
            ):
                c.add(onde, f"`{rotulo}.variaveis_travadas` so' com {{nome_em_minusculas}}")
            elif not isinstance(atual, str) or any(v not in atual for v in travadas):
                c.add(onde, f"`{rotulo}`: toda variavel travada tem de estar no `valor_atual`")
        itens.append(item)
    comparaveis = {"prazo", "valor", "percentual", "numero"}
    for item in itens:
        for relacao in ("menor_que", "maior_que"):
            if relacao not in item:
                continue
            alvo = item[relacao]
            if alvo not in tipos:
                c.add(onde, f"`editavel.{item['campo']}.{relacao}` aponta para campo inexistente {alvo!r}")
            elif (
                alvo == item["campo"] or tipos[alvo] != tipos[item["campo"]] or tipos[alvo] not in comparaveis
            ):
                c.add(
                    onde, f"`editavel.{item['campo']}.{relacao}` exige outro campo do mesmo tipo comparavel"
                )
    return tuple(itens)


def _recibo_ou_transcricao(c: _Coletor, onde: str, rotulo: str, item: Mapping[str, Any]) -> None:
    """Fase 4+: `recibo` + `evidencia`. Fase 1: `recibo: null` + `transcricao_manual` (pessoa do
    time técnico, com evidência). O CI da onda 4 só aceita a manual se ela já estava no `main` no corte."""
    recibo = item.get("recibo")
    if recibo is None:
        manual = _mapa_fechado(
            c,
            onde,
            f"{rotulo}.transcricao_manual",
            item.get("transcricao_manual"),
            frozenset({"evidencia_url", "evidencia_sha256", "transcrito_por"}),
            frozenset(),
        )
        if manual is not None:
            _https(c, onde, f"{rotulo}.transcricao_manual.evidencia_url", manual["evidencia_url"])
            _sha(c, onde, f"{rotulo}.transcricao_manual.evidencia_sha256", manual["evidencia_sha256"])
            if not isinstance(manual["transcrito_por"], str) or not manual["transcrito_por"].strip():
                c.add(onde, f"`{rotulo}.transcricao_manual.transcrito_por` (pessoa) obrigatorio")
        if "evidencia" in item:
            c.add(onde, f"`{rotulo}.evidencia` so' existe com recibo")
        return
    if "transcricao_manual" in item:
        c.add(onde, f"`{rotulo}`: `recibo` e `transcricao_manual` sao exclusivos")
    rec = _mapa_fechado(
        c, onde, f"{rotulo}.recibo", recibo, frozenset({"id", "sha256", "cofre_version_id"}), frozenset()
    )
    if rec is not None:
        if not isinstance(rec["id"], str) or not _RE_RECIBO.match(rec["id"]):
            c.add(onde, f"`{rotulo}.recibo.id` fora de ^rcb_<ULID>$")
        _sha(c, onde, f"{rotulo}.recibo.sha256", rec["sha256"])
        if not isinstance(rec["cofre_version_id"], str) or not rec["cofre_version_id"].strip():
            c.add(onde, f"`{rotulo}.recibo.cofre_version_id` obrigatorio")
    ev = _mapa_fechado(
        c,
        onde,
        f"{rotulo}.evidencia",
        item.get("evidencia"),
        frozenset({"url", "sha256", "commit"}),
        frozenset(),
    )
    if ev is not None:
        _https(c, onde, f"{rotulo}.evidencia.url", ev["url"])
        _sha(c, onde, f"{rotulo}.evidencia.sha256", ev["sha256"])
        if not isinstance(ev["commit"], str) or not _RE_COMMIT.match(ev["commit"]):
            c.add(onde, f"`{rotulo}.evidencia.commit` 40 hex")


_COMUNS_TRANSCRITAS: Final[frozenset[str]] = frozenset({"recibo", "transcricao_manual", "evidencia"})


def _aprovacoes(
    c: _Coletor, onde: str, valor: Any, aprovam: Sequence[str], validade: str, hoje: date
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(valor, list):
        c.add(onde, "`aprovacoes` tem de ser lista")
        return ()
    obrig = frozenset({"papel", "aprovador", "decisao", "regra_sha256", "data", "valida_ate", "recibo"})
    for i, item in enumerate(valor, start=1):
        rotulo = f"aprovacoes[{i}]"
        if not isinstance(item, dict):
            c.add(onde, f"`{rotulo}` tem de ser mapa")
            continue
        ok = _mapa_fechado(c, onde, rotulo, item, obrig, _COMUNS_TRANSCRITAS | {"ressalva"})
        if ok is None:
            continue
        if item["papel"] not in aprovam:
            c.add(onde, f"`{rotulo}.papel` {item['papel']!r} nao esta' em `aprovam` {list(aprovam)}")
        if item["decisao"] == "importada" and item["papel"] != "dono":
            c.add(onde, f"`{rotulo}`: importacao de DL so' com papel `dono`")
        if not isinstance(item["aprovador"], str) or not item["aprovador"].strip():
            c.add(onde, f"`{rotulo}.aprovador` (nome do cadastro) obrigatorio")
        if item["decisao"] not in DECISOES_TRANSCRITAS:
            c.add(onde, f"`{rotulo}.decisao` fora de {sorted(DECISOES_TRANSCRITAS)}")
        tem_ressalva = item["decisao"] == "de_acordo_com_ressalva"
        if tem_ressalva != ("ressalva" in item):
            c.add(onde, f"`{rotulo}.ressalva` obrigatoria sse `de_acordo_com_ressalva`")
        elif tem_ressalva:
            ressalva = _mapa_fechado(
                c, onde, f"{rotulo}.ressalva", item["ressalva"], frozenset({"tipo", "texto"}), frozenset()
            )
            if ressalva is not None:
                if ressalva["tipo"] not in TIPOS_DE_RESSALVA:
                    c.add(onde, f"`{rotulo}.ressalva.tipo` fora de {sorted(TIPOS_DE_RESSALVA)}")
                _texto(c, onde, f"{rotulo}.ressalva.texto", ressalva["texto"], 10, 2000)
        _sha(c, onde, f"{rotulo}.regra_sha256", item["regra_sha256"])
        quando = _data(c, onde, f"{rotulo}.data", item["data"], hoje)
        if quando is not None:
            ate = item["valida_ate"]
            esperado = somar_validade(quando, validade).isoformat()
            if str(ate) != esperado:
                c.add(onde, f"`{rotulo}.valida_ate` tem de ser {esperado} (data + {validade})")
        _recibo_ou_transcricao(c, onde, rotulo, item)
    return tuple(valor)


def _liberacoes(c: _Coletor, onde: str, valor: Any, hoje: date) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(valor, list):
        c.add(onde, "`liberacoes` tem de ser lista")
        return ()
    obrig = frozenset({"liberador", "regra_sha256", "pr", "data", "recibo"})
    for i, item in enumerate(valor, start=1):
        rotulo = f"liberacoes[{i}]"
        ok = _mapa_fechado(c, onde, rotulo, item, obrig, _COMUNS_TRANSCRITAS)
        if ok is None:
            continue
        if not isinstance(item["liberador"], str) or not item["liberador"].strip():
            c.add(onde, f"`{rotulo}.liberador` obrigatorio")
        if item["pr"] is not None and (not _inteiro(item["pr"]) or item["pr"] < 1):
            c.add(onde, f"`{rotulo}.pr` numero do PR (ou null para regra legado no main)")
        _sha(c, onde, f"{rotulo}.regra_sha256", item["regra_sha256"])
        _data(c, onde, f"{rotulo}.data", item["data"], hoje)
        _recibo_ou_transcricao(c, onde, rotulo, item)
    return tuple(valor)


def _comprovacoes(
    c: _Coletor, onde: str, valor: Any, aprovam: Sequence[str], hoje: date
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(valor, list):
        c.add(onde, "`comprovacoes` tem de ser lista")
        return ()
    obrig = frozenset(
        {"papel_da_condicao", "regra_sha256", "comprovacao", "comprovado_por", "data", "recibo"}
    )
    for i, item in enumerate(valor, start=1):
        rotulo = f"comprovacoes[{i}]"
        ok = _mapa_fechado(c, onde, rotulo, item, obrig, _COMUNS_TRANSCRITAS)
        if ok is None:
            continue
        if item["papel_da_condicao"] not in aprovam:
            c.add(onde, f"`{rotulo}.papel_da_condicao` nao esta' em `aprovam`")
        prova = _mapa_fechado(
            c, onde, f"{rotulo}.comprovacao", item["comprovacao"], frozenset({"url", "sha256"}), frozenset()
        )
        if prova is not None:
            _https(c, onde, f"{rotulo}.comprovacao.url", prova["url"])
            _sha(c, onde, f"{rotulo}.comprovacao.sha256", prova["sha256"])
        if not isinstance(item["comprovado_por"], str) or not item["comprovado_por"].strip():
            c.add(onde, f"`{rotulo}.comprovado_por` obrigatorio")
        _sha(c, onde, f"{rotulo}.regra_sha256", item["regra_sha256"])
        _data(c, onde, f"{rotulo}.data", item["data"], hoje)
        _recibo_ou_transcricao(c, onde, rotulo, item)
    return tuple(valor)


def _historico(c: _Coletor, onde: str, valor: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(valor, list):
        c.add(onde, "`historico_de_mudanca` tem de ser lista")
        return ()
    for i, item in enumerate(valor, start=1):
        rotulo = f"historico_de_mudanca[{i}]"
        ok = _mapa_fechado(c, onde, rotulo, item, frozenset({"pr", "o_que_mudou", "motivo"}), frozenset())
        if ok is None:
            continue
        if not _inteiro(item["pr"]) or item["pr"] < 1:
            c.add(onde, f"`{rotulo}.pr` numero do PR")
        _texto(c, onde, f"{rotulo}.o_que_mudou", item["o_que_mudou"], 5, 600)
        _texto(c, onde, f"{rotulo}.motivo", item["motivo"], 5, 600)
    return tuple(valor)


def _regra(c: _Coletor, bruto: Any, i: int, raiz: Path, hoje: date) -> Regra | None:
    onde = f"regras[{i}]"
    if not isinstance(bruto, dict):
        c.add(onde, "tem de ser mapa")
        return None
    rid = bruto.get("id")
    if isinstance(rid, str):
        onde = f"{rid}"
    ok = _mapa_fechado(c, onde, "regra", bruto, _CHAVES_REGRA_OBRIGATORIAS, frozenset({"alias"}))
    if ok is None:
        return None
    antes = len(c.achados)
    if not isinstance(rid, str) or len(rid) > _ID_MAX or not _RE_ID.match(rid):
        c.add(onde, f"`id` fora de ^[A-Z][A-Z0-9_]*(\\.[A-Z0-9_]+){{1,4}}$ (ate' {_ID_MAX})")
    alias = bruto.get("alias")
    if alias is not None and (not isinstance(alias, str) or not _RE_ALIAS.match(alias)):
        c.add(onde, "`alias` fora de ^[A-Z]{2,4}-[A-Z]{0,4}-?\\d{2}$")
    if bruto["estado_declarado"] not in ESTADOS_DECLARADOS:
        c.add(onde, f"`estado_declarado` fora de {list(ESTADOS_DECLARADOS)}")
    tipo = bruto["tipo"]
    if tipo not in TIPOS_DE_FONTE:
        c.add(onde, f"`tipo` fora de {list(TIPOS_DE_FONTE)}")
    fontes = _fontes(c, onde, str(tipo), bruto["fonte"], raiz)
    sha = _sha(c, onde, "sha256", bruto["sha256"])
    aprovam = _lista_de_papeis(c, onde, "aprovam", bruto["aprovam"], 1)
    ciencia = _lista_de_papeis(c, onde, "ciencia", bruto["ciencia"], 0)
    comuns = sorted(set(aprovam) & set(ciencia))
    if comuns:
        c.add(onde, f"`ciencia` e `aprovam` tem de ser disjuntos (ambos com {comuns})")
    textos = {
        campo: _texto(c, onde, campo, bruto[campo], mn, mx) for campo, (mn, mx) in LIMITES_DE_TEXTO.items()
    }
    norma = _norma(c, onde, bruto["norma"], raiz)
    validade = bruto["validade"]
    if validade not in VALIDADES:
        c.add(onde, f"`validade` so' aceita {list(VALIDADES)}")
        validade = "P1Y"
    editavel = _editavel(c, onde, bruto["editavel"])
    historico = _historico(c, onde, bruto["historico_de_mudanca"])
    aprovacoes = _aprovacoes(c, onde, bruto["aprovacoes"], aprovam, str(validade), hoje)
    liberacoes = _liberacoes(c, onde, bruto["liberacoes"], hoje)
    comprovacoes = _comprovacoes(c, onde, bruto["comprovacoes"], aprovam, hoje)
    if len(c.achados) != antes:
        return None
    return Regra(
        id=str(rid),
        alias=alias,
        estado_declarado=str(bruto["estado_declarado"]),
        tipo=str(tipo),
        fonte=fontes,
        sha256=sha,
        aprovam=aprovam,
        ciencia=ciencia,
        norma=norma,
        validade=str(validade),
        editavel=editavel,
        historico_de_mudanca=historico,
        aprovacoes=aprovacoes,
        liberacoes=liberacoes,
        comprovacoes=comprovacoes,
        **textos,
    )


def validar_catalogo(
    dados: Any, *, arquivo: str, raiz: Path, hoje: date | None = None
) -> tuple[Catalogo | None, list[str]]:
    """Valida a forma de um catálogo já carregado (`carregar.carregar_yaml`).

    `arquivo` é o caminho relativo (`spec/regras/catalogo/<PROCESSO>.yaml`). Devolve
    `(catalogo, [])` ou `(None, achados)` — nunca um catálogo parcial."""
    hoje = hoje or date.today()
    c = _Coletor(f"{arquivo}: ")
    if not isinstance(dados, dict):
        c.add("raiz", "tem de ser mapa")
        return None, c.achados
    sobram = sorted(set(dados) - _CHAVES_CATALOGO)
    faltam = sorted(_CHAVES_CATALOGO - set(dados))
    if sobram or faltam:
        c.add("raiz", f"chaves fora do formato {sobram} / faltando {faltam}")
        return None, c.achados
    if dados["schema"] != SCHEMA_CATALOGO:
        c.add("schema", f"tem de ser {SCHEMA_CATALOGO!r}")
    processo = dados["processo"]
    if not isinstance(processo, str) or not _RE_PROCESSO.match(processo):
        c.add("processo", "fora de ^SP-OP-[A-Z0-9-]+-\\d{3}$")
    else:
        if Path(arquivo).name != f"{processo}.yaml":
            c.add("processo", f"tem de ser igual ao nome do arquivo ({Path(arquivo).name})")
        if not (raiz / _CONTRATOS / f"{processo}.md").is_file():
            c.add("processo", f"contrato {_CONTRATOS}/{processo}.md nao existe")
    if not isinstance(dados["regras"], list):
        c.add("regras", "tem de ser lista")
        return None, c.achados
    regras = [_regra(c, bruto, i, raiz, hoje) for i, bruto in enumerate(dados["regras"], start=1)]
    ids = [r.id for r in regras if r is not None]
    for rid in sorted({x for x in ids if ids.count(x) > 1}):
        c.add(rid, "`id` repetido no catalogo")
    aliases = [r.alias for r in regras if r is not None and r.alias is not None]
    for alias in sorted({x for x in aliases if aliases.count(x) > 1}):
        c.add(alias, "`alias` repetido no catalogo")
    if c.achados:
        return None, c.achados
    return Catalogo(
        processo=str(processo), arquivo=arquivo, regras=tuple(r for r in regras if r is not None)
    ), []


__all__ = [
    "DECISOES_TRANSCRITAS",
    "ESTADOS_DECLARADOS",
    "LIMITES_DE_TEXTO",
    "PAPEIS",
    "SCHEMA_CATALOGO",
    "TIPOS_EDITAVEL",
    "VALIDADES",
    "Catalogo",
    "Norma",
    "Regra",
    "sha256_arquivo_normalizado",
    "somar_validade",
    "validar_catalogo",
]

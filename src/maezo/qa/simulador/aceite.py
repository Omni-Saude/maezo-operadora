"""Roteiro de ACEITE do dono: mensagens FIXAS (sem persona LLM) com assercao por passo.

Nasceu de um roteiro manual que o dono rodou no WhatsApp e achou defeitos que a bateria de personas
nao pegou. Aqui o mesmo roteiro e' repetivel: `--roteiro dono_v1` repete exatamente os mesmos
textos, na ordem, ao despachante real (composicao do simulador: nenhum caso real, escalonamento
interceptado), e confere cada resposta.

SEGMENTO = uma conversa (telefone sintetico proprio, acesso do zero: ACEITO -> CPF -> nascimento,
com o CPF do dono). Segmentos rodam em ordem; entre um e o seguinte, limpeza da conversa anterior.

ASSERCOES de um passo (todas sobre o texto concatenado das respostas DAQUELE turno, ou so' as do
`agente` quando ele e' dado; sem acento e sem diferenca de caixa, no texto E no padrao):
  deve                  todo padrao tem de casar
  nao_deve              nenhum padrao pode casar
  escalacao             "nenhuma" = nenhum caso novo aberto neste turno; outro valor = um caso com
                        esse `motivo_categoria` foi aberto neste turno; `None` = nao confere
  primeira_frase_deve   padrao sobre a primeira frase da primeira resposta
  nao_igual_ao_anterior as respostas deste turno nao podem ser iguais as do passo anterior
Os padroes usam `re.search` com MULTILINE (cada resposta e' uma linha: `^` casa no inicio de cada
resposta) e DOTALL (`.` atravessa linha). Uma falha diz QUAL assercao falhou, nunca ecoa o texto.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .mascaramento import Mascarador, sem_acento
from .personas import Persona
from .registro import Sujeito, Turno

ESCALACAO_NENHUMA = "nenhuma"
_FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL
_FIM_DE_FRASE = re.compile(r"[.!?\n]")

#: A "persona" das conversas do roteiro (so' para o registro; nenhum modelo e' chamado).
PERSONA_ACEITE = Persona(
    id="roteiro_aceite",
    nome="Roteiro de aceite",
    descricao="Mensagens fixas do roteiro de aceite do dono.",
    objetivo="Repetir o roteiro de aceite sem variacao.",
    estilo="Fixo.",
    escalacao="permitida",
    turnos_min=1,
    turnos_max=12,
)


@dataclass(frozen=True)
class Passo:
    texto: str
    deve: tuple[str, ...] = ()
    nao_deve: tuple[str, ...] = ()
    escalacao: str | None = None
    primeira_frase_deve: str | None = None
    nao_igual_ao_anterior: bool = False
    agente: str | None = None


@dataclass(frozen=True)
class Segmento:
    nome: str
    passos: tuple[Passo, ...]


@dataclass
class ResultadoDoPasso:
    segmento: str
    indice: int
    texto: str
    respostas: list[tuple[str, str]] = field(default_factory=list)
    escalacoes: list[dict[str, str | None]] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)

    @property
    def passou(self) -> bool:
        return not self.falhas


@dataclass
class ResultadoDoSegmento:
    nome: str
    telefone_rotulo: str
    passos: list[ResultadoDoPasso] = field(default_factory=list)
    erro_preparo: str | None = None

    @property
    def passou(self) -> bool:
        return self.erro_preparo is None and all(p.passou for p in self.passos)


# ================================================================================================
# O casamento
# ================================================================================================
def compilar(padrao: str) -> re.Pattern[str]:
    """O padrao sem acento e minusculo (como o texto). `sem_acento` nao toca em `\\d`, `\\*`, `\\$`."""
    return re.compile(sem_acento(padrao), _FLAGS)


def primeira_frase(texto: str) -> str:
    """A primeira frase (ate' `. ! ?` ou quebra de linha), sem pontuacao/emoji na frente."""
    frase = _FIM_DE_FRASE.split(texto.strip(), maxsplit=1)[0]
    return re.sub(r"^[^\w]+", "", frase, flags=re.UNICODE).strip()


def _texto_do_turno(respostas: Sequence[tuple[str, str]], agente: str | None) -> str:
    return "\n".join(t for a, t in respostas if agente is None or a == agente)


def avaliar_passo(
    passo: Passo,
    respostas: Sequence[tuple[str, str]],
    escalacoes: Sequence[dict[str, str | None]],
    anterior: str | None,
    *,
    erro_do_turno: str | None = None,
) -> list[str]:
    """As falhas do passo (lista vazia = passou). `anterior` = texto normalizado do passo anterior."""
    falhas: list[str] = []
    if erro_do_turno:
        falhas.append(f"turno terminou em erro ({erro_do_turno})")
    texto = sem_acento(_texto_do_turno(respostas, passo.agente))
    onde = f" [agente {passo.agente}]" if passo.agente else ""
    for padrao in passo.deve:
        if not compilar(padrao).search(texto):
            falhas.append(f"deve{onde}: nada casou com /{padrao}/")
    for padrao in passo.nao_deve:
        if compilar(padrao).search(texto):
            falhas.append(f"nao_deve{onde}: casou /{padrao}/")
    if passo.primeira_frase_deve is not None:
        primeira = respostas[0][1] if respostas else ""
        if not compilar(passo.primeira_frase_deve).search(sem_acento(primeira_frase(primeira))):
            falhas.append(f"primeira_frase_deve: a primeira frase nao casa /{passo.primeira_frase_deve}/")
    if passo.escalacao is not None:
        motivos = [e.get("motivo") or "sem_motivo" for e in escalacoes]
        if passo.escalacao == ESCALACAO_NENHUMA:
            if motivos:
                falhas.append(f"escalacao: esperava nenhuma, abriu {motivos}")
        elif passo.escalacao not in motivos:
            falhas.append(f"escalacao: esperava '{passo.escalacao}', abriu {motivos or 'nenhuma'}")
    if passo.nao_igual_ao_anterior and anterior is not None and texto.strip() == anterior.strip():
        falhas.append("nao_igual_ao_anterior: respostas identicas as do passo anterior")
    return falhas


def _respostas_do_turno(turno: Turno) -> list[tuple[str, str]]:
    return [(m.agente, m.texto) for m in turno.mensagens]


def _escalacoes_do_turno(turno: Turno) -> list[dict[str, str | None]]:
    return [
        {"agente": e.agente, "motivo": e.motivo, "severidade": e.severidade} for e in turno.escalonamentos
    ]


Enviar = Callable[[str], Awaitable[Turno]]


async def executar_passos(segmento: Segmento, enviar: Enviar) -> list[ResultadoDoPasso]:
    """Envia os passos em ordem e avalia cada um. Um passo que falha NAO interrompe o segmento."""
    resultados: list[ResultadoDoPasso] = []
    anterior: str | None = None
    for i, passo in enumerate(segmento.passos, start=1):
        turno = await enviar(passo.texto)
        respostas = _respostas_do_turno(turno)
        escalacoes = _escalacoes_do_turno(turno)
        falhas = avaliar_passo(passo, respostas, escalacoes, anterior, erro_do_turno=turno.erro)
        resultados.append(
            ResultadoDoPasso(
                segmento=segmento.nome,
                indice=i,
                texto=passo.texto,
                respostas=respostas,
                escalacoes=escalacoes,
                falhas=falhas,
            )
        )
        anterior = sem_acento(_texto_do_turno(respostas, passo.agente))
    return resultados


def segmento_sem_preparo(segmento: Segmento, erro: str) -> list[ResultadoDoPasso]:
    """O acesso nao concluiu: nenhum passo rodou, e todos contam como reprovados."""
    return [
        ResultadoDoPasso(segmento.nome, i, p.texto, falhas=[f"nao executado: {erro}"])
        for i, p in enumerate(segmento.passos, start=1)
    ]


# ================================================================================================
# Saida (mascarada)
# ================================================================================================
def como_json(resultados: Sequence[ResultadoDoSegmento], sujeito: Sujeito) -> dict[str, Any]:
    m = Mascarador(sujeito)
    passos_total = sum(len(s.passos) for s in resultados)
    passos_ok = sum(1 for s in resultados for p in s.passos if p.passou)
    return {
        "roteiro_passou": all(s.passou for s in resultados),
        "passos": passos_total,
        "passos_aprovados": passos_ok,
        "segmentos": [
            {
                "nome": s.nome,
                "passou": s.passou,
                "erro_preparo": s.erro_preparo,
                "passos": [
                    {
                        "passo": p.indice,
                        "texto": m.texto(p.texto),
                        "respostas": [{"agente": a, "texto": m.texto(t)} for a, t in p.respostas],
                        "escalacoes": p.escalacoes,
                        "passou": p.passou,
                        "falhas": p.falhas,
                    }
                    for p in s.passos
                ],
            }
            for s in resultados
        ],
    }


def secao_do_relatorio(dados: dict[str, Any], roteiro: str) -> str:
    linhas = [
        f"## Roteiro de aceite `{roteiro}`",
        "",
        f"- Resultado: **{'APROVADO' if dados['roteiro_passou'] else 'REPROVADO'}** "
        f"({dados['passos_aprovados']}/{dados['passos']} passos)",
        "",
    ]
    for seg in dados["segmentos"]:
        linhas += [f"### Segmento {seg['nome']} — {'ok' if seg['passou'] else 'FALHOU'}", ""]
        if seg["erro_preparo"]:
            linhas += [f"Acesso nao concluiu: `{seg['erro_preparo']}`", ""]
        linhas += ["| Passo | Mensagem | Resultado | Escalacao |", "|---|---|---|---|"]
        for p in seg["passos"]:
            motivos = ", ".join(str(e["motivo"]) for e in p["escalacoes"]) or "-"
            linhas.append(
                f"| {p['passo']} | {p['texto']} | {'ok' if p['passou'] else 'FALHOU'} | {motivos} |"
            )
        linhas.append("")
        for p in seg["passos"]:
            if p["passou"]:
                continue
            linhas.append(f"**Passo {p['passo']}** — `{p['texto']}`")
            linhas += [f"- {f}" for f in p["falhas"]]
            for r in p["respostas"]:
                linhas.append(f"  - [{r['agente']}] {str(r['texto'])[:400]}")
            linhas.append("")
    return "\n".join(linhas) + "\n"


# ================================================================================================
# O roteiro do dono (v1)
# ================================================================================================
def _p(texto: str, **kw: Any) -> Passo:
    return Passo(texto=texto, **kw)


_NAO_TENHO = "nao tenho outra informa"

DONO_V1: tuple[Segmento, ...] = (
    Segmento(
        "1-plano-e-cobranca",
        (
            _p(
                "Qual é o meu tipo de plano?",
                deve=("coletivo empresarial", "enfermaria", "obstetr"),
                nao_deve=("nao estao dispon",),
                escalacao=ESCALACAO_NENHUMA,
            ),
            _p(
                "Estou em carência?",
                deve=("carência", "(nao consta|nenhuma car|sem car|em curso|cumprid|pendente)"),
                nao_deve=("ainda nao estao dispon",),
            ),
            _p(
                "Meu plano cobre parto?",
                deve=("obstetr", "(central|aplicativo|portal|pessoa da equipe)"),
                nao_deve=("(esta coberto|cobre sim|cobertura garantida|com certeza)",),
            ),
            _p(
                "Qual o número da minha carteirinha?",
                deve=(r"\*{3,}\d{3,5}",),
                nao_deve=(r"\d{9,}",),
            ),
            _p("Qual é o meu nome?", deve=("nao mostro",), escalacao=ESCALACAO_NENHUMA),
            _p(
                "A mensalidade de julho foi paga?",
                deve=("empresa contratante", "julho de 2026"),
                nao_deve=(_NAO_TENHO,),
            ),
            _p(
                "Quanto foi minha coparticipação em setembro?",
                deve=("setembro de 2026", r"R\$ ?0,00"),
            ),
            _p(
                "de qual ano?",
                deve=("setembro de 2026",),
                nao_deve=(_NAO_TENHO,),
                nao_igual_ao_anterior=True,
            ),
            _p(
                "Quais foram as últimas três mensalidades pagas por mim?",
                deve=("empresa contratante",),
                nao_deve=(f"{_NAO_TENHO}|Confirmando",),
            ),
            _p(
                "Preciso da 2ª via do boleto",
                deve=("(2ª via|segunda via|boleto)", "(empresa|RH)"),
                nao_deve=(_NAO_TENHO, "^a confirmacao do pagamento"),
            ),
            _p(
                "Quando vence o boleto? E meu plano cobre dermatologista?",
                deve=("empresa", "(dermato|cobertura|procedimento)", "(central|aplicativo|portal)"),
                nao_deve=("me perguntar de novo", _NAO_TENHO),
            ),
            _p(
                "O valor do meu boleto está errado, fui cobrado duas vezes",
                escalacao="cobranca",
                deve=("equipe",),
            ),
            _p(
                "O valor do meu boleto está errado, fui cobrado duas vezes",
                escalacao=ESCALACAO_NENHUMA,
                deve=("(ja esta com|continua com).*equipe",),
            ),
            _p("Qual o valor da minha mensalidade de outubro?", deve=("empresa contratante",)),
        ),
    ),
    Segmento(
        "2-pedido-de-atendente",
        (
            _p(
                "Quero falar com um atendente",
                escalacao="solicitacao_humano",
                deve=("encaminhamos seu caso",),
            ),
            _p("Quanto tempo demora?", deve=("(nao consigo prever|aqui mesmo)",), nao_deve=("192",)),
            _p("A mensalidade de agosto foi paga?", deve=("empresa contratante",)),
        ),
    ),
    Segmento(
        "3-emergencia",
        (
            _p("Estou com dor no peito", escalacao="red_flag_clinico", deve=("192",)),
            _p(
                "Qual o valor da minha mensalidade de outubro?",
                deve=("empresa contratante",),
                nao_deve=("192",),
            ),
            _p(
                "piorou, ele está sem ar",
                primeira_frase_deve="^ligue agora para o 192",
                nao_deve=("outras duvidas",),
            ),
        ),
    ),
    Segmento(
        "4-duvida-de-saude",
        (
            _p(
                "Quero tirar uma dúvida sobre minha saúde",
                escalacao=ESCALACAO_NENHUMA,
                deve=("(plano|canais|aplicativo|portal|sintoma)",),
                nao_deve=("equipe de saude",),
            ),
        ),
    ),
)

ROTEIROS: dict[str, tuple[Segmento, ...]] = {"dono_v1": DONO_V1}


def validar(roteiro: Sequence[Segmento]) -> None:
    """Todo padrao compila (um padrao quebrado reprova ANTES de qualquer envio)."""
    for segmento in roteiro:
        for passo in segmento.passos:
            for padrao in (
                *passo.deve,
                *passo.nao_deve,
                *([passo.primeira_frase_deve] if passo.primeira_frase_deve else []),
            ):
                compilar(padrao)


__all__ = [
    "DONO_V1",
    "ESCALACAO_NENHUMA",
    "PERSONA_ACEITE",
    "ROTEIROS",
    "Passo",
    "ResultadoDoPasso",
    "ResultadoDoSegmento",
    "Segmento",
    "avaliar_passo",
    "como_json",
    "compilar",
    "executar_passos",
    "primeira_frase",
    "secao_do_relatorio",
    "segmento_sem_preparo",
    "validar",
]

"""Camada 2 "SEU PLANO" da cobertura: DESENHO e INTERFACE (DL-0167, HEL-3b). Nao ha' implementacao viva.

A camada 1 (`regra_geral`) responde a regra que vale para todos. A camada 2 responde o que o CADASTRO do
plano da pessoa verificada diz, so' por consulta deterministica as rotas JA' PINADAS do lago
(`config/integrations/amh/contracts.lock.json`). Cada fato entra na resposta como FATO DO CADASTRO, com a
rota registrada como `Fonte(tipo="lago", id=<rota>, versao=<contrato pinado>)`, e o texto passa no MESMO
verificador da camada 1 (`verificador.verificar`), tendo como trecho o texto deterministico dos fatos.

As rotas (rota do lago; contrato pinado; porta hoje em `ports/tina.py`; o que responde, so' fato do cadastro):

- `/elegibilidade`; `tina`; `TinaPort.get_elegibilidade`; plano ativo, segmentacao, tipo, acomodacao.
- `/carencias`; `tina`; `TinaPort.get_carencias`; carencias do proprio titular e a data de fim.
- `/requisicoes`; `tina`; `TinaPort.get_requisicoes`; situacao do pedido de autorizacao.
- `/cobertura`; `tina-cobertura` v1.9; `TinaCoberturaPort.get_cobertura`; situacao do procedimento no
  cadastro (Rol, extra, fora da segmentacao), diretriz de utilizacao, carencia do item.
- `/cpt`; `tina-cpt` v1.15; `TinaCptPort.get_cpt_vigente`; CPT vigente (sim/nao, quantas, maior data fim).
- `/procedimentos`; `tina-procedimentos` v1.14; `TinaProcedimentosPort`; catalogo (resolucao, HEL-2).
- `/rede-credenciada`; `tina-rede-credenciada`; NENHUMA porta (pedido a T-MZO-A); prestadores da rede.

Como entra na resposta: primeiro a regra geral (camada 1, quando ha'), depois "No cadastro do seu plano:" e
os fatos, e a formula fixa no fim. Nunca: "esta coberto", "vai ser autorizado" (o cadastro PREVE, nao
decide); `coberto=None`/`indeterminado` = "o cadastro nao informa" e a oferta de uma pessoa; `itens` vazio
NAO e' "nao cobre"; `truncada` = pergunta de esclarecimento (HEL-2), nunca a escolha de um item.

O que falta para implementar (pedidos a T-MZO-A, que e' dona de `ports/tina.py` e da composicao):
(a) a fonte da Helena (`maezo.contratos.fatos_do_plano`) ja' declara `FonteDeCobertura` e `FonteDeCpt`, mas a
composicao do receptor nao injeta nenhuma implementacao delas (sairam com o DL-0154); (b) uma porta fina de
leitura da rede credenciada sobre o contrato pinado `tina-rede-credenciada`; (c) a fonte do catalogo
(`TinaProcedimentosPort`) injetada para a resolucao do procedimento.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

#: As rotas que a camada 2 pode consultar (e nenhuma outra).
Rota = Literal["/elegibilidade", "/carencias", "/requisicoes", "/cobertura", "/cpt", "/rede-credenciada"]
ROTAS: Final[tuple[Rota, ...]] = (
    "/elegibilidade",
    "/carencias",
    "/requisicoes",
    "/cobertura",
    "/cpt",
    "/rede-credenciada",
)
#: A pergunta -> as rotas que a respondem (a camada 2 nunca consulta rota que a pergunta nao pede).
ROTAS_POR_ASSUNTO: Final[dict[str, tuple[Rota, ...]]] = {
    "carencia": ("/carencias", "/cobertura"),
    "cobertura_contratual": ("/cobertura", "/elegibilidade"),
    "cpt": ("/cpt",),
    "rede": ("/rede-credenciada",),
    "situacao_do_pedido": ("/requisicoes",),
}


@dataclass(frozen=True, slots=True)
class FatoDoCadastro:
    """Um fato de UMA rota, ja' em texto deterministico (o trecho que o verificador confere)."""

    rota: Rota
    versao_do_contrato: str
    texto: str


@runtime_checkable
class ConsultaDoSeuPlano(Protocol):
    """A interface da camada 2: os fatos do cadastro do PROPRIO sujeito verificado para um assunto.

    Nunca levanta; falha ou fonte ausente = `()` (a camada nao fala e a habilidade segue em falha fechada).
    `procedimento` e' o codigo resolvido pela HEL-2 (nunca o termo digitado pela pessoa)."""

    async def fatos(
        self, portable_subject_ref: str, assunto: str, *, procedimento: str | None
    ) -> tuple[FatoDoCadastro, ...]: ...


__all__ = ["ROTAS", "ROTAS_POR_ASSUNTO", "ConsultaDoSeuPlano", "FatoDoCadastro", "Rota"]

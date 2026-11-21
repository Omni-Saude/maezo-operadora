"""Sombra do K1 do rco no portao de criterios do AUTH (SP-OP-AUTH-001, DL-0187).

O QUE ISTO FAZ. `ValidateAutoCriteriaWorker.execute` (topico `operadora.auth.validate_auto_criteria`), DEPOIS
de o resultado dos quatro criterios estar pronto (inclusive o da degradacao), chama `adiar_sombra_auth`, que
so ADIA a sombra para depois de o harness completar a tarefa (`_audit_ctx.adiar_pos_conclusao`): dentro de
`execute` nada roda, a sombra nao custa nada ao caso real (nem motor, nem tempo dentro do bloqueio da
tarefa).
Depois de completar, o harness roda `avaliar_em_sombra_auth`, que avalia em SOMBRA:

- a composicao `composicao_carencia_cpt` (`rco_geral_carencia` + `rco_geral_cpt`, regra `mais_restritiva`):
  as duas decisoes sao membros de um grupo de `composicoes[]` do K1 2.3.0 e nunca sao avaliadas sozinhas
  (DL-0173), entao vao por `rco_sombra.avaliar_composicao_em_sombra`;
- a decisao `rco_geral_elegibilidade`, que NAO e membro de nenhum grupo no recorte real do K1 2.3.0
  (`tests/fixtures/rco/k1-2.3.0-recorte/manifesto.json`: os grupos sao `composicao_carencia_cpt` e
  `pares_mutuamente_exclusivos_ortop`), por `rco_sombra.avaliar_em_sombra` (a guarda de membro isolado
  continua valendo: se um pacote futuro a puser num grupo, sai `membro_de_composicao`, nada avaliado).

Nada disto toca o resultado do worker: a tarefa ja completou quando a sombra roda; as duas portas devolvem
`None`, nunca levantam `Exception` e nao escrevem variavel de processo. O efeito e so o FATO de sombra na
cadeia de auditoria (decisao `SHADOW`, dedup `{tenant}:sombra:{task_id}`, com o `dmn_versions` das tabelas
`rco_`, a auditoria do montador e `composicaoK1`), o registro observacional de decisoes K1 em
`modo=sombra` e as metricas. A composicao e registrada com `modo=sombra` (`rco_sombra`): nenhum aviso do K6 e
enfileirado. Com o pin fixado (K1 b8444f52) isto roda em toda tarefa: DEPOIS do complete, ate 3 chamadas ao
motor (os dois membros da composicao e a elegibilidade, num orcamento de `PRAZO_SOMBRA_AUTH_S`), um append do
fato `SHADOW` na cadeia de auditoria e as insercoes do registro K1 (`modo=sombra`); o deploy do motor leva os
237 DMN do rco (as 236 tabelas pinadas e o exemplo sintetico). Nenhum efeito na decisao:
`efeito_k1_habilitado` segue falso e nem e lido aqui.

FATOS DO CASO (`fatos_carencia_cpt_auth`), conferidos contra as regras do montador (`rco_montador`):

- regra 5: da guia, SO `data_atendimento` e `codigo_procedimento_tuss`, das variaveis do contrato de start
  (`CAMPOS_GUIA_START`); nenhuma outra variavel de processo entra (cadastro e fora da TISS nunca vem do
  start);
- regra 6: `carater_atendimento_guia=None`. A variavel de start `carater_atendimento` e semente nao verificada
  e `eletivo` e o unico valor que casa as linhas `negar`; o start do AUTH nao traz o carater DA GUIA
  (decisao da coordenacao: a regra 6 fica, a entrada entra nula);
- regra 8: a versao do K3 vem de quem chama (`rco_sombra._versao_k3_pinada`, o pin AMH verificado);
- `k3=None` (nenhum adaptador do K3 v1.16 existe), `tiss={}` e `tipo_guia=None` (nenhum worker do AUTH le a
  guia TISS), `cadastro`/`fora_tiss` vazios;
- `termos_sem_produtor` = `TERMOS_SEM_PRODUTOR_AUTH`: os termos da unica formula implementada que estas tres
  decisoes usam (`urgenciaAposVinteQuatroHorasCobertura`) que nenhum worker do AUTH produz. O termo
  `parametro` (`horasMinimasUrgencia`) TEM produtor: o valor publicado no proprio pacote.

Com o start de hoje, quase toda entrada entra nula (so `dataAtendimento` pode vir preenchida): a primeira
medida e a cobertura das entradas (`maezo_rco_entrada_cobertura_total`), nao a concordancia com o worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from maezo.platform.observability import record_rco_sombra
from maezo.tools.workers import _audit_ctx, rco_montador, rco_sombra
from maezo.tools.workers.dmn_transport import DmnTransport

#: O grupo de `composicoes[]` do K1 2.3.0 com carencia e CPT (`rco_geral_carencia`, `rco_geral_cpt`).
COMPOSICAO_CARENCIA_CPT: Final[str] = "composicao_carencia_cpt"
#: A decisao de elegibilidade, avaliada sozinha (nao e membro de grupo no K1 2.3.0).
DECISAO_ELEGIBILIDADE: Final[str] = "rco_geral_elegibilidade"
#: DL-0187: o orcamento de motor da sombra por tarefa do AUTH (o mesmo teto de uma avaliacao isolada) e a sua
#: reparticao estatica: os dois membros da composicao e a elegibilidade. Se um pin futuro mudar o tamanho do
#: grupo, a composicao continua limitada a parte dela (`rco_sombra` divide o total dela entre os membros).
PRAZO_SOMBRA_AUTH_S: Final[float] = rco_sombra.PRAZO_SOMBRA_S
MEMBROS_COMPOSICAO_AUTH: Final[int] = 2
AVALIACOES_AUTH: Final[int] = MEMBROS_COMPOSICAO_AUTH + 1
#: Regra 5 do montador: as UNICAS variaveis de start que entram como guia (nome Maezo do dicionario).
CAMPOS_GUIA_START: Final[tuple[str, ...]] = ("data_atendimento", "codigo_procedimento_tuss")
#: Termos de `urgenciaAposVinteQuatroHorasCobertura` sem produtor no AUTH: as datas e horas de inicio da
#: cobertura (cadastro: nenhuma rota do K3 pinado as entrega a este processo) e a data e hora de referencia
#: do atendimento (elementos da guia TISS pelo tipo da guia: nenhum worker do AUTH le a guia).
TERMOS_SEM_PRODUTOR_AUTH: Final[frozenset[str]] = frozenset(
    {
        "dataInicioCobertura",
        "horaInicioCobertura",
        "dataReferenciaAtendimento",
        "horaReferenciaAtendimento",
    }
)


def fatos_carencia_cpt_auth(process_vars: Mapping[str, Any]) -> rco_sombra.MontarFatos:
    """Os fatos do caso para as decisoes de regras gerais do AUTH, como o montador os quer (DL-0187).

    So `CAMPOS_GUIA_START` saem de `process_vars` (copiados no momento da montagem; o valor ainda passa pelo
    tipo e pelo dominio do dicionario no montador). Tudo o mais e nulo por construcao (docstring do modulo).
    """

    def montar(versao_k3: tuple[int, int]) -> rco_montador.FatosDoCaso:
        guia = {campo: process_vars[campo] for campo in CAMPOS_GUIA_START if campo in process_vars}
        return rco_montador.FatosDoCaso(
            guia=guia,
            versao_k3_pinada=versao_k3,
            k3=None,
            carater_atendimento_guia=None,
            tiss={},
            tipo_guia=None,
            termos_sem_produtor=TERMOS_SEM_PRODUTOR_AUTH,
        )

    return montar


def adiar_sombra_auth(dmn: DmnTransport | None, process_vars: Mapping[str, Any], *, topico: str) -> None:
    """Adia a sombra do AUTH para DEPOIS de completar a tarefa (DL-0187); dentro de `execute` nada roda.

    Copia AGORA so `CAMPOS_GUIA_START` (o resto das variaveis nunca chega a sombra) e entrega ao harness
    (`_audit_ctx.adiar_pos_conclusao`) o chamavel que roda `avaliar_em_sombra_auth`. Fora de um despacho
    auditado nada e adiado nem roda. NUNCA levanta."""
    try:
        guia = {campo: process_vars[campo] for campo in CAMPOS_GUIA_START if campo in process_vars}
        _audit_ctx.adiar_pos_conclusao(lambda: avaliar_em_sombra_auth(dmn, guia, topico=topico))
    except Exception as exc:  # o adiamento nunca muda o portao
        record_rco_sombra(rco_sombra.ERRO_DE_PROGRAMACAO)
        rco_sombra.logger.error("rco_sombra_auth_erro_de_programacao", erro=type(exc).__name__, topico=topico)


def avaliar_em_sombra_auth(dmn: DmnTransport | None, process_vars: Mapping[str, Any], *, topico: str) -> None:
    """Avalia em sombra a composicao de carencia/CPT e a elegibilidade; devolve `None` e nunca levanta
    `Exception` (as duas portas de `rco_sombra` ja engolem e contam; esta guarda e cinto e suspensorio e
    conta o mesmo `erro_de_programacao`).

    ORCAMENTO (DL-0187): UM so prazo de motor por tarefa, `PRAZO_SOMBRA_AUTH_S`, repartido estaticamente
    (sem ler relogio) pelas `AVALIACOES_AUTH` avaliacoes: os membros da composicao recebem a parte deles
    (`rco_sombra` divide o total da composicao entre os membros) e a elegibilidade a sua; a soma nunca passa
    do orcamento. O orcamento cobre SO as chamadas ao motor: a leitura do pin e do dicionario (em cache) e o
    montador sao locais e ficam fora; o teto proprio do registro de decisoes K1 do harness nao muda."""
    orcamento = PRAZO_SOMBRA_AUTH_S
    parte = orcamento / AVALIACOES_AUTH
    try:
        fatos = fatos_carencia_cpt_auth(process_vars)
        rco_sombra.avaliar_composicao_em_sombra(
            dmn,
            COMPOSICAO_CARENCIA_CPT,
            fatos,
            topico=topico,
            prazo_total_s=parte * MEMBROS_COMPOSICAO_AUTH,
        )
        rco_sombra.avaliar_em_sombra(dmn, DECISAO_ELEGIBILIDADE, fatos, topico=topico, prazo_total_s=parte)
    except Exception as exc:  # as portas ja sao totais; se algo escapar, conta como elas contam
        record_rco_sombra(rco_sombra.ERRO_DE_PROGRAMACAO)
        rco_sombra.logger.error("rco_sombra_auth_erro_de_programacao", erro=type(exc).__name__, topico=topico)


__all__ = [
    "AVALIACOES_AUTH",
    "MEMBROS_COMPOSICAO_AUTH",
    "PRAZO_SOMBRA_AUTH_S",
    "CAMPOS_GUIA_START",
    "COMPOSICAO_CARENCIA_CPT",
    "DECISAO_ELEGIBILIDADE",
    "TERMOS_SEM_PRODUTOR_AUTH",
    "adiar_sombra_auth",
    "avaliar_em_sombra_auth",
    "fatos_carencia_cpt_auth",
]

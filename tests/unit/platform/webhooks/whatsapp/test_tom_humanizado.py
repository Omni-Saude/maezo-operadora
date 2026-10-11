"""Tom humanizado (DL-0087, 10/10/2026): a SUBSTANCIA obrigatoria dos textos fixos nao se perde no estilo.

O dono mandou encurtar e humanizar os textos que vao ao beneficiario (guia em
`docs/guia-de-tom-dos-agentes.md`), com uma condicao: nada de seguranca ou regulatorio pode sumir. Os testes
de texto exato (`test_acesso.py`, `test_helena_*`) fixam a REDACAO; estes fixam o que tem de continuar la'
em QUALQUER redacao futura — consentimento LGPD completo, emergencia com 192, canais so' com os nomes
permitidos, emoji so' em mensagem positiva e no maximo um, frases curtas.
"""

from __future__ import annotations

import unicodedata

import pytest

from maezo.agents.helena import consultas_plano as cp
from maezo.agents.helena import graph as g
from maezo.agents.helena import prompts as hp
from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import acesso_cadastro as cad
from maezo.platform.webhooks.whatsapp import dispatch as dp

_PREFIXOS = ("RESPOSTA_", "FRASE_", "TEXTO_", "MENSAGEM_")


def _constantes(modulo: object) -> dict[str, str]:
    return {
        f"{modulo.__name__.rsplit('.', 1)[-1]}.{nome}": valor  # type: ignore[attr-defined]
        for nome, valor in vars(modulo).items()
        if nome.startswith(_PREFIXOS) and isinstance(valor, str)
    }


def _todos_os_textos() -> dict[str, str]:
    textos: dict[str, str] = {}
    textos.update({f"acesso.{k}": v for k, v in ac.textos_fixos().items()})
    textos["acesso.REVOGACAO_EM_PROCESSAMENTO"] = ac.REVOGACAO_EM_PROCESSAMENTO
    textos.update(_constantes(cad))
    textos.update(_constantes(g))
    textos.update(_constantes(hp))
    textos["graph.RETOMADA_TEMPLATE"] = g.RETOMADA_TEMPLATE
    textos["graph.RETOMADA_RECUSADA_PLACEHOLDER"] = g.RETOMADA_RECUSADA_PLACEHOLDER
    textos.update({f"graph._PERGUNTA_FALLBACK[{k}]": v for k, v in g._PERGUNTA_FALLBACK.items()})
    textos["consultas_plano.RESPOSTA_CONSULTA_SEM_IDENTIDADE"] = cp.RESPOSTA_CONSULTA_SEM_IDENTIDADE
    textos["consultas_plano._FECHO"] = cp._FECHO
    textos["dispatch.NON_TEXT_ACK_TEXT"] = dp.NON_TEXT_ACK_TEXT
    textos["dispatch.LIMITE_EXCEDIDO_TEXT"] = dp.LIMITE_EXCEDIDO_TEXT
    return textos


TEXTOS = _todos_os_textos()

#: As unicas mensagens que podem levar o emoji leve: positivas ou de acolhimento, sem tema clinico.
COM_EMOJI_PERMITIDO = frozenset(
    {
        "acesso.verificado",
        "acesso_cadastro.RESPOSTA_QUEM_SOU_EU",
        "graph.RESPOSTA_SAUDACAO_CURTA",
        "graph.RESPOSTA_DESPEDIDA",
    }
)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


def _emojis(texto: str) -> list[str]:
    return [c for c in texto if unicodedata.category(c) == "So"]


def test_a_colecao_nao_e_vacua() -> None:
    assert len(TEXTOS) >= 40
    assert "acesso.consentimento" in TEXTOS and "graph.FRASE_PASSAGEM_COBRANCA" in TEXTOS


def test_consentimento_tem_toda_a_substancia_lgpd() -> None:
    t = ac.TEXTO_CONSENTIMENTO
    plano = _sem_acento(t)
    assert "consentimento" in plano
    assert "cadastro, plano e cobranca" in plano  # o que e' usado
    assert "ACEITO" in t and "REVOGAR" in t  # como aceitar e como revogar
    assert "revogar quando quiser" in plano
    assert "se preferir nao aceitar" in plano  # a alternativa de nao aceitar...
    assert "central de atendimento" in plano and "aplicativo austa clinicas" in plano  # ...pelos canais
    assert "portal do plano" in plano
    assert not _emojis(t), "texto legal sem emoji"


def test_consentimento_novo_tem_versao_nova_e_sha_do_texto_exato() -> None:
    import hashlib

    assert ac.VERSAO_TEXTO_CONSENTIMENTO == "wa-consent-v2"
    assert hashlib.sha256(ac.TEXTO_CONSENTIMENTO.encode("utf-8")).hexdigest() == ac.SHA256_TEXTO_CONSENTIMENTO


def test_emergencia_continua_inequivoca() -> None:
    t = ac.TEXTO_EMERGENCIA
    assert "192" in t and "SAMU" in t and "pronto-socorro" in t and "emergência" in t
    assert not _emojis(t)
    assert "192" in dp.LIMITE_EXCEDIDO_TEXT and "SAMU" in dp.LIMITE_EXCEDIDO_TEXT


def test_revogacao_e_bloqueio_mantem_o_que_prometiam() -> None:
    assert "ACEITO" in ac.RESPOSTA_REVOGADO and "não vou mais usar seus dados" in ac.RESPOSTA_REVOGADO.lower()
    assert "não vou usar seus dados" in ac.REVOGACAO_EM_PROCESSAMENTO
    assert "equipe" in ac.BLOQUEADO and "equipe" in ac.TELEFONE_NAO_IDENTIFICADO
    # nada diz se o CPF existe (DL-0083/0084)
    for nome in ("falha_conferencia", "telefone_nao_identificado", "bloqueado"):
        assert "cpf" not in _sem_acento(TEXTOS[f"acesso.{nome}"])


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_nenhum_texto_cita_canal_proibido(nome: str) -> None:
    plano = _sem_acento(TEXTOS[nome])
    for proibido in ("portal do beneficiario", "portal da operadora", "aplicativo do plano", "0800"):
        assert proibido not in plano, f"{nome} cita {proibido!r}"


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_no_maximo_um_emoji_e_so_em_mensagem_positiva(nome: str) -> None:
    emojis = _emojis(TEXTOS[nome])
    assert len(emojis) <= 1, f"{nome}: mais de um emoji"
    if emojis:
        assert nome in COM_EMOJI_PERMITIDO, f"{nome}: emoji fora de mensagem positiva/acolhedora"
        plano = _sem_acento(TEXTOS[nome])
        for tema in ("emergencia", "sintoma", "encaminh", "falha", "revog", "192", "risco"):
            assert tema not in plano, f"{nome}: emoji em mensagem sobre {tema!r}"


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_garantia_de_nenhuma_decisao_so_na_forma_aprovada(nome: str) -> None:
    """Onde a garantia aparece, e' a forma do dono; e nunca vira 'nada foi alterado'."""
    plano = _sem_acento(TEXTOS[nome])
    assert "nada foi alterado" not in plano
    if "nenhuma decisao" in plano:
        assert "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada." in TEXTOS[nome]


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_frases_curtas(nome: str) -> None:
    """Regua de clareza da Helena (no maximo 20 palavras por frase), agora para todo texto fixo."""
    texto = TEXTOS[nome].replace("?", ".").replace("!", ".").replace(":", ".")
    for frase in texto.split("."):
        assert len(frase.split()) <= 20, f"{nome}: frase longa demais: {frase.strip()!r}"


def test_helena_continua_dizendo_que_nao_avalia_e_orienta_emergencia() -> None:
    assert "não avalia" in g.RESPOSTA_SINTOMA_SEM_ALERTA
    for nome in (
        "RESPOSTA_FALHA_TECNICA_START",
        "RESPOSTA_FALHA_DE_REDACAO",
        "RESPOSTA_HANDOFF_RECUSADA",
        "RESPOSTA_FORA_DO_CANAL",
        "RESPOSTA_INFORM_RECUSADA",
        "RESPOSTA_HANDOFF_JA_ABERTO",
        "RESPOSTA_SEM_ENCAMINHAMENTO",
        "RESPOSTA_ESCALONAMENTO_URGENTE",
        "RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL",
    ):
        assert "serviço de emergência mais próximo" in getattr(g, nome), nome
    assert "cobertura" in cp._FECHO and "autorização" in cp._FECHO


def test_prompt_de_resposta_traz_o_tom_sem_tirar_as_proibicoes() -> None:
    texto = hp.response_prompt()
    assert "TOM DA CONVERSA" in texto and "SO o que foi perguntado" in texto
    assert "a proibicao vence" in texto
    for cerca in (
        "NUNCA AFIRME QUE O BENEFICIARIO NAO TEM SINAIS DE ALERTA",
        'SOMENTE response_kind="escalate" PODE PROMETER CONTATO HUMANO',
        "portal do\nbeneficiario",
        "VOCE NAO CONSULTA O STATUS DO CASO DA PROPRIA PESSOA",
    ):
        assert cerca in texto, cerca
    assert "7. Tom" in cp._PROMPT_CONSULTA and "Sem emoji" in cp._PROMPT_CONSULTA

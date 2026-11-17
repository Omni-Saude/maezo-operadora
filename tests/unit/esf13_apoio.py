"""Apoio dos testes do E-SF-13: registro SINTETICO ratificado, aceite OS-05 SINTETICO e dubles do gate.

Tudo aqui e' fixture de teste. NADA disto existe em producao: o registro publicado
(`spec/communications/whatsapp-templates.yaml`) nao ratifica finalidade, nao aprova template, nao publica
texto de permissao e nao declara oposicao executavel. O texto de aceite abaixo e' inventado para o teste
(o texto real do aceite OS-05 / termo E-SF-62 nao existe — GAP-CONSENT-ADMISSAO).
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from maezo.gateway.communications.outbound_gate import OutboundGate, PermissaoDeContato
from maezo.gateway.communications.whatsapp_templates import RegistroDeTemplates, validar_registro
from maezo.platform.lembretes.texto import CORPO_TEMPLATE_META

TEXTO_ACEITE_SINTETICO = "TEXTO SINTETICO DE TESTE — aceite de contato (finalidades nomeadas)."
SHA_ACEITE_SINTETICO = hashlib.sha256(TEXTO_ACEITE_SINTETICO.encode("utf-8")).hexdigest()
VERSAO_ACEITE_SINTETICO = "os05-sintetico-v1"

FINALIDADE_LEAD = "recuperacao_jornada_teste"
TEMPLATE_LEAD = "recuperacao_teste_v1"


def documento_ratificado() -> dict[str, Any]:
    """O documento do registro com TUDO aberto (so' para provar o mecanismo do outro lado do gate)."""
    return {
        "schema": "maezo.whatsapp-templates.v1",
        "versao": 1,
        "variable_classes": [
            {"id": "competencia_mes_ano", "tipo": "pessoal_contratual"},
            {"id": "valor_cobranca_reais", "tipo": "pessoal_contratual"},
            {"id": "data_vencimento_dia_mes", "tipo": "pessoal_contratual"},
            {"id": "contagem", "tipo": "nao_pessoal"},
        ],
        "variable_class_taxonomy_ratified": True,
        "purposes": [
            {"id": "cobranca_lembrete_pre_vencimento", "ratified": True, "ratification_ref": "TESTE-RAT-1"},
            {"id": FINALIDADE_LEAD, "ratified": True, "ratification_ref": "TESTE-RAT-2"},
        ],
        "contact_permission_texts": [
            {
                "version": VERSAO_ACEITE_SINTETICO,
                "sha256": SHA_ACEITE_SINTETICO,
                "purposes": ["cobranca_lembrete_pre_vencimento", FINALIDADE_LEAD],
                "channel": "whatsapp",
            }
        ],
        "opposition_executable_subject_kinds": ["beneficiario", "lead"],
        "templates": [
            {
                "id": "lembrete_mensalidade_v1",
                "name": "lembrete_mensalidade_v1",
                "language": "pt_BR",
                "category": "utility",
                "meta_status": "approved",
                "meta_template_ref": "TESTE-META-1",
                "purpose_ref": "cobranca_lembrete_pre_vencimento",
                "subject_kind": "beneficiario",
                "content_review_ref": "TESTE-REV-1",
                "publicity": False,
                "body": CORPO_TEMPLATE_META,
                "variables": [
                    {"pos": 1, "class": "competencia_mes_ano"},
                    {"pos": 2, "class": "valor_cobranca_reais"},
                    {"pos": 3, "class": "data_vencimento_dia_mes"},
                ],
            },
            {
                "id": TEMPLATE_LEAD,
                "name": TEMPLATE_LEAD,
                "language": "pt_BR",
                "category": "marketing",
                "meta_status": "approved",
                "meta_template_ref": "TESTE-META-2",
                "purpose_ref": FINALIDADE_LEAD,
                "subject_kind": "lead",
                "content_review_ref": "TESTE-REV-2",
                "publicity": True,
                "body": "Voce tem {{1}} cotacao salva. Para retomar, responda SIM. Para nao receber, PARAR.",
                "variables": [{"pos": 1, "class": "contagem"}],
            },
        ],
    }


def registro_ratificado(**mudancas: Any) -> RegistroDeTemplates:
    doc = copy.deepcopy(documento_ratificado())
    doc.update(mudancas)
    return validar_registro(doc)


def aceite_os05_sintetico(*, prova_ref: str | None = "prova-sintetica-1", **kw: Any) -> PermissaoDeContato:
    base: dict[str, Any] = {
        "origem": "aceite_contato",
        "escopo": "contato_proativo",
        "texto_versao": VERSAO_ACEITE_SINTETICO,
        "texto_sha256": SHA_ACEITE_SINTETICO,
        "prova_ref": prova_ref,
    }
    base.update(kw)
    return PermissaoDeContato(**base)


class OposicaoBeneficiarioSintetica:
    """Porta de oposicao do beneficiario SINTETICA (CD-25). Nao existe em producao: so' deixa os testes de
    MECANISMO do lembrete exercitarem o que vem depois da trava."""

    def __init__(self, *, executavel: bool = True, falha: bool = False) -> None:
        self._executavel = executavel
        self._falha = falha
        self.chamadas = 0

    async def executavel(self) -> bool:
        self.chamadas += 1
        if self._falha:
            raise RuntimeError("porta de oposicao fora")
        return self._executavel


class SupressaoFixa:
    def __init__(self, vigentes: frozenset[tuple[str, str]] = frozenset(), *, falha: bool = False) -> None:
        self._vigentes = vigentes
        self._falha = falha
        self.chamadas = 0

    async def vigentes(self) -> frozenset[tuple[str, str]]:
        self.chamadas += 1
        if self._falha:
            raise RuntimeError("fonte de supressao fora")
        return self._vigentes


class ReservaEmMemoria:
    def __init__(self) -> None:
        self.chaves: set[str] = set()
        self.reservas = 0

    async def reservar(self, chave: str) -> bool:
        if chave in self.chaves:
            return False
        self.chaves.add(chave)
        self.reservas += 1
        return True

    async def liberar(self, chave: str) -> None:
        self.chaves.discard(chave)


class TransporteGravador:
    """Conta os POST. Recebe telefone + autorizacao, como `WhatsAppServer.send_template`."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, Any]] = []

    async def __call__(self, telefone: str, autorizacao: Any) -> dict[str, object]:
        self.posts.append((telefone, autorizacao))
        return {"messages": [{"id": "wamid.teste"}]}


def gate_ratificado(supressao: Any = None, **mudancas: Any) -> OutboundGate:
    return OutboundGate(
        registro_ratificado(**mudancas), supressao=supressao if supressao is not None else SupressaoFixa()
    )

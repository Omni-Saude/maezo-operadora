"""E-SF-05r (parte c), EP-CORE-00b — raiz de composição que lê os registros a cada chamada.

Antes, `create_app` só recebia gateways já compostos e os guardava uma vez: um registro de
admissão (A1, catálogo público; A2, sessão de vendas) era conferido só no momento da
composição, e nenhum `read_*_admission` tinha chamador em `src/`. Este resolvedor guarda o
CAMINHO de cada registro e, a cada requisição, relê o arquivo (`read_*_admission`, sem cache),
compõe o gateway da superfície admitida e o entrega à rota.

Fail-closed, sem default:
- caminho ausente (`None`), arquivo ausente (`FileNotFoundError`), registro recusado, registro
  de outro contrato (a composição levanta) ou qualquer outra falha na leitura ou na
  composição → a superfície fica sem ligação e a rota responde o 503 de corpo fixo;
- um registro só liga a própria superfície: o A1 nunca liga a sessão de vendas, e o A2 nunca
  liga o catálogo público.

Nada aqui lê variável de ambiente ou flag, e nenhum código de produção constrói este
resolvedor: a raiz de produção (`maezo.portal.api.production`) continua sem gateway
comercial, e as duas rotas seguem em 503. Ligar a produção exige registro assinado e o
transporte do registro (lacuna aberta), por ato próprio, fora deste módulo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from maezo.gateway.commercial import CommercialGateway, PublicCatalogGateway
from maezo.gateway.commercial_catalog_admission import CatalogSourceAdmission, read_catalog_source_admission
from maezo.gateway.commercial_sales_admission import SalesSessionAdmission, read_sales_session_admission

__all__ = ["CommercialRecordResolver"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _same(gateway: CommercialGateway | None) -> CommercialGateway | None:
    return gateway


@dataclass(frozen=True, slots=True, kw_only=True)
class CommercialRecordResolver:
    """Resolve, por chamada, o gateway de cada superfície a partir do registro em disco.

    `compose_public` e `compose_commercial` recebem a admissão recém-lida e devolvem o
    gateway composto (as portas de autoridade ficam com quem monta o resolvedor).
    `wrap_commercial` é o invólucro da postura de legitimidade que `create_app` aplica; por
    padrão, devolve o mesmo objeto.
    """

    catalog_record_path: Path | None
    sales_record_path: Path | None
    compose_public: Callable[[CatalogSourceAdmission], PublicCatalogGateway]
    compose_commercial: Callable[[SalesSessionAdmission], CommercialGateway]
    clock: Callable[[], datetime] = _utc_now
    wrap_commercial: Callable[[CommercialGateway | None], CommercialGateway | None] = field(default=_same)

    def public_catalog_gateway(self) -> PublicCatalogGateway | None:
        if self.catalog_record_path is None:
            return None
        try:
            admission = read_catalog_source_admission(self.catalog_record_path, now=self.clock())
            gateway = self.compose_public(admission)
        except Exception:  # fail-closed: ausência, recusa ou falha deixam a superfície sem ligação
            return None
        return gateway if isinstance(gateway, PublicCatalogGateway) else None

    def commercial_gateway(self) -> CommercialGateway | None:
        if self.sales_record_path is None:
            return None
        try:
            admission = read_sales_session_admission(self.sales_record_path, now=self.clock())
            gateway = self.compose_commercial(admission)
        except Exception:  # fail-closed: ausência, recusa ou falha deixam a superfície sem ligação
            return None
        if not isinstance(gateway, CommercialGateway):
            return None
        return self.wrap_commercial(gateway)

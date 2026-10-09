"""`consulta_valores` no dominio de `lucas_cobranca_subtipo` (DL-0086, 08/10/2026).

**Por que existe.** Decisao do dono (08/10/2026): o Lucas responde sozinho a pergunta de VALOR
(mensalidade, coparticipacao, saldo, historico, data de pagamento) com os fatos do contrato
`billing-status`. A Helena passa a classificar essas perguntas no subtipo novo `consulta_valores`, e o
roteador grava o subtipo do handoff em `conversa_agente_ativo.lucas_cobranca_subtipo` — cujo CHECK
(migration 0017) so' conhecia os sete subtipos de antes. Sem esta migration a primeira gravacao de um
handoff `consulta_valores` falharia no CHECK.

**O que muda.** So' o CHECK da coluna: o mesmo dominio de antes mais `consulta_valores`, na ordem de
`platform/webhooks/whatsapp/roteamento.py::COBRANCA_SUBTIPOS` (`test_roteamento.py` prova que nao
divergem). O nome e' o que o Postgres deu ao CHECK em linha da 0017
(`conversa_agente_ativo_lucas_cobranca_subtipo_check`), mantido para o `downgrade` ser simetrico.

**Seguro com a versao anterior do codigo no ar.** O CHECK so' ALARGA: toda linha que a versao anterior
grava continua valida, e a versao anterior nunca grava `consulta_valores`.

**Downgrade.** Volta ao CHECK da 0017. Revisao de seguranca do #709: o downgrade NAO pode ficar preso ate'
a purga de 30 dias do roteador (um rollback urgente da versao seria bloqueado por uma conversa viva).
Entao ele APAGA as linhas `consulta_valores` de `conversa_agente_ativo` e registra a contagem (`RAISE
NOTICE`, no log do Job da migration). E' seguro: a linha so' diz "o Lucas esta' ativo nesta conversa"
(nenhum dado do beneficiario); sem ela a proxima mensagem volta a Helena, que classifica de novo — o
mesmo desfecho da purga por inatividade. Nenhuma outra linha e' tocada.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "conversa_agente_ativo_lucas_cobranca_subtipo_check"


def upgrade() -> None:
    op.execute(f"ALTER TABLE conversa_agente_ativo DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"""
        ALTER TABLE conversa_agente_ativo ADD CONSTRAINT {_CONSTRAINT}
            CHECK (lucas_cobranca_subtipo IN (
                'boleto_2via', 'vencimento', 'confirmacao_pagamento', 'contestacao',
                'cobranca_recebida', 'consulta_valores', 'cancelamento', 'outro'
            ))
    """)


def downgrade() -> None:
    op.execute("""DO $$
    DECLARE
        apagadas integer;
    BEGIN
        DELETE FROM conversa_agente_ativo WHERE lucas_cobranca_subtipo = 'consulta_valores';
        GET DIAGNOSTICS apagadas = ROW_COUNT;
        RAISE NOTICE 'migration 0024 downgrade: % conversa_agente_ativo row(s) with consulta_valores deleted',
            apagadas;
    END $$""")
    op.execute(f"ALTER TABLE conversa_agente_ativo DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"""
        ALTER TABLE conversa_agente_ativo ADD CONSTRAINT {_CONSTRAINT}
            CHECK (lucas_cobranca_subtipo IN (
                'boleto_2via', 'vencimento', 'confirmacao_pagamento', 'contestacao',
                'cobranca_recebida', 'cancelamento', 'outro'
            ))
    """)

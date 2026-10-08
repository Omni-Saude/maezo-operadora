"""Vendor suppression record store — the OP20 GP11 registry with a per-record clock (VW4 wiring).

**Por que esta tabela existe.** O registro preventivo de nao-contato (art. 7o IX + art. 18 §2o,
LGPD) nasce com RELÓGIO VISÍVEL: `t_deadline` é computado pela CAMADA DE APLICACAO no ato do
INSERT (insumo ACEITO pelo dono 2026-10-07 — VW0-DECISION-REGISTER §"INCORPORACAO VW4-ANSWERS",
sha `ab262f7b…`; conteúdo `VW4-RATIFICATION-ANSWERS-V1.md` §4c: SLA `T_total` = 15 DIAS UTEIS).
Registro sem deadline NÃO É ACEITO: a coluna é NOT NULL (verdade de storage — nenhuma linha
existe sem prazo) e o adapter da aplicacao recusa (`DEADLINE_UNCOMPUTABLE`) quando o calendário
não produz prazo. "Sem prazo que corre em silencio" é aqui uma invariante estrutural.

**O que NAO esta aqui.** ZERO conteudo de payload (identificador opaco + data + canal, nada
mais — minimizacao art. 10 §1o; `subject_ref` e referencia OPACA, ADR-0006), ZERO cifra e ZERO
prazo de RETENCAO (retencao = VW0-D12, RATIFY-LATER DPO — a migration nunca inventa apagamento).

**O calendário é código, não SQL.** Dias úteis (seg–sex, SEM feriados — limite documentado do
calendário own-code, `gateway/vendor_suppression.py#add_business_days`) são calculados pela
camada de aplicacao e chegam prontos: a coluna é `timestamptz`, o cálculo é código. Nenhum
`INTERVAL` de negócio, nenhuma funcao SQL de data util, nenhum `pg_cron` — o relógio de
escalonamento (50%/80%/100% do SLA) também nasce computado no INSERT e vive nas colunas
`t_lembrete`/`t_escalonado` (timestamp puro; quem dispara é o engine do processo, via timers
`timeDate` absolutos — nunca uma job SQL).

**Idempotência.** `suppression_ref` é o digest canônico de `(subject_ref, contact_channel)`
cunhado pela camada de aplicacao; a chave do registry `tenant + suppression + subject_ref +
contact_channel` colapsa para `(tenant, suppression_ref)` — PRIMARY KEY. Reenvio do mesmo
pedido retorna o MESMO registro (nunca contagem dupla de SLA), via `ON CONFLICT DO NOTHING` +
re-leitura no adapter.

**Status.** Texto fechado do ciclo próprio do relógio: `pendente` (nascido, aguarda
processamento), `honrado` (aplicado na construção de lista), `escalado` (escalonamento 80%/100%
— visível e atrasado por design, nunca silencioso). `motivo` sempre presente: um registro sem
razão declarada não existe.

**Downgrade.** Restaura o estado anterior (tabela inexistente) e recusa-se sobre tabela
populada: registro de oposição do titular vivo é do dono qualificado, não da migration. A
migration nunca é apagada do histórico.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE portal_vendor_suppressions (
            tenant text NOT NULL CHECK (tenant <> ''),
            suppression_ref text NOT NULL CHECK (suppression_ref ~ '^[0-9a-f]{64}$'),
            subject_ref text NOT NULL CHECK (subject_ref <> ''),
            contact_channel text NOT NULL CHECK (contact_channel <> ''),
            canal text NOT NULL CHECK (canal <> ''),
            categoria_sujeito text NOT NULL CHECK (categoria_sujeito IN ('vendedor', 'lead')),
            t_recorded timestamptz NOT NULL DEFAULT clock_timestamp(),
            t_deadline timestamptz NOT NULL,
            t_lembrete timestamptz NOT NULL,
            t_escalonado timestamptz NOT NULL,
            status text NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'honrado', 'escalado')),
            motivo text NOT NULL CHECK (motivo <> ''),
            PRIMARY KEY (tenant, suppression_ref)
        )
    """)
    op.execute(
        "COMMENT ON TABLE portal_vendor_suppressions IS 'Registro preventivo de nao-contato "
        "(OP20/GP11, VW4 wiring): identificador opaco + data + canal (art. 10 §1o); t_deadline/"
        "t_lembrete/t_escalonado computados pela camada de aplicacao no INSERT (SLA 15 dias uteis "
        "ACEITO pelo dono 2026-10-07, sha ab262f7b — calendario own-code seg-sex SEM feriados); "
        "sem conteudo de payload, sem cifra, sem retencao (VW0-D12 RATIFY-LATER DPO)'"
    )
    op.execute("REVOKE ALL ON TABLE portal_vendor_suppressions FROM PUBLIC")


def downgrade() -> None:
    # Reversão estrutural restaura o estado anterior e NUNCA apaga registro vivo de oposição:
    # disposição é do dono qualificado, não da migration.
    op.execute("""DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM portal_vendor_suppressions) THEN
            RAISE EXCEPTION 'populated vendor suppression store requires qualified lifecycle disposition';
        END IF;
    END $$""")
    op.execute("DROP TABLE portal_vendor_suppressions")

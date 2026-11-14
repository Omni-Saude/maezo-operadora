"""Registro da verificacao de legitimidade da PJ contratante (OP21, REG-1.1) — dark.

**Por que existe.** O parecer do Diretor Juridico (2026-10-26, snapshot canonico
`textos-base/PARECER-OP21-2026-10-26-LUCAS.txt` sha256 `d80740cc…`) validou as ancoras do
controle e o desenho `OP21-LEGITIMACY-SCREEN-DESIGN-V1.md` (sha256 `5639f41d…`, DECISAO 2)
fixou este modelo: o banco guarda a VERIFICACAO (criterio→resultado→referencia tipada) e a
REFERENCIA aos documentos — NUNCA os documentos. A guarda documental e' dever da operadora
e/ou da administradora de beneficios (RN 557/2022, arts. 12 e 15 §3º; parecer §5
"Registros": "… sem duplicar a custodia").

**O que guarda.** Uma linha por revisao de tela `(tenant, operation='legitimacy_screen',
screen_ref, screen_revision)`: a PJ contratante (`legal_entity_ref`) e a oportunidade de
venda (`opportunity_ref`, por referencia), o tipo e a forma de contratacao (RN 557/2022,
art. 29 I–III; empresario individual = arts. 9º–14), o bloco FECHADO de criterios em JSONB
(`[{criterion_id, result: proven|not_proven|not_applicable|pending, norm_anchor,
evidence_refs:[{kind, locator, document_sha256}]}]`), o estado da verificacao, o que a
postura enforcing decidiria (`would_block` — registravel em shadow, nunca aplicador aqui), a
sinalizacao do efeito do art. 39 com a ancora literal "RN 557/2022, art. 39", o vencimento
da reconfirmacao anual (art. 10) e a auditoria.

**Fechado anti-custodia.** O JSONB de criterios so' passa pela funcao IMMUTABLE
`portal_legitimacy_criteria_ok`: quatro chaves exatas por criterio, tres por referencia,
enums fechados, charset/teto em cada texto — em particular o TETO DO LOCATOR (256), a cerca
de abuso do desenho §3, e o digest hex-64 do documento apontado. Nao existe chave onde
bytes/preview/conteudo de documento caibam — tentativa vira violacao de CHECK LOUD. As
ancoras persistidas carregam a forma canonica (parecer §1): contratacao cita RN 557/2022 por
artigo proprio; penalidade cita "RN 489/2022, art. 23-A (redacao dada pela RN 659/2025)" —
as formas imprecisas "RN 659/2025, art. 23-A" e "RN 557/659" sao recusadas por CHECK.

**O pin estrutural.** `status` tem CHECK fechado em `('valid','invalid','pending')` —
nenhum outro estado e' persistivel nem por bug. O ENFORCING nao e' estado desta tabela:
e' postura sucessora com bind explicito em composition root (desenho DECISAO 3), nunca
knob de ambiente e nunca UPDATE nesta tabela. Revalidacao por evento = NOVA
`screen_revision` (nova linha); revisao persistida e' imutavel.

**Idempotencia (desenho §3, emenda ao registry).** Duas unicidades no storage: a PK da
INSTANCIA (reenvio nunca cria segunda instancia, molde 0020) e a UNIQUE da binding 'a
oportunidade `(tenant, operation, opportunity_ref, screen_revision)` — mesma oportunidade e
revisao sob outro `screen_ref` e' recusa, nunca segunda trilha. A regra de vigencia POR PJ
("recibo vigente unico por `(tenant, legal_entity_ref)`") e' avaliada pelo chamador sobre a
ultima revisao — janela de vigencia e' semantica de postura, nao constraint estatica.

**Seguro com a versao anterior do codigo no ar.** Tabela nova, escura: nenhuma rota, flag,
guard ou enforcing existe no substrato (PR 1; tela/flag = PR 2, guard shadow = PR 3,
enforcing = PR 4) — nenhuma superficie escreve aqui e o caminho comercial fica byte-identico.
O `downgrade` apaga a tabela e a funcao: descartavel por construcao enquanto escura (nenhum
registro de controle existe ainda; molde 0029 — rascunhos/estruturas nao carregam direito
adquirido). A migration nunca e' apagada do historico.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Forma fechada do bloco de criterios — espelhada byte a byte com o modelo pydantic
# (`gateway/commercial_legitimacy_screen.py`): CriterionId, CriterionResultKind, NormAnchor,
# EvidenceRefKind, EvidenceLocator, Sha256Digest. CHECK nao aceita subquery, entao a forma
# vive numa funcao IMMUTABLE (molde 0013: funcao plpgsql criada e revogada pela migration).
_CRITERIA_OK = r"""
CREATE FUNCTION portal_legitimacy_criteria_ok(candidate jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
DECLARE
    element jsonb;
    ref jsonb;
BEGIN
    IF candidate IS NULL OR jsonb_typeof(candidate) <> 'array'
       OR jsonb_array_length(candidate) = 0 THEN
        RETURN false;
    END IF;
    FOR element IN SELECT * FROM jsonb_array_elements(candidate) LOOP
        IF jsonb_typeof(element) <> 'object' THEN
            RETURN false;
        END IF;
        -- Conjunto de chaves EXATO: nada alem de criterion_id/result/norm_anchor/evidence_refs
        -- (nao existe slot para conteudo de documento), nada faltando.
        IF EXISTS (
            SELECT 1 FROM jsonb_object_keys(element) AS kk(key)
             WHERE kk.key NOT IN ('criterion_id', 'result', 'norm_anchor', 'evidence_refs')
        ) OR (SELECT count(*) FROM jsonb_object_keys(element) AS kk(key)
              WHERE kk.key IN ('criterion_id', 'result', 'norm_anchor', 'evidence_refs')) <> 4 THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(element->'criterion_id') <> 'string'
           OR element->>'criterion_id' !~ '^[a-z0-9_]{1,64}$' THEN
            RETURN false;
        END IF;
        IF element->>'result' NOT IN ('proven', 'not_proven', 'not_applicable', 'pending') THEN
            RETURN false;
        END IF;
        -- Ancora normativa: texto curto na forma canonica; as formas imprecisas de citacao
        -- (parecer §1/§4) sao recusadas por LIKE — a forma longa canonica
        -- "RN 489/2022, art. 23-A (redacao dada pela RN 659/2025)" nao contem nenhum deles.
        IF jsonb_typeof(element->'norm_anchor') <> 'string'
           OR element->>'norm_anchor' !~ '^[A-Za-z0-9À-ÿ .,;:()/§ºª-]{1,192}$'
           OR element->>'norm_anchor' LIKE '%RN 659/2025, art. 23-A%'
           OR element->>'norm_anchor' LIKE '%RN 557/659%' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(element->'evidence_refs') <> 'array' THEN
            RETURN false;
        END IF;
        FOR ref IN SELECT * FROM jsonb_array_elements(element->'evidence_refs') LOOP
            IF jsonb_typeof(ref) <> 'object' THEN
                RETURN false;
            END IF;
            IF EXISTS (
                SELECT 1 FROM jsonb_object_keys(ref) AS kk(key)
                 WHERE kk.key NOT IN ('kind', 'locator', 'document_sha256')
            ) OR (SELECT count(*) FROM jsonb_object_keys(ref) AS kk(key)
                  WHERE kk.key IN ('kind', 'locator', 'document_sha256')) <> 3 THEN
                RETURN false;
            END IF;
            -- kind = atribuicao de custodia (RN 557/2022, art. 12): operadora ou
            -- administradora; o canal nunca e' custodiante representavel (parecer §3).
            IF ref->>'kind' NOT IN ('operadora', 'administradora') THEN
                RETURN false;
            END IF;
            -- Teto do locator = a cerca de abuso do desenho §3; charset fechado com
            -- separadores de caminho, sem espaco, sem texto longo. Forma fatorada
            -- (1..255 + um caractere = 1..256) porque o motor de regex do PostgreSQL
            -- recusa contagem de repeticao acima de 255; espelho byte a byte do modelo.
            IF jsonb_typeof(ref->'locator') <> 'string'
               OR ref->>'locator' !~ '^([A-Za-z0-9_.:/-]{1,255})?[A-Za-z0-9_.:/-]$' THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(ref->'document_sha256') <> 'string'
               OR ref->>'document_sha256' !~ '^[0-9a-f]{64}$' THEN
                RETURN false;
            END IF;
        END LOOP;
    END LOOP;
    RETURN true;
END
$fn$"""

_CREATE = r"""
CREATE TABLE portal_legitimacy_screens (
    -- Espelho byte a byte de OpaqueRef (portal/contracts/models.py): sem whitespace, sem
    -- caractere de controle, sem /?#; 1..512.
    tenant text NOT NULL CHECK (
        tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'
    ),
    operation text NOT NULL DEFAULT 'legitimacy_screen' CHECK (operation = 'legitimacy_screen'),
    -- Molde policy_ref da 0029, espelhado com ScreenRef.
    screen_ref text NOT NULL CHECK (screen_ref ~ '^[A-Za-z0-9_-]{16,128}$'),
    screen_revision integer NOT NULL CHECK (screen_revision >= 1),
    legal_entity_ref text NOT NULL CHECK (
        legal_entity_ref <> '' AND char_length(legal_entity_ref) <= 512
        AND legal_entity_ref !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- Binding 'a oportunidade de venda (OP14/OP15), por referencia: nunca copia de comando.
    opportunity_ref text NOT NULL CHECK (
        opportunity_ref <> '' AND char_length(opportunity_ref) <= 512
        AND opportunity_ref !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- RN 557/2022 art. 29 I-III e arts. 9º-14 (empresario individual).
    contract_kind text NOT NULL CHECK (
        contract_kind IN ('empresarial', 'adesao', 'empresario_individual')
    ),
    contracting_form text NOT NULL CHECK (
        contracting_form IN ('direta', 'administradora', 'coestipulante')
    ),
    -- O bloco FECHADO criterio→resultado→referencia. Guarda-verdade da forma: a funcao
    -- IMMUTABLE acima; nunca bytes de documento (a custodia e' da operadora/administradora,
    -- RN 557/2022 art. 12).
    criteria jsonb NOT NULL
        CHECK (jsonb_typeof(criteria) = 'array' AND portal_legitimacy_criteria_ok(criteria)),
    -- O PIN: nenhum estado fora do enum e' persistivel; enforcing e' postura sucessora.
    status text NOT NULL CHECK (status IN ('valid', 'invalid', 'pending')),
    -- O que a postura enforcing decidiria: registravel em shadow, nunca aplicador de efeito.
    would_block boolean NOT NULL,
    -- Sinalizacao do efeito do art. 39 (parecer §6): par exclusivo com a ancora literal.
    -- Nome EXPLICITO: a cerca de citacao e' apontada pelos testes pelo nome, nunca por
    -- auto-numeracao do PostgreSQL.
    art39_signaled boolean NOT NULL,
    art39_anchor text CONSTRAINT portal_legitimacy_screens_art39_par_exclusivo CHECK (
        (art39_signaled = true AND art39_anchor = 'RN 557/2022, art. 39')
        OR (art39_signaled = false AND art39_anchor IS NULL)
    ),
    -- Reconfirmacao anual (RN 557/2022 art. 10, aniversario do contrato); revalidacao por
    -- evento = nova screen_revision, nunca UPDATE de revisao.
    reconfirmation_due_at timestamptz,
    created_by text NOT NULL CHECK (
        created_by <> '' AND char_length(created_by) <= 512
        AND created_by !~ '[[:space:][:cntrl:]/?#]'
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Instancia (NO-DUPLICATE-INSTANCE por PK, molde 0020).
    PRIMARY KEY (tenant, operation, screen_ref, screen_revision),
    -- Binding 'a oportunidade: mesma oportunidade + revisao sob outro screen_ref e' recusa.
    CONSTRAINT portal_legitimacy_screens_oportunidade_unica UNIQUE
        (tenant, operation, opportunity_ref, screen_revision),
    CHECK (updated_at >= created_at)
)
"""


def upgrade() -> None:
    op.execute(_CRITERIA_OK)
    op.execute(_CREATE)
    # Eixos de leitura do port: ultima revisao por OPORTUNIDADE e por PJ contratante.
    op.execute(
        "CREATE INDEX portal_legitimacy_screens_oportunidade_idx "
        "ON portal_legitimacy_screens (tenant, opportunity_ref, screen_revision DESC)"
    )
    op.execute(
        "CREATE INDEX portal_legitimacy_screens_contratante_idx "
        "ON portal_legitimacy_screens (tenant, legal_entity_ref, screen_revision DESC)"
    )
    op.execute(
        "COMMENT ON TABLE portal_legitimacy_screens IS 'Registro da verificacao de legitimidade "
        "da PJ contratante em planos coletivos (OP21, REG-1.1): criterio→resultado→referencia "
        "tipada em JSONB fechado (kind+locator+document_sha256 — NUNCA bytes de documento, "
        "custodia RN 557/2022 art. 12), status pinado por CHECK em valid/invalid/pending, "
        "would_block e sinalizacao art. 39 com ancora literal; revalidacao = nova "
        "screen_revision. Ancoras: contratacao RN 557/2022 por artigo; penalidade RN 489/2022 "
        "art. 23-A (redacao dada pela RN 659/2025). Escura no substrato — tela/flag PR 2, "
        "guard PR 3, enforcing PR 4'"
    )
    op.execute("REVOKE ALL ON TABLE portal_legitimacy_screens FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION portal_legitimacy_criteria_ok(jsonb) FROM PUBLIC")


def downgrade() -> None:
    # Descartavel por construcao enquanto escura (PR 1: nenhum escritor existe na composicao).
    op.execute("DROP TABLE portal_legitimacy_screens")
    op.execute("DROP FUNCTION portal_legitimacy_criteria_ok(jsonb)")

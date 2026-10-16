-- Rosters dos roteiros `lucas_v4_pagador` e `lucas_v4_nao_pagador` (DL-0114, docs/runbooks/lucas-v4.md).
--
-- LEITURA APENAS, so' por uma pessoa com acesso ao Athena (workgroup amh-austa_operadora-analytics-dev, com
-- permissao de coluna em nr_cpf / dt_nascimento / nm_pessoa_fisica). O resultado (CSV) e' copiado, de bucket para
-- bucket, para s3://<bucket do simulador>/roster/ como no runbook do simulador. O codigo do simulador NUNCA le' o
-- lago (ADR-0037): so' esse CSV, que fica no S3 cifrado e na memoria do processo; o relatorio usa rotulos e o
-- mascarador. Nenhuma tabela e' criada nem alterada.
--
-- ATENCAO: ESTE SQL NAO FOI VALIDADO NO ATHENA. Os nomes de tabela/coluna seguem a regra do dono (pagador =
-- PLS_SEGURADO.NR_SEQ_PAGADOR -> PLS_CONTRATO_PAGADOR.NR_SEQUENCIA; o beneficiario e' o pagador quando as
-- CD_PESSOA_FISICA coincidem) e o `roster.sql`; confirme os nomes das colunas da bronze antes de rodar.
--
-- Saida (as duas consultas): cpf,nascimento,nome (nascimento em AAAA-MM-DD). O dono NAO entra aqui: o roteiro
-- `lucas_v4` e' do dono (plano empresarial) e o `__main__` usa o primeiro do roster que resolver na AMH e nao for o dono.

-- =====================================================================================================
-- 1) PAGADORES: titulares PF que sao o proprio pagador do contrato (roteiro `lucas_v4_pagador`)
--    Com mensalidade gerada nos ultimos 3 meses e contrato sem rescisao nem cancelamento.
-- =====================================================================================================
WITH pessoa AS (
    SELECT
        cd_pessoa_fisica,
        regexp_replace(nr_cpf, '[^0-9]', '') AS cpf,
        CAST(dt_nascimento AS date) AS nascimento,
        trim(nm_pessoa_fisica) AS nome,
        row_number() OVER (PARTITION BY cd_pessoa_fisica ORDER BY dt_atualizacao DESC) AS ordem
    FROM amh_austa_operadora_bronze.tasy_pessoa_fisica
    WHERE nr_cpf IS NOT NULL AND dt_nascimento IS NOT NULL AND dt_obito IS NULL AND nm_pessoa_fisica IS NOT NULL
),
pagadores AS (
    SELECT DISTINCT s.cd_pessoa_fisica
    FROM amh_austa_operadora_bronze.tasy_pls_segurado s
    JOIN amh_austa_operadora_bronze.tasy_pls_contrato_pagador p
        ON p.nr_sequencia = s.nr_seq_pagador
       AND p.cd_pessoa_fisica = s.cd_pessoa_fisica              -- o beneficiario E' o pagador (PF)
    JOIN amh_austa_operadora_bronze.tasy_pls_mensalidade_segurado m
        ON m.nr_seq_segurado = s.nr_sequencia
    WHERE m.dt_mesano_referencia >= date_add('month', -3, current_timestamp)
      AND s.nr_seq_titular IS NULL                               -- titular
      AND s.dt_rescisao IS NULL
      AND s.dt_cancelamento IS NULL
)
SELECT p.cpf, CAST(p.nascimento AS varchar) AS nascimento, p.nome
FROM pessoa p
JOIN pagadores g ON g.cd_pessoa_fisica = p.cd_pessoa_fisica
WHERE p.ordem = 1 AND length(p.cpf) = 11 AND p.cpf <> '42853296890'
ORDER BY rand()
LIMIT 200;

-- =====================================================================================================
-- 2) NAO PAGADORES: dependentes cujo pagador e' OUTRA pessoa fisica (roteiro `lucas_v4_nao_pagador`)
-- =====================================================================================================
WITH pessoa AS (
    SELECT
        cd_pessoa_fisica,
        regexp_replace(nr_cpf, '[^0-9]', '') AS cpf,
        CAST(dt_nascimento AS date) AS nascimento,
        trim(nm_pessoa_fisica) AS nome,
        row_number() OVER (PARTITION BY cd_pessoa_fisica ORDER BY dt_atualizacao DESC) AS ordem
    FROM amh_austa_operadora_bronze.tasy_pessoa_fisica
    WHERE nr_cpf IS NOT NULL AND dt_nascimento IS NOT NULL AND dt_obito IS NULL AND nm_pessoa_fisica IS NOT NULL
),
nao_pagadores AS (
    SELECT DISTINCT s.cd_pessoa_fisica
    FROM amh_austa_operadora_bronze.tasy_pls_segurado s
    JOIN amh_austa_operadora_bronze.tasy_pls_contrato_pagador p
        ON p.nr_sequencia = s.nr_seq_pagador
    WHERE p.cd_pessoa_fisica IS NOT NULL                         -- pagador pessoa FISICA (nao empresa)
      AND p.cd_pessoa_fisica <> s.cd_pessoa_fisica               -- e nao e' o proprio beneficiario
      AND s.nr_seq_titular IS NOT NULL                           -- dependente
      AND s.dt_rescisao IS NULL
      AND s.dt_cancelamento IS NULL
)
SELECT p.cpf, CAST(p.nascimento AS varchar) AS nascimento, p.nome
FROM pessoa p
JOIN nao_pagadores n ON n.cd_pessoa_fisica = p.cd_pessoa_fisica
WHERE p.ordem = 1 AND length(p.cpf) = 11 AND p.cpf <> '42853296890'
ORDER BY rand()
LIMIT 200;

-- Roster do simulador de beneficiario (runbook docs/runbooks/simulador-beneficiario.md).
--
-- Rodar SO' por uma pessoa com acesso ao Athena (workgroup amh-austa_operadora-analytics-dev, com
-- permissao de coluna em nr_cpf / dt_nascimento / nm_pessoa_fisica). O resultado (CSV no bucket de
-- resultados do workgroup) e' copiado, de bucket para bucket, para s3://<bucket do simulador>/roster/
-- (comando no runbook). O codigo do simulador NUNCA le' o lago (ADR-0037): so' esse CSV.
--
-- Quem entra: beneficiarios com mensalidade gerada nos ultimos 6 meses e contrato sem rescisao nem
-- cancelamento, com CPF de 11 digitos e data de nascimento, sem obito; mais o DONO (CPF 42853296890),
-- sempre, na primeira linha. 200 pessoas sorteadas: o preflight do simulador resolve cada uma na
-- AMH (resolve-by-document) e usa as primeiras que resolverem (padrao 40).
--
-- Saida: cpf,nascimento,nome (nascimento em AAAA-MM-DD).
WITH pessoa AS (
    SELECT
        cd_pessoa_fisica,
        regexp_replace(nr_cpf, '[^0-9]', '') AS cpf,
        CAST(dt_nascimento AS date) AS nascimento,
        trim(nm_pessoa_fisica) AS nome,
        row_number() OVER (PARTITION BY cd_pessoa_fisica ORDER BY dt_atualizacao DESC) AS ordem
    FROM amh_austa_operadora_bronze.tasy_pessoa_fisica
    WHERE nr_cpf IS NOT NULL
      AND dt_nascimento IS NOT NULL
      AND dt_obito IS NULL
      AND nm_pessoa_fisica IS NOT NULL
),
com_cobranca AS (
    SELECT DISTINCT s.cd_pessoa_fisica
    FROM amh_austa_operadora_bronze.tasy_pls_mensalidade_segurado m
    JOIN amh_austa_operadora_bronze.tasy_pls_segurado s
        ON s.nr_sequencia = m.nr_seq_segurado
    WHERE m.dt_mesano_referencia >= date_add('month', -6, current_timestamp)
      AND s.dt_rescisao IS NULL
      AND s.dt_cancelamento IS NULL
),
dono AS (
    SELECT cpf, nascimento, nome, 0 AS prioridade, 0.0E0 AS sorteio
    FROM pessoa
    WHERE ordem = 1 AND cpf = '42853296890'
    LIMIT 1
),
sorteados AS (
    SELECT p.cpf, p.nascimento, p.nome, 1 AS prioridade, rand() AS sorteio
    FROM pessoa p
    JOIN com_cobranca c ON c.cd_pessoa_fisica = p.cd_pessoa_fisica
    WHERE p.ordem = 1
      AND length(p.cpf) = 11
      AND p.cpf <> '42853296890'
)
SELECT cpf, CAST(nascimento AS varchar) AS nascimento, nome
FROM (
    SELECT * FROM dono
    UNION ALL
    SELECT * FROM sorteados
)
ORDER BY prioridade, sorteio
LIMIT 200

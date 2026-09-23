-- D-H.3 (plano portal-autoridade-nativa-dev, T1.4): estado duravel do emissor de casos staff.
-- Instalado pelo dono (maezo_native_schema_owner) em maezo_native, dentro de engine-native-install.sql.
-- Uma linha por (escopo, policy_ref). A escrita e CAS por revision (UPDATE ... WHERE revision=$old).
-- Rotacao (D-H.6): policy_ref novo = linha nova, revision recomecando. Nunca ha DELETE: o login
-- maezo_native_case_issuer recebe SELECT,INSERT,UPDATE e mais nada; cibseven_app e witnesses, nada.
CREATE TABLE mzo_staff_case_issuer_ledger (
 tenant text NOT NULL, environment text NOT NULL, engine_name text NOT NULL, database_incarnation text NOT NULL,
 policy_ref text NOT NULL CHECK(octet_length(policy_ref) BETWEEN 1 AND 255),
 revision bigint NOT NULL CHECK(revision>=0),
 issued_state bytea NOT NULL CHECK(octet_length(issued_state)<=1048576),
 pending_request bytea CHECK(pending_request IS NULL OR octet_length(pending_request) BETWEEN 1 AND 65536),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant,environment,engine_name,database_incarnation,policy_ref)
);
REVOKE ALL ON mzo_staff_case_issuer_ledger FROM PUBLIC;

-- ADR0049 D3/D5. Additive migration; SAME schema/database as existing MZO_HUMAN_* / ACT_*.
-- Execute once as the independently designated migration owner. Creates no role,
-- credential, trust anchor, tenant, qualification, activation or consumer evidence.
CREATE TABLE MZO_HUMAN_DECISION_AUTHORITY (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INSTALLATION_ varchar(255) NOT NULL, GENERATION_ bigint NOT NULL CHECK(GENERATION_>=0),
 DOCUMENT_ text NOT NULL, DIGEST_ char(64) NOT NULL,
 PRIMARY KEY(TENANT_,INSTALLATION_,GENERATION_)
);
CREATE TABLE MZO_HUMAN_DECISION_QUALIFICATION (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INSTALLATION_ varchar(255) NOT NULL, AUTHORITY_REV_ bigint NOT NULL CHECK(AUTHORITY_REV_>=0),
 PACKET_ text NOT NULL, DIGEST_ char(64) NOT NULL,
 PRIMARY KEY(TENANT_,INSTALLATION_,AUTHORITY_REV_,DIGEST_)
);
CREATE TABLE MZO_HUMAN_DECISION_INSTALL_RECEIPT (
 TENANT_ varchar(255) NOT NULL REFERENCES MZO_HUMAN_TENANT(TENANT_),
 INSTALLATION_ varchar(255) NOT NULL, OPERATION_ varchar(255) NOT NULL,
 KIND_ varchar(32) NOT NULL CHECK(KIND_ IN ('designate','install','revoke')),
 REQUEST_DIGEST_ char(64) NOT NULL, EXPECTED_REV_ bigint NOT NULL CHECK(EXPECTED_REV_>=0),
 RESULTING_REV_ bigint NOT NULL CHECK(RESULTING_REV_=EXPECTED_REV_+1),
 PACKET_DIGESTS_ text NOT NULL,
 RECORDED_AT_ timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(TENANT_,INSTALLATION_,OPERATION_)
);
REVOKE ALL ON MZO_HUMAN_DECISION_AUTHORITY,MZO_HUMAN_DECISION_QUALIFICATION,
 MZO_HUMAN_DECISION_INSTALL_RECEIPT FROM PUBLIC;
-- Explicit separate grants, applied by install_decision_qualification_schema:
-- migration owner retains ownership; installer SELECT/INSERT only on immutable
-- records and binding, SELECT/UPDATE on tenant revision; source reader SELECT only.
-- The actual engine runtime remains SELECT ONLY on MZO_HUMAN_DECISION_BINDING.
-- Never grant runtime/reader membership in installer/owner, or schema CREATE.

-- Q2 installed admission (portal-read-admission.v1, T1.7a). Apply once, in the pinned native schema
-- (maezo_native, ADR-0060), as its owner login (maezo_native_schema_owner), never at startup.
-- One row per admission revision. RECORD_ holds the exact JCS bytes the approver's installation root
-- signed over "maezo/portal-read-admission/v1\0" || RECORD_; SIGNATURE_ is the raw Ed25519 signature.
-- Only the owner writes (install a revision, or set REVOKED_ = true). The engine login receives
-- SELECT only, granted by the installation script (T1.4); the provider refuses to run when the
-- engine login can INSERT, UPDATE, DELETE or TRUNCATE here, or is (a member of) the owner.
-- No role/credential is provisioned by this file. PUBLIC receives no rights.
CREATE TABLE MZO_PORTAL_READ_ADMISSION (
 ADMISSION_REF_ varchar(255) NOT NULL,
 REVISION_ bigint NOT NULL CHECK (REVISION_>=1),
 RECORD_ bytea NOT NULL CHECK (octet_length(RECORD_) BETWEEN 2 AND 65536),
 SIGNATURE_ bytea NOT NULL CHECK (octet_length(SIGNATURE_)=64),
 REVOKED_ boolean NOT NULL DEFAULT false,
 INSTALLED_AT_ timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(ADMISSION_REF_,REVISION_)
);
REVOKE ALL ON MZO_PORTAL_READ_ADMISSION FROM PUBLIC;

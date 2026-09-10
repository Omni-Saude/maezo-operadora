package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;

import java.util.*;

/** SC1 factual AUTH identity extraction. No external grant or new case allocator.
 * The caller holds the existing tenant lock and authenticates the actual AUTH
 * installation before constructing AuthStore. External identities are factual
 * equality checks only; no external checkpoint/grant lock is acquired here.
 */
final class NativeCaseIdentityReader {
  private final AuthStore db;
  NativeCaseIdentityReader(AuthStore db) { this.db = Objects.requireNonNull(db); }

  Map<String,Object> read(String caseRef) {
    ExternalCaseModels.check("case", caseRef);
    var claim = db.caseLink(caseRef);
    if (claim == null) throw unavailable();
    var reverse = db.instanceLink(str(claim,"instance_"));
    if (!claim.equals(reverse) || !db.tenant.equals(claim.get("tenant_"))
        || !caseRef.equals(claim.get("case_"))) throw unavailable();
    var definition = AuthStore.parse(claim.get("definition_"));
    if (!"SP-OP-AUTH-001".equals(definition.get("process_key"))) throw unavailable();
    var row = db.optional("""
      SELECT d.ID_ AS definition_id,d.KEY_ AS definition_key,d.VERSION_ AS definition_version,
       d.DEPLOYMENT_ID_ AS deployment_id,d.TENANT_ID_ AS definition_tenant,
       encode(sha256(b.BYTES_),'hex') AS definition_digest,
       r.ID_ AS active_id,r.PROC_INST_ID_ AS active_instance,r.PROC_DEF_ID_ AS active_definition,
       r.TENANT_ID_ AS active_tenant,h.PROC_INST_ID_ AS historic_instance,
       h.PROC_DEF_ID_ AS historic_definition,h.TENANT_ID_ AS historic_tenant,h.END_TIME_ AS ended_at
      FROM public.ACT_RE_PROCDEF d
      JOIN public.ACT_GE_BYTEARRAY b ON b.DEPLOYMENT_ID_=d.DEPLOYMENT_ID_ AND b.NAME_=d.RESOURCE_NAME_
      LEFT JOIN public.ACT_RU_EXECUTION r ON r.ID_=? AND r.PROC_INST_ID_=r.ID_
      LEFT JOIN public.ACT_HI_PROCINST h ON h.PROC_INST_ID_=?
      WHERE d.ID_=?
      """, claim.get("instance_"),claim.get("instance_"),definition.get("definition_id"));
    if (row == null || !definition.get("definition_digest").equals(row.get("definition_digest"))
        || !definition.get("deployment_id").equals(row.get("deployment_id"))) throw unavailable();
    var identity = fromClaim(claim, definition, row);
    var external = db.rows("""
      SELECT identity FROM maezo_external.mzo_external_source_head
      WHERE tenant=? AND environment=? AND engine_name=? AND database_incarnation=?
        AND identity->>'case_ref'=? ORDER BY source_ref COLLATE "C"
      """, db.scope.get("tenant"),db.scope.get("environment"),db.scope.get("engine_name"),
      db.scope.get("database_incarnation"),caseRef);
    for (var old : external) identity = reconcile(identity, ExternalCaseStore.json(old.get("identity")));
    // Reuse the existing exact runtime/historic/tenant/deployed-bytes predicate.
    var projection = ExternalCaseStore.nativeProjection(identity,db.tenant,row);
    return record("identity",identity,"native",projection,"claim_digest",hash(claim),
        "definition_claim_digest",hash(definition));
  }

  static Map<String,Object> fromClaim(Map<String,Object> claim,Map<String,Object> definition,
      Map<String,Object> row) {
    var identity = record("upstream_resource_key",claim.get("guide_"),"case_ref",claim.get("case_"),
      "process_instance_ref",claim.get("instance_"),"process_definition_id",definition.get("definition_id"),
      "process_definition_key",definition.get("process_key"),"process_definition_version",
      Long.toString(ExternalCaseStore.numberColumn(row,"definition_version")),
      "process_definition_digest",definition.get("definition_digest"),"kind","authorization");
    return ExternalCaseModels.shape("identity",identity);
  }

  static Map<String,Object> reconcile(Map<String,Object> claimed,Map<String,Object> external) {
    ExternalCaseModels.shape("identity",external);
    for (String field : claimed.keySet()) {
      // Existing canonical upstream namespace may differ from the AUTH guide ref.
      if (!field.equals("upstream_resource_key") && !claimed.get(field).equals(external.get(field)))
        throw unavailable();
    }
    return copy(external);
  }
}

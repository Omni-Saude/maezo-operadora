package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.time.Duration;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.*;
import java.util.regex.Pattern;

/** D-M / T-M2: {@code staff_escalation.v1}, read by the engine from its own {@code ACT_*} in the pinned schema.
 * The guide is the component of the anchor business key {@code AUTH-{tenant}-{guia}} (D-K.1). Reason, priority and
 * deadlines come from the ONE escalation {@code ESC-{tenant}-sla-auth-{guia}} of SP-OP-ESCALATION-001: the reason is
 * the {@code motivo_categoria} code (the DMN input, repeated by the process variable), and priority/SLA are the
 * {@code roteamento} the DMN {@code escalation_routing} produced, read from the immutable decision history (the
 * variable itself must exist, but is mutable over engine-rest and is a serialized map, so it only confirms).
 * Free text ({@code motivo}, {@code motivo_fallback}) is never read. Absent or ambiguous escalation: the guide stays,
 * every other field is null and {@code escalation_state} is {@code unresolved}; the case never disappears.
 */
final class StaffEscalation {
  private StaffEscalation(){}
  static final String PROJECTION="staff_escalation.v1";
  static final Set<String> FIELDS=Set.of("guide_number","reason_code","priority","ack_due_at","resolution_due_at");
  static final String PROCESS="SP-OP-ESCALATION-001",DECISION="escalation_routing",REASON_INPUT="in_motivo";
  static final Pattern REASON=Pattern.compile("[a-z][a-z0-9_]{0,63}"),PRIORITY=Pattern.compile("P[1-9]");
  static final Duration MAX_SLA=Duration.ofDays(366);

  static String escalationKey(String tenant,String guide){
    AuthBusinessKeys.auth(tenant,guide); // same component rules as the anchor key
    return "ESC-"+tenant+"-sla-auth-"+guide;
  }
  /** Params: tenant, business key, tenant, tenant. One row, counts only: ambiguity is data, never a thrown read. */
  static String sql(String schema){String s=EngineSchema.q(schema);return """
      WITH p AS (SELECT PROC_INST_ID_ AS id,START_TIME_ AS started FROM %1$s.ACT_HI_PROCINST
                 WHERE TENANT_ID_=? AND BUSINESS_KEY_=? AND PROC_DEF_KEY_='SP-OP-ESCALATION-001'),
       d AS (SELECT x.ID_ AS id FROM %1$s.ACT_HI_DECINST x JOIN p ON x.PROC_INST_ID_=p.id
             WHERE x.DEC_DEF_KEY_='escalation_routing' AND x.TENANT_ID_=?),
       o AS (SELECT x.VAR_NAME_ AS name,x.VAR_TYPE_ AS type,x.TEXT_ AS text FROM %1$s.ACT_HI_DEC_OUT x JOIN d ON x.DEC_INST_ID_=d.id),
       i AS (SELECT x.VAR_TYPE_ AS type,x.TEXT_ AS text FROM %1$s.ACT_HI_DEC_IN x JOIN d ON x.DEC_INST_ID_=d.id WHERE x.CLAUSE_ID_='in_motivo'),
       v AS (SELECT x.NAME_ AS name,x.VAR_TYPE_ AS type,x.TEXT_ AS text FROM %1$s.ACT_HI_VARINST x JOIN p ON x.PROC_INST_ID_=p.id
             WHERE x.TENANT_ID_=? AND x.NAME_ IN ('motivo_categoria','roteamento'))
      SELECT (SELECT count(*) FROM p) AS instances,(SELECT min(started) FROM p) AS started,
       (SELECT count(*) FROM d) AS decisions,
       (SELECT count(*) FROM o WHERE name='prioridade') AS n_priority,(SELECT min(text) FROM o WHERE name='prioridade' AND type='string') AS priority,
       (SELECT count(*) FROM o WHERE name='sla_ack') AS n_ack,(SELECT min(text) FROM o WHERE name='sla_ack' AND type='string') AS sla_ack,
       (SELECT count(*) FROM o WHERE name='sla_resolucao') AS n_resolution,(SELECT min(text) FROM o WHERE name='sla_resolucao' AND type='string') AS sla_resolution,
       (SELECT count(*) FROM i) AS n_input,(SELECT min(text) FROM i WHERE type='string') AS reason_input,
       (SELECT count(*) FROM v WHERE name='motivo_categoria') AS n_reason,(SELECT min(text) FROM v WHERE name='motivo_categoria' AND type='string') AS reason,
       (SELECT count(*) FROM v WHERE name='roteamento') AS n_routing
      """.formatted(s);}

  static Map<String,Object> read(AuthStore db,String engineSchema,String guide){
    var row=db.optional(sql(engineSchema),db.tenant,escalationKey(db.tenant,guide),db.tenant,db.tenant);
    return from(guide,row);
  }
  /** Pure: the closed projection for one guide and the aggregated row (null row = absent). */
  static Map<String,Object> from(String guide,Map<String,Object> row){
    if(guide==null||guide.isEmpty())throw unavailable();
    if(row==null||count(row,"instances")!=1||count(row,"decisions")!=1||count(row,"n_priority")!=1||count(row,"n_ack")!=1
        ||count(row,"n_resolution")!=1||count(row,"n_input")!=1||count(row,"n_reason")!=1||count(row,"n_routing")!=1)return unresolved(guide);
    Object reason=row.get("reason"),priority=row.get("priority");
    if(!(reason instanceof String code)||!REASON.matcher(code).matches()||!code.equals(row.get("reason_input"))
        ||!(priority instanceof String p)||!PRIORITY.matcher(p).matches())return unresolved(guide);
    Duration ack=duration(row.get("sla_ack")),resolution=duration(row.get("sla_resolution"));
    Instant started;try{started=ExternalCaseStore.instantColumn(row.get("started"));}catch(Rejected r){return unresolved(guide);}
    if(ack==null||resolution==null||ack.compareTo(resolution)>0)return unresolved(guide);
    return record("guide_number",guide,"reason_code",code,"priority",p,"ack_due_at",time(started.plus(ack)),
      "resolution_due_at",time(started.plus(resolution)),"escalation_state","resolved");
  }
  static Map<String,Object> unresolved(String guide){
    return record("guide_number",guide,"reason_code",null,"priority",null,"ack_due_at",null,"resolution_due_at",null,
      "escalation_state","unresolved");
  }
  /** ISO-8601 time-based duration (PnDTnHnMnS), strictly positive and bounded; anything else is unresolved. */
  static Duration duration(Object value){
    if(!(value instanceof String text)||text.length()>32||!text.startsWith("P"))return null;
    try{var d=Duration.parse(text);return d.isNegative()||d.isZero()||d.compareTo(MAX_SLA)>0?null:d;}
    catch(DateTimeParseException failure){return null;}
  }
  static long count(Map<String,Object> row,String column){return row.get(column) instanceof Number n?n.longValue():-1;}
  /** list: `***` + the last four characters; a guide of four or fewer characters is fully masked. */
  static String mask(String guide){return guide.length()>4?"***"+guide.substring(guide.length()-4):"***";}
  /** The projection an operation emits: the list masks the guide, the audited detail carries it whole. */
  static Map<String,Object> forOperation(Map<String,Object> escalation,String operation){
    var result=copy(escalation);
    if("list".equals(operation))result.put("guide_number",mask(str(escalation,"guide_number")));
    else if(!"detail".equals(operation))throw invalid();
    return result;
  }
  /** The grant authorizes the projection only with its exact closed field set; absent means not emitted. */
  static boolean authorized(Map<String,Set<String>> fields){
    var granted=fields.get(PROJECTION);if(granted==null)return false;
    if(!FIELDS.equals(granted))throw denied();return true;
  }
}

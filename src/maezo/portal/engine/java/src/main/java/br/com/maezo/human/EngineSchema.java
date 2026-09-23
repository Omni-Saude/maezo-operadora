package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.util.*;

/** D-I (T1.8b): the PostgreSQL schema of the engine's own {@code ACT_*} relations
 * ({@code cibseven} in the ADR-0060 D-C2 layout) is a deployment pin, never the constant
 * {@code public}. Closed lowercase identifier, compared exactly; never a shared, system
 * or native schema. The SQL uses it only after validation, double-quoted.
 */
final class EngineSchema {
  private EngineSchema(){}
  static final Set<String> RESERVED=Set.of("public","information_schema","maezo_native");
  /** Relations the plugin reads; each must exist in the pinned schema and be readable. */
  static final List<String> TABLES=List.of("act_ge_bytearray","act_hi_procinst","act_re_procdef","act_ru_execution","act_ru_task");
  static String require(String value,String nativeSchema){
    if(value==null||!value.matches("[a-z_][a-z0-9_]{0,62}")||RESERVED.contains(value)||value.startsWith("pg_")
        ||value.equals(nativeSchema))throw unavailable();
    return value;
  }
  static String q(String schema){return "\""+require(schema,null)+"\"";}
  /** Positional parameters: schema, relation name. */
  static final String PIN="""
    SELECT c.relkind,has_table_privilege(session_user,c.oid,'SELECT') AS can_read
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname=? AND c.relname=?
    """;
  static void requireTable(Map<String,Object> row){
    if(row==null||!"r".equals(row.get("relkind"))||!Boolean.TRUE.equals(row.get("can_read")))throw unavailable();
  }
  /** The engine configuration must not point the engine anywhere else. */
  static void requireEngineConfiguration(String schema,String databaseSchema,String prefix){
    if(databaseSchema!=null&&!databaseSchema.equals(schema)||prefix!=null&&!prefix.isEmpty()&&!prefix.equals(schema+"."))throw unavailable();
  }
  /** Params: instance, instance, definition. */
  static String identitySql(String schema){String s=q(schema);return """
      SELECT d.ID_ AS definition_id,d.KEY_ AS definition_key,d.VERSION_ AS definition_version,
       d.DEPLOYMENT_ID_ AS deployment_id,d.TENANT_ID_ AS definition_tenant,
       encode(sha256(b.BYTES_),'hex') AS definition_digest,
       r.ID_ AS active_id,r.PROC_INST_ID_ AS active_instance,r.PROC_DEF_ID_ AS active_definition,
       r.TENANT_ID_ AS active_tenant,h.PROC_INST_ID_ AS historic_instance,
       h.PROC_DEF_ID_ AS historic_definition,h.TENANT_ID_ AS historic_tenant,h.END_TIME_ AS ended_at
      FROM %1$s.ACT_RE_PROCDEF d
      JOIN %1$s.ACT_GE_BYTEARRAY b ON b.DEPLOYMENT_ID_=d.DEPLOYMENT_ID_ AND b.NAME_=d.RESOURCE_NAME_
      LEFT JOIN %1$s.ACT_RU_EXECUTION r ON r.ID_=? AND r.PROC_INST_ID_=r.ID_
      LEFT JOIN %1$s.ACT_HI_PROCINST h ON h.PROC_INST_ID_=?
      WHERE d.ID_=?
      """.formatted(s);}
  /** Params: (case_ref, instance, definition) per row. */
  static String statesSql(String schema,int rows){
    if(rows<1)throw unavailable();String s=q(schema);
    return "SELECT v.case_ref,d.ID_ AS definition_id,d.KEY_ AS definition_key,d.VERSION_ AS definition_version,d.TENANT_ID_ AS definition_tenant,encode(sha256(b.BYTES_),'hex') AS definition_digest,r.ID_ AS active_id,r.PROC_INST_ID_ AS active_instance,r.PROC_DEF_ID_ AS active_definition,r.TENANT_ID_ AS active_tenant,h.PROC_INST_ID_ AS historic_instance,h.PROC_DEF_ID_ AS historic_definition,h.TENANT_ID_ AS historic_tenant,h.END_TIME_ AS ended_at FROM (VALUES "
      +String.join(",",Collections.nCopies(rows,"(CAST(? AS text),CAST(? AS text),CAST(? AS text))"))
      +") v(case_ref,instance_id,definition_id) JOIN "+s+".ACT_RE_PROCDEF d ON d.ID_=v.definition_id JOIN "+s+".ACT_GE_BYTEARRAY b ON b.DEPLOYMENT_ID_=d.DEPLOYMENT_ID_ AND b.NAME_=d.RESOURCE_NAME_ LEFT JOIN "+s+".ACT_RU_EXECUTION r ON r.ID_=v.instance_id AND r.PROC_INST_ID_=r.ID_ LEFT JOIN "+s+".ACT_HI_PROCINST h ON h.PROC_INST_ID_=v.instance_id";
  }
  /** Params: tenant, instance. */
  static String taskSql(String schema){
    return "SELECT ID_,PROC_INST_ID_,PROC_DEF_ID_,TASK_DEF_KEY_,REV_,CREATE_TIME_,TENANT_ID_ FROM "+q(schema)+".ACT_RU_TASK WHERE TENANT_ID_=? AND PROC_INST_ID_=? ORDER BY ID_ COLLATE \"C\" FOR SHARE";
  }
}

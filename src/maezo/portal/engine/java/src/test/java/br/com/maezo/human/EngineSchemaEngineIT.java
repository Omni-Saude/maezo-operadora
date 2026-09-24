package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.sql.*;
import java.util.*;
import org.junit.jupiter.api.*;

/** D-I (T1.8b) against a real PostgreSQL catalog, in the ADR-0060 D-C2 layout: ACT_* in
 * {@code cibseven}, {@code mzo_*} path first ({@code maezo_native}), nothing in public.
 * The runtime login runs the exact boot pin and the exact ACT_* SQL of the plugin.
 * Explicit integration lane (surefire excludes *EngineIT); settings as in AtomicEngineIT.
 */
@Tag("integration")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class EngineSchemaEngineIT {
  static final String ENGINE="cibseven",NATIVE="maezo_native";
  static final String TENANT="tenant-a",INSTANCE="inst-1",DEFINITION="SP-OP-AUTH-001:1:def",DONE="inst-2";
  String adminUrl,user,password,database,owner,runtime,runtimePassword;

  static String env(String name){String v=System.getenv(name);
    if(v==null||v.isBlank())throw new IllegalStateException("explicit integration setting missing: "+name);return v;}
  String url(String db,String path){
    var m=java.util.regex.Pattern.compile("^(jdbc:postgresql://[^/?]+/)[^/?]+(\\?.*)?$").matcher(adminUrl);
    if(!m.matches())throw new IllegalStateException("integration admin URL must be jdbc:postgresql://host[:port]/database");
    String query=m.group(2)==null?"":m.group(2);
    return m.group(1)+db+(path==null?query:query+(query.isEmpty()?"?":"&")+"currentSchema="+path);
  }
  Connection admin(String db)throws SQLException{return DriverManager.getConnection(db==null?adminUrl:url(db,null),user,password);}
  Connection runtime()throws SQLException{return DriverManager.getConnection(url(database,NATIVE+","+ENGINE),runtime,runtimePassword);}
  static List<Map<String,Object>> rows(Connection c,String sql,Object...args)throws SQLException{
    try(var s=c.prepareStatement(sql)){for(int i=0;i<args.length;i++)s.setObject(i+1,args[i]);
      try(var r=s.executeQuery()){var out=new ArrayList<Map<String,Object>>();
        while(r.next()){var row=new HashMap<String,Object>();
          for(int i=1;i<=r.getMetaData().getColumnCount();i++)row.put(r.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),r.getObject(i));
          out.add(row);}
        return out;}}
  }
  static Map<String,Object> optional(Connection c,String sql,Object...args)throws SQLException{
    var r=rows(c,sql,args);assertTrue(r.size()<=1,sql);return r.isEmpty()?null:r.get(0);}

  @BeforeAll void install()throws Exception{
    adminUrl=env("MAEZO_HUMAN_IT_JDBC_URL");user=env("MAEZO_HUMAN_IT_DB_USER");password=env("MAEZO_HUMAN_IT_DB_PASSWORD");
    if(adminUrl.matches("(?i).*([?&])(currentSchema|options)=.*"))throw new IllegalStateException("integration admin URL must not override schema");
    String token=UUID.randomUUID().toString().replace("-","").substring(0,12);
    database="t18b_"+token;owner="t18b_owner_"+token;runtime="t18b_rt_"+token;runtimePassword=UUID.randomUUID().toString();
    try(var c=admin(null);var s=c.createStatement()){
      s.execute("CREATE ROLE "+owner+" NOLOGIN NOSUPERUSER NOBYPASSRLS");
      s.execute("CREATE ROLE "+runtime+" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '"+runtimePassword+"'");
      s.execute("CREATE DATABASE "+database);
    }
    try(var c=admin(database);var s=c.createStatement()){
      s.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC");
      for(String schema:List.of(ENGINE,NATIVE,"engine_empty")){
        s.execute("CREATE SCHEMA "+schema+" AUTHORIZATION "+owner);s.execute("GRANT USAGE ON SCHEMA "+schema+" TO "+runtime);}
      s.execute("SET ROLE "+owner);
      s.execute("CREATE TABLE cibseven.ACT_RE_PROCDEF(ID_ varchar(64) PRIMARY KEY,KEY_ varchar(255),VERSION_ integer,DEPLOYMENT_ID_ varchar(64),TENANT_ID_ varchar(64),RESOURCE_NAME_ varchar(4000))");
      s.execute("CREATE TABLE cibseven.ACT_GE_BYTEARRAY(ID_ varchar(64) PRIMARY KEY,DEPLOYMENT_ID_ varchar(64),NAME_ varchar(255),BYTES_ bytea)");
      s.execute("CREATE TABLE cibseven.ACT_RU_EXECUTION(ID_ varchar(64) PRIMARY KEY,PROC_INST_ID_ varchar(64),PROC_DEF_ID_ varchar(64),TENANT_ID_ varchar(64))");
      s.execute("CREATE TABLE cibseven.ACT_HI_PROCINST(ID_ varchar(64) PRIMARY KEY,PROC_INST_ID_ varchar(64),PROC_DEF_ID_ varchar(64),TENANT_ID_ varchar(64),END_TIME_ timestamp,BUSINESS_KEY_ varchar(255),PROC_DEF_KEY_ varchar(255),START_TIME_ timestamp)");
      // D-M: the history relations of CIB 2.1 the escalation projection reads (column subset, same names/types).
      s.execute("CREATE TABLE cibseven.ACT_HI_DECINST(ID_ varchar(64) PRIMARY KEY,DEC_DEF_KEY_ varchar(255),PROC_INST_ID_ varchar(64),TENANT_ID_ varchar(64))");
      s.execute("CREATE TABLE cibseven.ACT_HI_DEC_IN(ID_ varchar(64) PRIMARY KEY,DEC_INST_ID_ varchar(64),CLAUSE_ID_ varchar(64),VAR_TYPE_ varchar(100),TEXT_ varchar(4000))");
      s.execute("CREATE TABLE cibseven.ACT_HI_DEC_OUT(ID_ varchar(64) PRIMARY KEY,DEC_INST_ID_ varchar(64),VAR_NAME_ varchar(255),VAR_TYPE_ varchar(100),TEXT_ varchar(4000))");
      s.execute("CREATE TABLE cibseven.ACT_HI_VARINST(ID_ varchar(64) PRIMARY KEY,PROC_INST_ID_ varchar(64),NAME_ varchar(255),VAR_TYPE_ varchar(100),TEXT_ varchar(4000),TENANT_ID_ varchar(64))");
      escalation(s,"esc-1","ESC-"+TENANT+"-sla-auth-G1",TENANT,"solicitacao_humano");
      escalation(s,"esc-2","ESC-"+TENANT+"-sla-auth-G2",TENANT,"falha_tecnica");escalation(s,"esc-3","ESC-"+TENANT+"-sla-auth-G2",TENANT,"falha_tecnica");
      escalation(s,"esc-4","ESC-tenant-b-sla-auth-G1","tenant-b","red_flag_clinico");
      s.execute("CREATE TABLE cibseven.ACT_RU_TASK(ID_ varchar(64) PRIMARY KEY,REV_ integer,PROC_INST_ID_ varchar(64),PROC_DEF_ID_ varchar(64),TASK_DEF_KEY_ varchar(255),CREATE_TIME_ timestamp,TENANT_ID_ varchar(64))");
      s.execute("INSERT INTO cibseven.ACT_RE_PROCDEF VALUES('"+DEFINITION+"','SP-OP-AUTH-001',1,'dep-1','"+TENANT+"','auth.bpmn')");
      s.execute("INSERT INTO cibseven.ACT_GE_BYTEARRAY VALUES('b-1','dep-1','auth.bpmn',convert_to('<bpmn/>','UTF8'))");
      s.execute("INSERT INTO cibseven.ACT_RU_EXECUTION VALUES('"+INSTANCE+"','"+INSTANCE+"','"+DEFINITION+"','"+TENANT+"')");
      s.execute("INSERT INTO cibseven.ACT_HI_PROCINST(ID_,PROC_INST_ID_,PROC_DEF_ID_,TENANT_ID_,END_TIME_) VALUES('h-1','"+INSTANCE+"','"+DEFINITION+"','"+TENANT+"',NULL),('h-2','"+DONE+"','"+DEFINITION+"','"+TENANT+"',now())");
      s.execute("INSERT INTO cibseven.ACT_RU_TASK VALUES('task-1',1,'"+INSTANCE+"','"+DEFINITION+"','review',now(),'"+TENANT+"')");
      s.execute("GRANT SELECT ON ALL TABLES IN SCHEMA cibseven TO "+runtime);
      s.execute("GRANT UPDATE ON cibseven.ACT_RU_TASK TO "+runtime);
      s.execute("CREATE TABLE maezo_native.mzo_human_tenant(TENANT_ varchar(64) PRIMARY KEY,REV_ bigint NOT NULL)");
      s.execute("CREATE TABLE maezo_native.mzo_portal_read_revocation(TENANT_ varchar(64),ENVIRONMENT_ varchar(64),ENGINE_ varchar(64),INCARNATION_ varchar(64),FINGERPRINT_ char(64),PRIMARY KEY(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,FINGERPRINT_))");
      s.execute("INSERT INTO maezo_native.mzo_human_tenant VALUES('"+TENANT+"',7),('tenant-b',9)");
      s.execute("INSERT INTO maezo_native.mzo_portal_read_revocation VALUES('"+TENANT+"','dev','engine','inc','"+"a".repeat(64)+"'),('tenant-b','dev','engine','inc','"+"b".repeat(64)+"')");
      s.execute("GRANT SELECT,UPDATE ON maezo_native.mzo_human_tenant TO "+runtime);
      s.execute("GRANT SELECT ON maezo_native.mzo_portal_read_revocation TO "+runtime);
      s.execute("RESET ROLE");
      var none=optional(c,"SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname LIKE 'act\\_%'");
      assertEquals(0L,((Number)none.get("n")).longValue());
    }
  }
  /** One escalation as CIB writes it: the instance, ONE escalation_routing decision (input + 4 outputs), two variables. */
  static void escalation(Statement s,String id,String key,String tenant,String reason)throws SQLException{
    s.execute("INSERT INTO cibseven.ACT_HI_PROCINST VALUES('hp-"+id+"','"+id+"','SP-OP-ESCALATION-001:1:x','"+tenant+"',NULL,'"+key+"','SP-OP-ESCALATION-001',TIMESTAMP '2026-09-24 12:00:00')");
    s.execute("INSERT INTO cibseven.ACT_HI_DECINST VALUES('d-"+id+"','escalation_routing','"+id+"','"+tenant+"')");
    s.execute("INSERT INTO cibseven.ACT_HI_DEC_IN VALUES('i-"+id+"','d-"+id+"','in_motivo','string','"+reason+"'),('j-"+id+"','d-"+id+"','in_severidade','string','moderada')");
    s.execute("INSERT INTO cibseven.ACT_HI_DEC_OUT VALUES('o1-"+id+"','d-"+id+"','prioridade','string','P3'),('o2-"+id+"','d-"+id+"','grupo_atendimento','string','atendimento-humano'),"
      +"('o3-"+id+"','d-"+id+"','sla_ack','string','PT30M'),('o4-"+id+"','d-"+id+"','sla_resolucao','string','PT4H')");
    s.execute("INSERT INTO cibseven.ACT_HI_VARINST VALUES('v1-"+id+"','"+id+"','motivo_categoria','string','"+reason+"','"+tenant+"'),"
      +"('v2-"+id+"','"+id+"','roteamento','serializable',NULL,'"+tenant+"'),('v3-"+id+"','"+id+"','motivo','string','texto livre','"+tenant+"')");
  }
  @AfterAll void drop()throws Exception{
    if(database==null)return;
    try(var c=admin(null);var s=c.createStatement()){
      s.execute("DROP DATABASE IF EXISTS "+database+" WITH (FORCE)");
      s.execute("DROP ROLE IF EXISTS "+runtime);s.execute("DROP ROLE IF EXISTS "+owner);
    }
  }

  @Test void bootPinAcceptsThePinnedEngineSchema()throws Exception{
    try(var c=runtime()){for(String t:EngineSchema.TABLES)EngineSchema.requireTable(optional(c,EngineSchema.PIN,ENGINE,t));}
  }
  @Test void bootPinRefusesAWrongEngineSchema()throws Exception{
    try(var c=runtime()){
      for(String wrong:List.of("engine_empty","public",NATIVE,"absent_schema"))
        for(String t:EngineSchema.TABLES){var row=optional(c,EngineSchema.PIN,wrong,t);
          assertThrows(Rejected.class,()->EngineSchema.requireTable(row),wrong+"."+t);}
    }
    try(var c=admin(database);var s=c.createStatement()){s.execute("REVOKE SELECT ON cibseven.ACT_RU_TASK FROM "+runtime);}
    try(var c=runtime()){var row=optional(c,EngineSchema.PIN,ENGINE,"act_ru_task");
      assertThrows(Rejected.class,()->EngineSchema.requireTable(row));}
    finally{try(var c=admin(database);var s=c.createStatement()){s.execute("GRANT SELECT ON cibseven.ACT_RU_TASK TO "+runtime);}}
  }
  @Test void identityQueryReadsCibseven()throws Exception{
    try(var c=runtime()){var row=optional(c,EngineSchema.identitySql(ENGINE),INSTANCE,INSTANCE,DEFINITION);
      assertNotNull(row);assertEquals(DEFINITION,row.get("definition_id"));assertEquals("dep-1",row.get("deployment_id"));
      assertEquals(INSTANCE,row.get("active_id"));assertEquals(INSTANCE,row.get("historic_instance"));
      assertEquals(64,((String)row.get("definition_digest")).length());}
  }
  @Test void statesQueryReadsCibsevenForActiveAndEnded()throws Exception{
    try(var c=runtime()){var r=rows(c,EngineSchema.statesSql(ENGINE,2),"case-a",INSTANCE,DEFINITION,"case-b",DONE,DEFINITION);
      assertEquals(2,r.size());var byRef=new HashMap<Object,Map<String,Object>>();for(var row:r)byRef.put(row.get("case_ref"),row);
      assertEquals(INSTANCE,byRef.get("case-a").get("active_id"));
      assertNull(byRef.get("case-b").get("active_id"));assertNotNull(byRef.get("case-b").get("ended_at"));}
  }
  @Test void taskQueryReadsCibseven()throws Exception{
    try(var c=runtime()){c.setAutoCommit(false);
      var r=rows(c,EngineSchema.taskSql(ENGINE),TENANT,INSTANCE);assertEquals(1,r.size());assertEquals("task-1",r.get(0).get("id_"));
      assertEquals(0,rows(c,EngineSchema.taskSql(ENGINE),"tenant-b",INSTANCE).size());c.rollback();}
  }
  /** Ressalva #489: the external path reads its native mzo_* rows from the pinned native schema, not public. */
  @Test void externalStoreReadsNativeRelationsFromThePinnedSchema()throws Exception{
    try(var c=runtime()){
      for(String t:ExternalCaseStore.NATIVE_TABLES)EngineSchema.requireTable(optional(c,EngineSchema.PIN,NATIVE,t));
      for(String t:ExternalCaseStore.NATIVE_TABLES){var row=optional(c,EngineSchema.PIN,"public",t);
        assertThrows(Rejected.class,()->EngineSchema.requireTable(row),"public."+t);}
      c.setAutoCommit(false);
      var tenant=optional(c,ExternalCaseStore.tenantLockSql(NATIVE),TENANT);
      assertEquals(7L,((Number)tenant.get("rev_")).longValue());
      var revoked=rows(c,ExternalCaseStore.revokedSql(NATIVE),TENANT,"dev","engine","inc");
      assertEquals(1,revoked.size());assertEquals("a".repeat(64),revoked.get(0).get("fingerprint_"));
      c.rollback();
      assertThrows(SQLException.class,()->rows(c,"SELECT rev_ FROM public.mzo_human_tenant WHERE tenant_=? FOR UPDATE",TENANT));
      c.rollback();
      assertThrows(SQLException.class,()->rows(c,ExternalCaseStore.tenantLockSql("engine_empty"),TENANT));
      c.rollback();
    }
    assertThrows(Rejected.class,()->ExternalCaseStore.tenantLockSql("public"));
    assertThrows(Rejected.class,()->ExternalCaseStore.revokedSql("x\";drop"));
  }
  Map<String,Object> escalationRow(Connection c,String tenant,String guide)throws SQLException{
    // Raw key: the fixture tenants contain "-", which escalationKey (the anchor rule) refuses by design.
    return optional(c,StaffEscalation.sql(ENGINE),tenant,"ESC-"+tenant+"-sla-auth-"+guide,tenant,tenant);
  }
  /** D-M / T-M2: the exact projection SQL, as the runtime login, in the D-C2 layout. */
  @Test void escalationProjectionReadsTheDmnHistoryInCibseven()throws Exception{
    try(var c=runtime()){
      var e=StaffEscalation.from("G1",escalationRow(c,TENANT,"G1"));
      assertEquals("resolved",e.get("escalation_state"));assertEquals("solicitacao_humano",e.get("reason_code"));assertEquals("P3",e.get("priority"));
      var started=java.sql.Timestamp.valueOf("2026-09-24 12:00:00").toInstant();
      assertEquals(PortalReadModels.time(started.plusSeconds(1800)),e.get("ack_due_at"));
      assertEquals(PortalReadModels.time(started.plusSeconds(4*3600)),e.get("resolution_due_at"));
      assertFalse(e.containsValue("texto livre"));
      // Two escalations under one key: ambiguous, never the first one found.
      assertEquals("unresolved",StaffEscalation.from("G2",escalationRow(c,TENANT,"G2")).get("escalation_state"));
      // No escalation: unresolved, guide kept. Another tenant's escalation under the same guide never answers.
      var absent=StaffEscalation.from("G9",escalationRow(c,TENANT,"G9"));assertEquals("unresolved",absent.get("escalation_state"));assertEquals("G9",absent.get("guide_number"));
      assertEquals("red_flag_clinico",StaffEscalation.from("G1",escalationRow(c,"tenant-b","G1")).get("reason_code"));
    }
    try(var c=admin(database);var s=c.createStatement()){s.execute("UPDATE cibseven.ACT_HI_VARINST SET TEXT_='falha_tecnica' WHERE ID_='v1-esc-1'");}
    try(var c=runtime()){assertEquals("unresolved",StaffEscalation.from("G1",escalationRow(c,TENANT,"G1")).get("escalation_state"));}
    finally{try(var c=admin(database);var s=c.createStatement()){s.execute("UPDATE cibseven.ACT_HI_VARINST SET TEXT_='solicitacao_humano' WHERE ID_='v1-esc-1'");}}
    try(var c=runtime()){assertThrows(SQLException.class,()->rows(c,StaffEscalation.sql("engine_empty"),TENANT,"k",TENANT,TENANT));}
  }
  @Test void wrongSchemaQueriesFailInsteadOfReadingElsewhere()throws Exception{
    try(var c=runtime()){assertThrows(SQLException.class,()->rows(c,EngineSchema.identitySql("engine_empty"),INSTANCE,INSTANCE,DEFINITION));}
  }
}

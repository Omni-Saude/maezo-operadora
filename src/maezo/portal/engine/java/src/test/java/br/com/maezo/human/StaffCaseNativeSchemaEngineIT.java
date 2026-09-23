package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.sql.*;
import java.util.*;
import org.junit.jupiter.api.*;

/** ADR-0060 D4 (T1.8) against a real PostgreSQL catalog: the staff pin query of
 * StaffCaseStore, run as a LOGIN runtime role in a throwaway database, must accept only
 * the relation that the plugin's UNQUALIFIED SQL actually resolves to.
 *
 * Explicit integration lane (surefire excludes *EngineIT). No engine is started: this
 * exercises the exact PIN text and the static checks the constructor applies to its row.
 * Missing settings fail explicitly, as in AtomicEngineIT.
 */
@Tag("integration")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class StaffCaseNativeSchemaEngineIT {
  static final String TABLE="mzo_staff_case_checkpoint_chunk",SCHEMA="maezo_native";
  static final String WRITES="""
    SELECT has_table_privilege(session_user,CAST(? AS oid),'INSERT') AS ins,
      has_table_privilege(session_user,CAST(? AS oid),'UPDATE') AS upd,
      has_table_privilege(session_user,CAST(? AS oid),'DELETE') AS del,
      has_table_privilege(session_user,CAST(? AS oid),'TRUNCATE') AS trunc
    """;
  String adminUrl,user,password,database,owner,runtime,runtimePassword;
  StaffCaseStore.RelationPin pin;

  static String env(String name){String v=System.getenv(name);
    if(v==null||v.isBlank())throw new IllegalStateException("explicit integration setting missing: "+name);return v;}
  String url(String db,String path){
    var m=java.util.regex.Pattern.compile("^(jdbc:postgresql://[^/?]+/)[^/?]+(\\?.*)?$").matcher(adminUrl);
    if(!m.matches())throw new IllegalStateException("integration admin URL must be jdbc:postgresql://host[:port]/database");
    String query=m.group(2)==null?"":m.group(2);
    return m.group(1)+db+(path==null?query:query+(query.isEmpty()?"?":"&")+"currentSchema="+path);
  }
  Connection admin(String db)throws SQLException{return DriverManager.getConnection(db==null?adminUrl:url(db,null),user,password);}
  Connection runtime(String path)throws SQLException{return DriverManager.getConnection(url(database,path),runtime,runtimePassword);}
  static Map<String,Object> one(Connection c,String sql,Object...args)throws SQLException{
    try(var s=c.prepareStatement(sql)){for(int i=0;i<args.length;i++)s.setObject(i+1,args[i]);
      try(var r=s.executeQuery()){assertTrue(r.next(),sql);var row=new HashMap<String,Object>();
        for(int i=1;i<=r.getMetaData().getColumnCount();i++)row.put(r.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),r.getObject(i));
        assertFalse(r.next(),sql);return row;}}
  }
  static Map<String,Object> pinRow(Connection c,String schema)throws SQLException{return one(c,StaffCaseStore.PIN,TABLE,schema,TABLE);}

  @BeforeAll void install()throws Exception{
    adminUrl=env("MAEZO_HUMAN_IT_JDBC_URL");user=env("MAEZO_HUMAN_IT_DB_USER");password=env("MAEZO_HUMAN_IT_DB_PASSWORD");
    if(adminUrl.matches("(?i).*([?&])(currentSchema|options)=.*"))throw new IllegalStateException("integration admin URL must not override schema");
    String token=UUID.randomUUID().toString().replace("-","").substring(0,12);
    database="t18_"+token;owner="t18_owner_"+token;runtime="t18_rt_"+token;runtimePassword=UUID.randomUUID().toString();
    try(var c=admin(null);var s=c.createStatement()){
      s.execute("CREATE ROLE "+owner+" NOLOGIN NOSUPERUSER NOBYPASSRLS");
      s.execute("CREATE ROLE "+runtime+" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '"+runtimePassword+"'");
      s.execute("CREATE DATABASE "+database);
    }
    try(var c=admin(database);var s=c.createStatement()){
      // Same owner and same name in three schemas: only the namespace and the path differ.
      for(String schema:List.of(SCHEMA,"maezo_native_v2","public")){
        if(!schema.equals("public"))s.execute("CREATE SCHEMA "+schema+" AUTHORIZATION "+owner);
        s.execute("CREATE TABLE "+schema+"."+TABLE+"(chunk_index integer NOT NULL)");
        s.execute("ALTER TABLE "+schema+"."+TABLE+" OWNER TO "+owner);
        s.execute("GRANT USAGE ON SCHEMA "+schema+" TO "+runtime);
        s.execute("REVOKE ALL ON "+schema+"."+TABLE+" FROM PUBLIC");
        s.execute("GRANT SELECT,INSERT ON "+schema+"."+TABLE+" TO "+runtime);
      }
      var row=one(c,"SELECT c.oid::bigint AS oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=? AND c.relname=?",SCHEMA,TABLE);
      pin=new StaffCaseStore.RelationPin(((Number)row.get("oid")).longValue(),owner);
    }
  }
  @AfterAll void drop()throws Exception{
    if(database==null)return;
    try(var c=admin(null);var s=c.createStatement()){
      s.execute("DROP DATABASE IF EXISTS "+database+" WITH (FORCE)");
      s.execute("DROP ROLE IF EXISTS "+runtime);s.execute("DROP ROLE IF EXISTS "+owner);
    }
  }
  static void acceptedByPin(Map<String,Object> row,String schema,StaffCaseStore.RelationPin pin){
    assertEquals(pin.oid(),((Number)row.get("oid")).longValue());assertEquals(pin.owner(),row.get("owner"));
    assertEquals("r",row.get("relkind"));assertEquals(Boolean.FALSE,row.get("relrowsecurity"));
    assertEquals(Boolean.FALSE,row.get("owner_member"));assertEquals(Boolean.TRUE,row.get("can_read"));
    StaffCaseStore.requireResolved(row,schema,pin);StaffCaseStore.requireNamespace(row,pin);
  }
  void adminExecute(String sql)throws SQLException{try(var c=admin(database);var s=c.createStatement()){s.execute(sql);}}
  Map<String,Object> pinned()throws SQLException{try(var c=runtime(SCHEMA)){return pinRow(c,SCHEMA);}}

  @Test void pinnedSchemaOnThePathIsAccepted()throws Exception{
    try(var c=runtime(SCHEMA+",pg_catalog")){acceptedByPin(pinRow(c,SCHEMA),SCHEMA,pin);}
  }
  @Test void publicHomonymIsRefused()throws Exception{
    try(var c=runtime(SCHEMA)){var row=pinRow(c,"public");
      assertNotEquals(pin.oid(),((Number)row.get("oid")).longValue());
      assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(row,"public",pin));}
    // And with public in front of the path, the unqualified name resolves to the homonym.
    try(var c=runtime("public,"+SCHEMA)){var row=pinRow(c,SCHEMA);
      assertEquals(pin.oid(),((Number)row.get("oid")).longValue());
      assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(row,SCHEMA,pin));}
  }
  @Test void prefixSharingSchemaIsAnotherSchema()throws Exception{
    try(var c=runtime("maezo_native_v2")){var row=pinRow(c,"maezo_native_v2");
      assertNotEquals(pin.oid(),((Number)row.get("oid")).longValue());
      assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(row,"maezo_native_v2",pin));
      assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(pinRow(c,SCHEMA),SCHEMA,pin));}
  }
  @Test void pgTempHomonymIsRefusedByToRegclass()throws Exception{
    try(var c=runtime(SCHEMA)){
      try(var s=c.createStatement()){s.execute("CREATE TEMP TABLE "+TABLE+"(chunk_index integer NOT NULL)");}
      var row=pinRow(c,SCHEMA);
      // The namespace-filtered catalog row alone still matches the pin: that is the hole.
      assertEquals(pin.oid(),((Number)row.get("oid")).longValue());assertEquals(SCHEMA,row.get("current_schema"));
      assertNotEquals(pin.oid(),((Number)row.get("resolved")).longValue());
      assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(row,SCHEMA,pin));
      try(var s=c.createStatement()){s.execute("DROP TABLE pg_temp."+TABLE);}
      acceptedByPin(pinRow(c,SCHEMA),SCHEMA,pin);
    }
  }
  @Test void schemaOwnedByAnotherRoleIsRefused()throws Exception{
    adminExecute("ALTER SCHEMA "+SCHEMA+" OWNER TO "+runtime);
    try{var row=pinned();assertEquals(runtime,row.get("schema_owner"));
      StaffCaseStore.requireResolved(row,SCHEMA,pin);
      assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(row,pin));}
    finally{adminExecute("ALTER SCHEMA "+SCHEMA+" OWNER TO "+owner);}
    StaffCaseStore.requireNamespace(pinned(),pin);
  }
  @Test void runtimeWithCreateOnTheSchemaIsRefused()throws Exception{
    adminExecute("GRANT CREATE ON SCHEMA "+SCHEMA+" TO "+runtime);
    try{var row=pinned();assertEquals(Boolean.TRUE,row.get("runtime_create"));assertEquals(Boolean.FALSE,row.get("public_create"));
      assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(row,pin));}
    finally{adminExecute("REVOKE CREATE ON SCHEMA "+SCHEMA+" FROM "+runtime);}
    StaffCaseStore.requireNamespace(pinned(),pin);
  }
  @Test void publicWithCreateOnTheSchemaIsRefused()throws Exception{
    adminExecute("GRANT CREATE ON SCHEMA "+SCHEMA+" TO PUBLIC");
    try{var row=pinned();assertEquals(Boolean.TRUE,row.get("public_create"));
      assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(row,pin));}
    finally{adminExecute("REVOKE CREATE ON SCHEMA "+SCHEMA+" FROM PUBLIC");}
    StaffCaseStore.requireNamespace(pinned(),pin);
  }
  @Test void checkpointChunkSelectInsertIsAcceptedAndUpdateIsRefused()throws Exception{
    try(var c=runtime(SCHEMA)){StaffCaseStore.requireWrites(true,false,one(c,WRITES,pin.oid(),pin.oid(),pin.oid(),pin.oid()));}
    try(var c=admin(database);var s=c.createStatement()){s.execute("GRANT UPDATE ON "+SCHEMA+"."+TABLE+" TO "+runtime);}
    try(var c=runtime(SCHEMA)){var writes=one(c,WRITES,pin.oid(),pin.oid(),pin.oid(),pin.oid());
      assertEquals(Boolean.TRUE,writes.get("upd"));
      assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(true,false,writes));}
    finally{try(var c=admin(database);var s=c.createStatement()){s.execute("REVOKE UPDATE ON "+SCHEMA+"."+TABLE+" FROM "+runtime);}}
  }
}

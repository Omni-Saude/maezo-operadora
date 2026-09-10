package br.com.maezo.human;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;

/** Explicit owner migration/designation and runtime readback; no runtime self-installation. */
public final class AuthInstallation {
  private AuthInstallation() {}
  static final List<String> TABLES=List.of("INSTALLATION","TRUST","REVOKED_KEY","INPUT_HEAD","INPUT_VERSION","GUIDE_CLAIM","INSTANCE_HEAD","DOC_OCCURRENCE","EFFECT_RECEIPT");
  static final Set<String> INSERTS=Set.of("INPUT_HEAD","INPUT_VERSION","GUIDE_CLAIM","INSTANCE_HEAD","DOC_OCCURRENCE","EFFECT_RECEIPT");
  static final Set<String> UPDATES=Set.of("INPUT_HEAD","INSTANCE_HEAD","DOC_OCCURRENCE");
  static String identifier(String value){if(!value.matches("[a-z_][a-z0-9_]{0,62}"))throw Rejected.invalid();return "\""+value+"\"";}
  static Map<String,Object> binding(Object value) {
    var b=Jcs.object(value);Jcs.keys(b,"schema","database_name","database_oid","schema_name","schema_oid","owner_role","runtime_role");
    if(!"human-auth-native-database.v1".equals(b.get("schema")))throw Rejected.invalid();
    for(String name:List.of("database_name","owner_role","runtime_role"))Jcs.ref(b,name);
    identifier(Jcs.string(b,"schema_name"));identifier(Jcs.string(b,"runtime_role"));
    if(PortalReadModels.number(b.get("database_oid"))<1||PortalReadModels.number(b.get("schema_oid"))<1||b.get("owner_role").equals(b.get("runtime_role")))throw Rejected.invalid();return b;
  }
  static Map<String,Object> qualification(Object value) {
    var q=Jcs.object(value);Jcs.keys(q,"schema","definition","native_code_digest","source_freeze_contract_digest","cutover_ref","review_receipt_ref","runtime_qualification_ref","valid_until");
    if(!"human-auth-installation-qualification.v1".equals(q.get("schema")))throw Rejected.invalid();
    AuthModels.validate("definition",q.get("definition"));
    for(String key:List.of("native_code_digest","source_freeze_contract_digest"))Jcs.hash(q,key);
    for(String key:List.of("cutover_ref","review_receipt_ref","runtime_qualification_ref"))Jcs.ref(q,key);
    PortalReadModels.time(q.get("valid_until"));return q;
  }
  private static List<Map<String,Object>> rows(Connection connection,String sql,Object...args) {
    return ConsumerEdgeInstallation.rows(connection,sql,args);
  }
  private static Map<String,Object> one(Connection connection,String sql,Object...args) {
    var rows=rows(connection,sql,args);if(rows.size()!=1)throw Rejected.denied();return rows.get(0);
  }
  private static void write(Connection connection,String sql,Object...args) {
    ConsumerEdgeInstallation.write(connection,sql,args);
  }
  private static void session(Connection connection,Map<String,Object> binding,boolean owner) {
    // Reuse the qualified same-database/TLS/no-role-switch/no-superuser boundary, not its PHI schema.
    ConsumerEdgeInstallation.session(connection,binding,owner);
  }
  private static void lock(Connection connection,String tenant) {
    one(connection,"SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_=? FOR UPDATE",tenant);
  }
  static void tables(Connection connection,Map<String,Object> binding) {
    String schema=Jcs.string(binding,"schema_name"),runtime=Jcs.string(binding,"runtime_role");
    for(String suffix:TABLES) {
      String name="mzo_auth_"+suffix.toLowerCase(Locale.ROOT);
      var row=one(connection,"SELECT c.oid,pg_get_userbyid(c.relowner) AS owner,c.relkind,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=? AND c.relname=?",schema,name);
      if(!binding.get("owner_role").equals(row.get("owner"))||!"r".equals(row.get("relkind"))||!Boolean.FALSE.equals(row.get("relrowsecurity")))throw Rejected.denied();
      for(String privilege:List.of("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")) {
        boolean expected=privilege.equals("SELECT")||privilege.equals("INSERT")&&INSERTS.contains(suffix)||privilege.equals("UPDATE")&&UPDATES.contains(suffix);
        boolean actual=Boolean.TRUE.equals(one(connection,"SELECT has_table_privilege(?,?,?) AS allowed",runtime,schema+"."+name,privilege).get("allowed"));
        if(actual!=expected)throw Rejected.denied();
        if(Set.of("SELECT","INSERT","UPDATE","REFERENCES").contains(privilege)
            &&Boolean.TRUE.equals(one(connection,"SELECT has_any_column_privilege(?,?,?) AS allowed",runtime,schema+"."+name,privilege).get("allowed"))!=expected)throw Rejected.denied();
      }
      if(!rows(connection,"SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE c.oid=? AND a.grantee=0",row.get("oid")).isEmpty())throw Rejected.denied();
    }
  }
  /** Source/owner approvals are inputs from their real review process, not created by this method. */
  public static void installSchema(Connection owner,Map<String,Object> database,Map<String,Object> scope,Map<String,Object> approvedQualification) {
    var b=binding(database);AuthModels.validate("scope",scope);var q=qualification(approvedQualification);
    session(owner,b,true);String tenant=Jcs.ref(scope,"tenant");lock(owner,tenant);
    if(PortalReadModels.number(scope.get("installation_revision"))!=1)throw Rejected.invalid();
    for(String suffix:TABLES)if(!Boolean.TRUE.equals(one(owner,"SELECT to_regclass(?) IS NULL AS absent",b.get("schema_name")+".mzo_auth_"+suffix.toLowerCase(Locale.ROOT)).get("absent")))throw Rejected.conflict();
    try(var resource=AuthInstallation.class.getResourceAsStream("/human-auth-intake-documents-postgres.sql");var statement=owner.createStatement()) {
      if(resource==null)throw EngineStore.unavailable();statement.setQueryTimeout(5);statement.execute(new String(resource.readAllBytes(),StandardCharsets.UTF_8));
      String role=identifier(Jcs.string(b,"runtime_role"));
      for(String suffix:TABLES) {
        String table=identifier(Jcs.string(b,"schema_name"))+"."+identifier("mzo_auth_"+suffix.toLowerCase(Locale.ROOT));
        statement.execute("REVOKE ALL ON TABLE "+table+" FROM PUBLIC,"+role);
        statement.execute("GRANT SELECT"+(INSERTS.contains(suffix)?",INSERT":"")+(UPDATES.contains(suffix)?",UPDATE":"")+" ON TABLE "+table+" TO "+role);
      }
    } catch(IOException|SQLException failure){throw EngineStore.unavailable();}
    write(owner,"INSERT INTO MZO_AUTH_INSTALLATION(TENANT_,INCARNATION_,REV_,SCOPE_,BINDING_,QUALIFICATION_) VALUES(?,?,1,?,?,?)",tenant,Jcs.ref(scope,"database_incarnation"),AuthStore.text(scope),AuthStore.text(b),AuthStore.text(q));tables(owner,b);
  }
  public static void designate(Connection owner,String tenant,Map<String,Object> designation,long expectedRevision) {
    lock(owner,tenant);var row=one(owner,"SELECT * FROM MZO_AUTH_INSTALLATION WHERE TENANT_=?",tenant);var b=binding(AuthStore.parse(row.get("binding_")));session(owner,b,true);tables(owner,b);
    if(((Number)row.get("rev_")).longValue()!=expectedRevision)throw Rejected.conflict();
    var d=AuthTrust.designation(designation);
    write(owner,"INSERT INTO MZO_AUTH_TRUST(TENANT_,KEY_ID_,DESIGNATION_) VALUES(?,?,?)",tenant,Jcs.ref(d,"key_id"),AuthStore.text(d));
  }
  public static void revoke(Connection owner,String tenant,String keyId,long expectedRevision) {
    lock(owner,tenant);var row=one(owner,"SELECT * FROM MZO_AUTH_INSTALLATION WHERE TENANT_=?",tenant);var b=binding(AuthStore.parse(row.get("binding_")));session(owner,b,true);tables(owner,b);
    if(((Number)row.get("rev_")).longValue()!=expectedRevision)throw Rejected.conflict();
    write(owner,"INSERT INTO MZO_AUTH_REVOKED_KEY(TENANT_,KEY_ID_,REV_) VALUES(?,?,?)",tenant,keyId,expectedRevision);
  }
  static Map<String,Object> runtime(AuthStore store) {
    var row=store.one("SELECT * FROM MZO_AUTH_INSTALLATION WHERE TENANT_=?",store.tenant);
    var b=binding(AuthStore.parse(row.get("binding_")));
    // Same enlisted connection, explicit role/database/schema/TLS readback. No second DataSource.
    session(store.connection(),b,false);tables(store.connection(),b);
    return qualification(AuthStore.parse(row.get("qualification_")));
  }
}

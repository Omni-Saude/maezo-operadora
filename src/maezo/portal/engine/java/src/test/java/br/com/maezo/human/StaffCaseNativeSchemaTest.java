package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import org.junit.jupiter.api.Test;

/** ADR-0060 D3-D6 (T1.8): the staff native schema is a pin, never a constant.
 * Finite controls only; the catalog behaviour (pg_temp homonym, schema homonyms, real
 * privileges) is proven against PostgreSQL by StaffCaseNativeSchemaEngineIT.
 */
class StaffCaseNativeSchemaTest {
  static final String SCHEMA="maezo_native";
  static final StaffCaseStore.RelationPin PIN=new StaffCaseStore.RelationPin(4242,"maezo_native_schema_owner");
  final KeyPair root,result;
  StaffCaseNativeSchemaTest() throws GeneralSecurityException {
    var g=KeyPairGenerator.getInstance("Ed25519");root=g.generateKeyPair();result=g.generateKeyPair();
  }
  StaffCaseInstallation.Configuration config(String nativeSchema){
    var scope=record("tenant","tenant","environment","test","engine_name","engine","database_incarnation","inc",
      "installation_ref","installation","installation_revision","1");
    var pins=new TreeMap<String,StaffCaseStore.RelationPin>();for(String name:StaffCaseStore.OWNED)pins.put(name,PIN);
    return new StaffCaseInstallation.Configuration(scope,"a".repeat(64),root.getPublic(),result.getPrivate(),result.getPublic(),
      "cibseven_app",nativeSchema,pins,5);
  }
  static Map<String,Object> resolution(Object current,Object resolved){
    var row=new HashMap<String,Object>();row.put("current_schema",current);row.put("resolved",resolved);return row;
  }
  static Map<String,Object> writes(boolean ins,boolean upd,boolean del,boolean trunc){
    return record("ins",ins,"upd",upd,"del",del,"trunc",trunc);
  }

  @Test void nativeSchemaIsAClosedLowercasePostgresIdentifier(){
    assertEquals(SCHEMA,config(SCHEMA).nativeSchema());
    assertEquals("_x1",config("_x1").nativeSchema());
    assertEquals("a".repeat(63),config("a".repeat(63)).nativeSchema());
    for(String bad:Arrays.asList(null,"","Maezo_native","maezo-native","1maezo","a".repeat(64),"maezo_native;drop",
        "\"maezo_native\"","maezo native","maezo.native","maezo_native\n","maezo_natïve"))
      assertThrows(Rejected.class,()->config(bad),String.valueOf(bad));
  }
  @Test void sharedAndSystemSchemasAreNeverTheNativeSchema(){
    for(String reserved:List.of("public","cibseven","information_schema","pg_catalog","pg_temp","pg_toast","pg_temp_3","pg_"))
      assertThrows(Rejected.class,()->config(reserved),reserved);
    // Exact denial, not a prefix ban: names that merely contain them stay valid identifiers.
    for(String allowed:List.of("public_native","cibseven_native","pgnative","maezo_pg_x"))
      assertEquals(allowed,config(allowed).nativeSchema());
  }
  @Test void configurationDigestBindsTheNativeSchema(){
    assertEquals(config(SCHEMA).digest(),config(SCHEMA).digest());
    // Exact comparison (D5): a native-v2 name that merely shares the prefix is another pin.
    assertNotEquals(config(SCHEMA).digest(),config("maezo_native_v2").digest());
    assertNotEquals(config(SCHEMA).digest(),config("maezo_native_owner_v1").digest());
  }
  @Test void pinQueryBindsTheSchemaAndResolvesTheUnqualifiedName(){
    String sql=StaffCaseStore.PIN;
    assertTrue(sql.contains("n.nspname=?"),sql);
    assertFalse(sql.contains("'public'"),sql);
    assertTrue(sql.contains("current_schema()"),sql);
    assertTrue(sql.contains("to_regclass(CAST(? AS text))"),sql);
    // Positional order: to_regclass(name) in the select list, then nspname, then relname.
    assertTrue(sql.indexOf("to_regclass")<sql.indexOf("nspname=?")&&sql.indexOf("nspname=?")<sql.indexOf("relname=?"),sql);
  }
  @Test void resolutionMustLandExactlyOnThePinnedRelation(){
    StaffCaseStore.requireResolved(resolution(SCHEMA,4242L),SCHEMA,PIN);
    StaffCaseStore.requireResolved(resolution(SCHEMA,4242),SCHEMA,PIN);
    // pg_temp (or an earlier schema) homonym: the unqualified name resolves elsewhere.
    assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(resolution(SCHEMA,9999L),SCHEMA,PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(resolution(SCHEMA,null),SCHEMA,PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(resolution(SCHEMA,"4242"),SCHEMA,PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(resolution("public",4242L),SCHEMA,PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(resolution("maezo_native_v2",4242L),SCHEMA,PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireResolved(resolution(null,4242L),SCHEMA,PIN));
  }
  static Map<String,Object> namespace(Object owner,Object runtimeCreate,Object publicCreate){
    var row=new HashMap<String,Object>();row.put("schema_owner",owner);row.put("runtime_create",runtimeCreate);
    row.put("public_create",publicCreate);return row;
  }
  @Test void namespaceMustBeOwnedByThePinnedOwnerAndClosedToCreate(){
    StaffCaseStore.requireNamespace(namespace(PIN.owner(),false,false),PIN);
    assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(namespace("cibseven_app",false,false),PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(namespace(null,false,false),PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(namespace(PIN.owner(),true,false),PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(namespace(PIN.owner(),false,true),PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(namespace(PIN.owner(),null,false),PIN));
    assertThrows(Rejected.class,()->StaffCaseStore.requireNamespace(namespace(PIN.owner(),false,null),PIN));
    String sql=StaffCaseStore.PIN;
    assertTrue(sql.contains("pg_get_userbyid(n.nspowner) AS schema_owner"),sql);
    assertTrue(sql.contains("has_schema_privilege(session_user,n.oid,'CREATE') AS runtime_create"),sql);
    assertTrue(sql.contains("a.grantee=0 AND a.privilege_type='CREATE'"),sql);
  }
  @Test void writeMatrixRefusesUpdateOnImmutableRelations(){
    StaffCaseStore.requireWrites(true,false,writes(true,false,false,false));
    StaffCaseStore.requireWrites(false,false,writes(true,true,false,false));
    StaffCaseStore.requireWrites(false,true,writes(false,false,false,false));
    assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(true,false,writes(true,true,false,false)));
    assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(false,false,writes(true,false,false,false)));
    assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(true,false,writes(true,false,true,false)));
    assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(true,false,writes(true,false,false,true)));
    assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(false,true,writes(true,false,false,false)));
    assertThrows(Rejected.class,()->StaffCaseStore.requireWrites(true,false,writes(false,false,false,false)));
  }
  @Test void checkpointChunkIsClassifiedImmutable() throws Exception {
    // D6: chunks are only INSERTed and read by chunk_index, so the DDL keeps SELECT,INSERT
    // and the pin must accept exactly that. The rule lives in the constructor; read it.
    String java=Files.readString(Path.of("src/main/java/br/com/maezo/human/StaffCaseStore.java"),StandardCharsets.UTF_8);
    var rule=java.lines().filter(l->l.strip().startsWith("boolean immutable=")).toList();
    assertEquals(1,rule.size(),"StaffCaseStore immutable rule moved");
    assertTrue(rule.get(0).contains("table.endsWith(\"_chunk\")"),rule.get(0));
    assertTrue(StaffCaseStore.OWNED.contains("mzo_staff_case_checkpoint_chunk"));
  }
}

package br.com.maezo.human;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.sql.*;
import java.util.*;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.w3c.dom.*;

/** Concrete owner SQL installer/readback; no runtime source/key/consumer qualification defaults. */
public final class ConsumerEdgeInstallation {
  private ConsumerEdgeInstallation() {}
  static final String CONTRACT="c739693c1199cd68f8da95a2e39e0e8e192b7713ce22efbdf7de32d813f31368";
  static final String CONTRACT_V2="fd6a9190d53aa2689bc4e7e04d05350d4ee2562955316adf6675008382d69048";
  static final String PURPOSE="native-consumer-edge-installation";
  static final String PREFIX="MZO_HUMAN_CONSUMER_";
  static final List<String> TABLES=List.of("DATABASE","TRUST","REVOKED","QUALIFICATION","HEAD","POINTER","POINTER_HEAD","LINK");
  static final Set<String> INSERTS=Set.of("POINTER","POINTER_HEAD","LINK");
  static void need(boolean v){ConsumerLineage.require(v);}
  static String str(Map<String,Object> m,String k){return ConsumerLineage.ref(m,k);}
  static long num(Map<String,Object> m,String k,boolean p){return ConsumerLineage.decimal(m,k,p);}
  static Map<String,Object> parse(String s){return ConsumerLineage.object(s.getBytes(StandardCharsets.UTF_8));}
  static String text(Object o){return ConsumerLineage.text(Jcs.canonical(o));}
  static String identifier(String v){need(v.matches("[a-z_][a-z0-9_]{0,62}"));return "\""+v+"\"";}

  /** Exact owner-installed database metadata. It must match the actual session/catalog at every use. */
  static Map<String,Object> database(Map<String,Object> b){
    Jcs.keys(b,"schema","scope","database_name","database_oid","schema_name","schema_oid","owner_role","runtime_role");
    need("phi-consumer-native-database.v1".equals(b.get("schema")));
    ConsumerLineage.scope(Jcs.object(b.get("scope")));
    for(String k:List.of("database_name","owner_role","runtime_role"))str(b,k);
    identifier(str(b,"schema_name"));num(b,"database_oid",true);num(b,"schema_oid",true);
    need(!b.get("owner_role").equals(b.get("runtime_role")));
    return parse(text(b));
  }
  static List<Map<String,Object>> rows(Connection c,String sql,Object... args){
    try(var p=c.prepareStatement(sql)){
      p.setQueryTimeout(5);for(int i=0;i<args.length;i++)p.setObject(i+1,args[i]);
      try(var rs=p.executeQuery()){
        List<Map<String,Object>> found=new ArrayList<>();
        while(rs.next()){
          need(found.size()<1024);Map<String,Object> row=new HashMap<>();
          for(int i=1;i<=rs.getMetaData().getColumnCount();i++)row.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),rs.getObject(i));
          found.add(row);
        }return found;
      }
    }catch(SQLException e){throw EngineStore.unavailable();}
  }
  static Map<String,Object> one(Connection c,String sql,Object...args){var r=rows(c,sql,args);need(r.size()==1);return r.get(0);}
  static void write(Connection c,String sql,Object...args){
    try(var p=c.prepareStatement(sql)){p.setQueryTimeout(5);for(int i=0;i<args.length;i++)p.setObject(i+1,args[i]);need(p.executeUpdate()==1);}
    catch(SQLException e){throw EngineStore.unavailable();}
  }
  static long clock(Connection c){return ((Number)one(c,"SELECT floor(extract(epoch from clock_timestamp())*1000)::bigint AS now").get("now")).longValue();}
  static void session(Connection c,Map<String,Object> b,boolean owner){
    try {need(!c.getAutoCommit() && "PostgreSQL".equals(c.getMetaData().getDatabaseProductName()));}
    catch(SQLException e){throw EngineStore.unavailable();}
    var actual=one(c,"SELECT current_database() AS db,d.oid::bigint AS db_oid,current_schema() AS schema,n.oid::bigint AS schema_oid,pg_get_userbyid(n.nspowner) AS owner,session_user AS login,current_user AS current,pg_my_temp_schema() AS temp FROM pg_database d JOIN pg_namespace n ON n.nspname=current_schema() WHERE d.datname=current_database()");
    need(b.get("database_name").equals(actual.get("db")) && b.get("schema_name").equals(actual.get("schema"))
        && num(b,"database_oid",true)==((Number)actual.get("db_oid")).longValue()
        && num(b,"schema_oid",true)==((Number)actual.get("schema_oid")).longValue()
        && b.get("owner_role").equals(actual.get("owner")) && actual.get("login").equals(actual.get("current"))
        && b.get(owner?"owner_role":"runtime_role").equals(actual.get("login")) && ((Number)actual.get("temp")).longValue()==0);
    var tls=one(c,"SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()");need(Boolean.TRUE.equals(tls.get("ssl")));
    var role=one(c,"SELECT oid,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname=?",b.get("runtime_role"));
    for(String k:List.of("rolsuper","rolcreatedb","rolcreaterole","rolbypassrls"))need(Boolean.FALSE.equals(role.get(k)));
    need(Boolean.TRUE.equals(role.get("rolcanlogin")) && rows(c,"SELECT roleid FROM pg_auth_members WHERE member=?",role.get("oid")).isEmpty());
    need(Boolean.FALSE.equals(one(c,"SELECT has_schema_privilege(?,?,'CREATE') AS allowed",b.get("runtime_role"),b.get("schema_name")).get("allowed")));
  }
  static void tables(Connection c,Map<String,Object> b){
    String role=str(b,"runtime_role");
    var original=one(c,"SELECT c.oid::bigint AS oid,pg_get_userbyid(c.relowner) AS owner,c.relkind,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=? AND c.relname='mzo_human_decision_binding'",b.get("schema_name"));
    need(b.get("owner_role").equals(original.get("owner")) && "r".equals(original.get("relkind")) && Boolean.FALSE.equals(original.get("relrowsecurity")));
    for(String privilege:List.of("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")){
      boolean expected=privilege.equals("SELECT");
      need(Boolean.TRUE.equals(one(c,"SELECT has_table_privilege(?,?::oid,?) AS allowed",role,original.get("oid"),privilege).get("allowed"))==expected);
      if(Set.of("SELECT","INSERT","UPDATE","REFERENCES").contains(privilege))need(Boolean.TRUE.equals(one(c,"SELECT has_any_column_privilege(?,?::oid,?) AS allowed",role,original.get("oid"),privilege).get("allowed"))==expected);
    }
    for(String suffix:TABLES){
      String name=(PREFIX+suffix).toLowerCase(Locale.ROOT);
      var t=one(c,"SELECT c.oid::bigint AS oid,pg_get_userbyid(c.relowner) AS owner,c.relkind,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=? AND c.relname=?",b.get("schema_name"),name);
      need(b.get("owner_role").equals(t.get("owner")) && "r".equals(t.get("relkind")) && Boolean.FALSE.equals(t.get("relrowsecurity")));
      if(!Set.of("HEAD","POINTER_HEAD").contains(suffix)){
        var trigger=one(c,"SELECT t.tgenabled,t.tgtype,p.prosrc,p.prosecdef FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace WHERE t.tgrelid=?::oid AND NOT t.tgisinternal AND p.proname='mzo_human_consumer_immutable' AND n.nspname=?",t.get("oid"),b.get("schema_name"));
        need("O".equals(trigger.get("tgenabled")) && ((Number)trigger.get("tgtype")).intValue()==58 && Boolean.FALSE.equals(trigger.get("prosecdef")) && ((String)trigger.get("prosrc")).trim().equals("BEGIN RAISE EXCEPTION 'immutable consumer evidence'; END"));
      }
      for(String privilege:List.of("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")){
        boolean expected="SELECT".equals(privilege) || ("INSERT".equals(privilege)&&INSERTS.contains(suffix)) || ("UPDATE".equals(privilege)&&"POINTER_HEAD".equals(suffix));
        boolean table=Boolean.TRUE.equals(one(c,"SELECT has_table_privilege(?,?::oid,?) AS allowed",role,t.get("oid"),privilege).get("allowed"));
        need(table==expected);
        if(Set.of("SELECT","INSERT","UPDATE","REFERENCES").contains(privilege))
          need(Boolean.TRUE.equals(one(c,"SELECT has_any_column_privilege(?,?::oid,?) AS allowed",role,t.get("oid"),privilege).get("allowed"))==expected);
      }
    }
  }
  static long lock(Connection c,String tenant){return ((Number)one(c,"SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_=? FOR UPDATE",tenant).get("rev_")).longValue();}
  static Map<String,Object> binding(Connection c,String tenant){return database(parse((String)one(c,"SELECT BINDING_ FROM MZO_HUMAN_CONSUMER_DATABASE WHERE TENANT_=?",tenant).get("binding_")));}

  /** Owner-only explicit migration. Caller transaction must commit before any runtime/readback use. */
  public static void installSchema(Connection owner,Map<String,Object> input){
    var b=database(input);session(owner,b,true);String tenant=str(Jcs.object(b.get("scope")),"tenant");lock(owner,tenant);
    var names=new ArrayList<String>();for(String name:TABLES)names.add((PREFIX+name).toLowerCase(Locale.ROOT));
    // D-J.4: the owner script may have installed these tables; accepted only if identical to the pin.
    if(NativeCatalogPin.absent(owner,NativeCatalogPin.CONSUMER,str(b,"schema_name"),names,str(b,"owner_role"),str(b,"runtime_role")))
    try(var in=ConsumerEdgeInstallation.class.getResourceAsStream("/human-consumer-lineage-postgres.sql");var statement=owner.createStatement()){
      need(in!=null);statement.setQueryTimeout(5);statement.execute(new String(in.readAllBytes(),StandardCharsets.UTF_8));
      String role=identifier(str(b,"runtime_role"));
      for(String name:TABLES){
        String table=identifier(str(b,"schema_name"))+"."+identifier((PREFIX+name).toLowerCase(Locale.ROOT));
        statement.execute("REVOKE ALL ON TABLE "+table+" FROM PUBLIC,"+role);
        statement.execute("GRANT SELECT"+(INSERTS.contains(name)?",INSERT":"")+("POINTER_HEAD".equals(name)?",UPDATE":"")+" ON TABLE "+table+" TO "+role);
      }
    }catch(IOException|SQLException e){throw EngineStore.unavailable();}
    write(owner,"INSERT INTO MZO_HUMAN_CONSUMER_DATABASE(TENANT_,BINDING_) VALUES(?,?)",tenant,text(b));
    write(owner,"INSERT INTO MZO_HUMAN_CONSUMER_HEAD(TENANT_,GENERATION_,QUALIFICATION_GENERATION_) VALUES(?,0,NULL)",tenant);
    tables(owner,b);
  }

  static Map<String,Object> designation(Map<String,Object> d){
    Jcs.keys(d,"schema","issuer","key_id","public_key","purpose","authority_ref","contract_digest","source_freeze_contract_digest","valid_from_ms","valid_until_ms");
    need("phi-consumer-edge-trust.v1".equals(d.get("schema")) && PURPOSE.equals(d.get("purpose")) && Set.of(CONTRACT,CONTRACT_V2).contains(d.get("contract_digest")));
    for(String k:List.of("issuer","key_id","authority_ref"))str(d,k);
    Jcs.hash(d,"source_freeze_contract_digest");decode(Jcs.string(d,"public_key"),32);
    need(num(d,"valid_until_ms",true)>num(d,"valid_from_ms",false));return parse(text(d));
  }
  /** Root designation itself is an external owner act, not self-signed request admission. */
  public static void designate(Connection owner,String tenant,Map<String,Object> input,long expectedGeneration){
    var b=binding(owner,tenant);session(owner,b,true);tables(owner,b);lock(owner,tenant);
    var d=designation(input);long next=advance(owner,tenant,expectedGeneration,null);
    write(owner,"INSERT INTO MZO_HUMAN_CONSUMER_TRUST(TENANT_,KEY_ID_,DESIGNATION_) VALUES(?,?,?)",tenant,d.get("key_id"),text(d));
    need(next>expectedGeneration);
  }
  public static void revoke(Connection owner,String tenant,String keyId,long expectedGeneration){
    var b=binding(owner,tenant);session(owner,b,true);tables(owner,b);lock(owner,tenant);
    long next=advance(owner,tenant,expectedGeneration,null);
    write(owner,"INSERT INTO MZO_HUMAN_CONSUMER_REVOKED(TENANT_,KEY_ID_,GENERATION_) VALUES(?,?,?)",tenant,keyId,next);
  }
  static long advance(Connection c,String tenant,long previous,Long qualification){
    need(previous>=0 && previous<Long.MAX_VALUE);long next=previous+1;
    write(c,"UPDATE MZO_HUMAN_CONSUMER_HEAD SET GENERATION_=?,QUALIFICATION_GENERATION_=? WHERE TENANT_=? AND GENERATION_=?",next,qualification,tenant,previous);return next;
  }
  static byte[] decode(String value,int size){
    try {need(!value.contains("="));byte[] raw=Base64.getUrlDecoder().decode(value);need(raw.length==size && Base64.getUrlEncoder().withoutPadding().encodeToString(raw).equals(value));return raw;}
    catch(IllegalArgumentException e){throw EngineStore.unavailable();}
  }
  static Map<String,Object> verify(Connection c,String tenant,byte[] raw,long now){
    var envelope=ConsumerLineage.object(raw);
    Jcs.keys(envelope,"schema","issuer","key_id","purpose","authority_ref","contract_digest","body","signature");
    need("phi-consumer-edge-qualification-envelope.v1".equals(envelope.get("schema")));
    var d=designation(parse((String)one(c,"SELECT DESIGNATION_ FROM MZO_HUMAN_CONSUMER_TRUST WHERE TENANT_=? AND KEY_ID_=?",tenant,str(envelope,"key_id")).get("designation_")));
    need(rows(c,"SELECT KEY_ID_ FROM MZO_HUMAN_CONSUMER_REVOKED WHERE TENANT_=? AND KEY_ID_=?",tenant,d.get("key_id")).isEmpty());
    return signed(tenant,raw,d,now);
  }
  /** Pure cryptographic/shape verification; production obtains designation only from qualified owner SQL. */
  static Map<String,Object> signed(String tenant,byte[] raw,Map<String,Object> designation,long now){
    var envelope=ConsumerLineage.object(raw);
    Jcs.keys(envelope,"schema","issuer","key_id","purpose","authority_ref","contract_digest","body","signature");
    need("phi-consumer-edge-qualification-envelope.v1".equals(envelope.get("schema")));
    var d=designation(designation);
    for(String k:List.of("issuer","key_id","purpose","authority_ref","contract_digest"))need(d.get(k).equals(envelope.get(k)));
    need(num(d,"valid_from_ms",false)<=now && now<num(d,"valid_until_ms",true));
    var unsigned=new TreeMap<>(envelope);unsigned.remove("signature");
    try {
      byte[] pk=decode(Jcs.string(d,"public_key"),32),encoded=new byte[44];
      byte[] prefix=HexFormat.of().parseHex("302a300506032b6570032100");System.arraycopy(prefix,0,encoded,0,12);System.arraycopy(pk,0,encoded,12,32);
      var verifier=Signature.getInstance("Ed25519");verifier.initVerify(KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(encoded)));
      verifier.update(Jcs.canonical(unsigned));need(verifier.verify(decode(Jcs.string(envelope,"signature"),64)));
    }catch(GeneralSecurityException e){throw EngineStore.unavailable();}
    var body=Jcs.object(envelope.get("body"));
    boolean v2="phi-consumer-edge-qualification.v2".equals(body.get("schema"));
    if(v2)Jcs.keys(body,"schema","scope","qualification_ref","expected_generation","expected_authority_revision","native_build_digest","phi_build_digest","source_freeze","targets","valid_from_ms","valid_until_ms","cohort","cohort_digest");
    else Jcs.keys(body,"schema","scope","qualification_ref","expected_generation","expected_authority_revision","native_build_digest","phi_build_digest","source_freeze","targets","valid_from_ms","valid_until_ms");
    need((v2?CONTRACT_V2:CONTRACT).equals(d.get("contract_digest")));
    need((v2 || "phi-consumer-edge-qualification.v1".equals(body.get("schema"))) && tenant.equals(ConsumerLineage.scope(Jcs.object(body.get("scope"))).get("tenant")));
    str(body,"qualification_ref");num(body,"expected_generation",false);num(body,"expected_authority_revision",false);
    Jcs.hash(body,"native_build_digest");Jcs.hash(body,"phi_build_digest");
    need(num(body,"valid_from_ms",false)<=now && now<num(body,"valid_until_ms",true) && num(body,"valid_until_ms",true)<=num(d,"valid_until_ms",true));
    var freeze=Jcs.object(body.get("source_freeze"));
    Jcs.keys(freeze,"issuer_contract_digest","authority_ref","source_commit","source_tree","native_build_digest","phi_build_digest","source_artifacts_digest");
    need(d.get("source_freeze_contract_digest").equals(freeze.get("issuer_contract_digest")) && d.get("authority_ref").equals(freeze.get("authority_ref")));
    for(String k:List.of("source_commit","source_tree"))need(Jcs.string(freeze,k).matches("[a-f0-9]{40}"));
    Jcs.hash(freeze,"source_artifacts_digest");
    for(String k:List.of("native_build_digest","phi_build_digest"))need(body.get(k).equals(freeze.get(k)));
    targets(body);return parse(text(body));
  }

  static List<Map<String,Object>> targets(Map<String,Object> body){
    boolean v2="phi-consumer-edge-qualification.v2".equals(body.get("schema"));
    Map<String,Map<String,Object>> members=v2?cohort(body):Map.of();
    Object value=body.get("targets");need(value instanceof List<?>);List<?> values=(List<?>)value;
    need(v2?!values.isEmpty() && values.size()<=43:values.size()==6);
    List<Map<String,Object>> targets=new ArrayList<>();String previous="";Set<String> keys=new HashSet<>();
    for(Object item:values){
      var t=Jcs.object(item);
      if(v2)Jcs.keys(t,"process_definition_id","process_key","task_key","binding_digest","consumer_digest","process_digest","edges","material_digest");
      else Jcs.keys(t,"process_definition_id","process_key","task_key","binding_digest","consumer_digest","process_digest","edges");
      String key=str(t,"process_key")+"/"+str(t,"task_key");need((v2?ConsumerLineage.SOURCES:ConsumerLineage.LEGACY_SOURCES).containsKey(key)&&key.compareTo(previous)>0&&keys.add(key));previous=key;
      if(v2){var member=members.get(key);need(member!=null && member.get("process_definition_id").equals(t.get("process_definition_id")) && member.get("material_digest").equals(Jcs.hash(t,"material_digest")));}
      str(t,"process_definition_id");for(String k:List.of("binding_digest","consumer_digest","process_digest"))Jcs.hash(t,k);
      need(t.get("edges") instanceof List<?>);List<?> edges=(List<?>)t.get("edges");
      String kind=ConsumerLineage.SOURCES.get(key);Set<String> expected=switch(kind){case "auth_decisao"->Set.of("NEGAR","JUNTA_MEDICA");case "auth_junta"->Set.of("NEGAR");case "pagto_admissibilidade"->Set.of("DEVOLVER");default->Set.of();};
      Set<String> observed=new HashSet<>();String before="";
      for(Object e:edges){var edge=Jcs.object(e);Jcs.keys(edge,"outcome","activity_id","topic","consumer_kind");
        String outcome=str(edge,"outcome");String order=outcome+"/"+str(edge,"activity_id");need(order.compareTo(before)>0&&observed.add(outcome));before=order;
        String consumer=outcome.equals("NEGAR")?"auth_denial_record":outcome.equals("JUNTA_MEDICA")?"auth_junta_forward":outcome.equals("DEVOLVER")?"pagto_admissibility_return":"";
        String topic=switch(consumer){case "auth_denial_record"->"operadora.auth.send_denial_notice";case "auth_junta_forward"->"operadora.auth.convene_junta";case "pagto_admissibility_return"->"operadora.pagto.register_payment_refusal";default->"";};
        String activity=switch(consumer){case "auth_denial_record"->"ST_EnviarNegativaFormal";case "auth_junta_forward"->"ST_ConvocarJunta";case "pagto_admissibility_return"->"ST_RegisterPaymentRefusal";default->"";};
        need(consumer.equals(edge.get("consumer_kind")) && topic.equals(edge.get("topic")) && activity.equals(edge.get("activity_id")));
      }need(observed.equals(expected));targets.add(parse(text(t)));
    }need(keys.equals(v2?members.keySet():ConsumerLineage.LEGACY_SOURCES.keySet()));return List.copyOf(targets);
  }

  /** Material digests precede packet signatures/binding digests: no hash cycle. */
  static Map<String,Map<String,Object>> cohort(Map<String,Object> body){
    var manifest=Jcs.object(body.get("cohort"));Jcs.keys(manifest,"schema","scope","members");
    need("human-decision-cohort.v2".equals(manifest.get("schema")) && ConsumerLineage.scope(Jcs.object(manifest.get("scope"))).equals(body.get("scope")));
    need(Jcs.digest(Jcs.canonical(Map.of("schema","human-decision-cohort-hash.v2","value",manifest))).equals(Jcs.hash(body,"cohort_digest")));
    Object value=manifest.get("members");need(value instanceof List<?>);var members=(List<?>)value;need(!members.isEmpty() && members.size()<=43);
    Map<String,Map<String,Object>> result=new TreeMap<>();String previous="";
    for(Object item:members){var m=Jcs.object(item);Jcs.keys(m,"process_definition_id","process_definition_key","task_definition_key","material_digest");
      String key=str(m,"process_definition_key")+"/"+str(m,"task_definition_key");str(m,"process_definition_id");Jcs.hash(m,"material_digest");
      need(ConsumerLineage.SOURCES.containsKey(key) && key.compareTo(previous)>0 && result.put(key,m)==null);previous=key;
    }return Collections.unmodifiableMap(result);
  }

  public static final class Installed {
    private final Map<String,Object> scope,body;private final long generation,authorityRevision;private final byte[] envelope;
    private Installed(Map<String,Object> scope,long generation,long authorityRevision,Map<String,Object> body,byte[] envelope){
      this.scope=ConsumerLineage.scope(scope);this.generation=generation;this.authorityRevision=authorityRevision;
      this.body=parse(text(body));this.envelope=envelope.clone();
    }
    public Map<String,Object> scope(){return parse(text(scope));}
    public Map<String,Object> body(){return parse(text(body));}
    public long generation(){return generation;}
    public long authorityRevision(){return authorityRevision;}
    public byte[] envelope(){return envelope.clone();}
    Map<String,Object> target(String definition,String task){
      var matches=targets(body).stream().filter(t->definition.equals(t.get("process_definition_id"))&&task.equals(t.get("task_key"))).toList();need(matches.size()==1);return matches.get(0);
    }
    Map<String,Object> edge(Map<String,Object> target,String outcome,String activity,String topic){
      List<?> edges=(List<?>)target.get("edges");var matches=edges.stream().map(Jcs::object).filter(e->outcome.equals(e.get("outcome"))&&activity.equals(e.get("activity_id"))&&topic.equals(e.get("topic"))).toList();need(matches.size()==1);return matches.get(0);
    }
    List<String> continuationPath(CommandContext context,Map<String,Object> target,String outcome){
      var matching=((List<?>)target.get("edges")).stream().map(Jcs::object)
          .filter(e->outcome.equals(e.get("outcome"))).toList();
      if(matching.isEmpty())return List.of();
      need(matching.size()==1);
      Connection connection=new EngineStore(context,str(scope,"tenant")).connection;
      var binding=one(connection,"SELECT REQUIRED_GROUP_ FROM MZO_HUMAN_DECISION_BINDING WHERE TENANT_=? AND ENVIRONMENT_=? AND PROCESS_=? AND TASK_KEY_=? AND AUTHORITY_REV_=?",
          scope.get("tenant"),scope.get("environment"),target.get("process_definition_id"),target.get("task_key"),authorityRevision);
      var paths=topology(deploymentBytes(connection,target,str(scope,"tenant")),target,(String)binding.get("required_group_"));
      need(paths.containsKey(outcome));return paths.get(outcome);
    }
    void requireCurrent(CommandContext context){
      var current=ConsumerEdgeInstallation.current(context,str(scope,"tenant"));
      need(generation==current.generation && authorityRevision==current.authorityRevision && scope.equals(current.scope)
          && Arrays.equals(envelope,current.envelope));
    }
  }

  static void nativeBuild(Map<String,Object> body){
    try {
      Path artifact=Path.of(ConsumerLineage.class.getProtectionDomain().getCodeSource().getLocation().toURI());
      need(artifact.isAbsolute() && !Files.isSymbolicLink(artifact) && Files.isRegularFile(artifact) && artifact.getFileName().toString().endsWith(".jar") && Files.size(artifact)<=67108864);
      need(Jcs.digest(Files.readAllBytes(artifact)).equals(body.get("native_build_digest")));
    }catch(Exception e){throw EngineStore.unavailable();}
  }

  /** Full batch readback, including every current native source binding, never a single-row projection. */
  static Installed read(Connection c,String tenant,boolean owner,String engine){
    return read(c,tenant,owner,engine,true);
  }
  private static Installed read(Connection c,String tenant,boolean owner,String engine,boolean lock){
    var b=binding(c,tenant);session(c,b,owner);tables(c,b);var scope=Jcs.object(b.get("scope"));need(engine.equals(scope.get("engine_name")));
    long revision=lock?lock(c,tenant):((Number)one(c,"SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_=?",tenant).get("rev_")).longValue();var head=one(c,"SELECT GENERATION_,QUALIFICATION_GENERATION_ FROM MZO_HUMAN_CONSUMER_HEAD WHERE TENANT_=?",tenant);
    need(head.get("qualification_generation_") instanceof Number && head.get("generation_").equals(head.get("qualification_generation_")));
    long generation=((Number)head.get("generation_")).longValue();
    var row=one(c,"SELECT * FROM MZO_HUMAN_CONSUMER_QUALIFICATION WHERE TENANT_=? AND GENERATION_=?",tenant,generation);
    need(((Number)row.get("authority_rev_")).longValue()==revision);
    byte[] raw=((String)row.get("envelope_")).getBytes(StandardCharsets.UTF_8);var body=verify(c,tenant,raw,clock(c));
    need(scope.equals(body.get("scope")) && num(body,"expected_generation",false)==generation-1 && num(body,"expected_authority_revision",false)==revision && body.get("qualification_ref").equals(row.get("qualification_")));
    nativeBuild(body);bindings(c,body,revision,clock(c),false);
    long monotonic=System.nanoTime(),databaseNow=clock(c);
    long elapsed=Math.max(0,System.nanoTime()-monotonic);
    long conservativeNow=Math.addExact(databaseNow,Math.addExact(elapsed,999999L)/1000000L);
    need(conservativeNow<num(body,"valid_until_ms",true));
    return new Installed(scope,generation,revision,body,raw);
  }
  static Installed current(CommandContext context,String tenant){
    need(context!=null);return read(context.getDbSqlSession().getSqlSession().getConnection(),tenant,false,context.getProcessEngineConfiguration().getProcessEngineName());
  }
  public static Installed readback(Connection owner,String tenant,String engine,byte[] expectedEnvelope){
    var tx=one(owner,"SELECT current_setting('transaction_read_only') AS readonly,current_setting('transaction_isolation') AS isolation");
    need("on".equals(tx.get("readonly")) && "repeatable read".equals(tx.get("isolation")));
    // Read-only PG transactions cannot certify their own pending installation writes.
    Installed installed=read(owner,tenant,true,engine,false);
    need(Arrays.equals(installed.envelope(),expectedEnvelope));
    return installed;
  }

  /** Installs the complete signed qualification, under the existing tenant lock and separate CAS. */
  public static void install(Connection owner,String tenant,byte[] raw){
    var b=binding(owner,tenant);session(owner,b,true);tables(owner,b);long revision=lock(owner,tenant),now=clock(owner);
    var body=verify(owner,tenant,raw,now);need(b.get("scope").equals(body.get("scope")) && num(body,"expected_authority_revision",false)==revision);
    nativeBuild(body);bindings(owner,body,revision,now,true);long previous=num(body,"expected_generation",false);need(previous<Long.MAX_VALUE);
    var head=one(owner,"SELECT GENERATION_ FROM MZO_HUMAN_CONSUMER_HEAD WHERE TENANT_=?",tenant);
    long observed=((Number)head.get("generation_")).longValue();
    if("phi-consumer-edge-qualification.v2".equals(body.get("schema")) && observed==previous+1){
      Installed prior=read(owner,tenant,true,str(Jcs.object(b.get("scope")),"engine_name"));
      need(Arrays.equals(prior.envelope(),raw));return; // Exact lost-ACK replay, no second generation.
    }
    need(observed==previous);
    long next=previous+1;
    write(owner,"INSERT INTO MZO_HUMAN_CONSUMER_QUALIFICATION(TENANT_,GENERATION_,AUTHORITY_REV_,QUALIFICATION_,ENVELOPE_) VALUES(?,?,?,?,?)",tenant,next,revision,body.get("qualification_ref"),ConsumerLineage.text(raw));
    advance(owner,tenant,previous,next);
    Installed value=read(owner,tenant,true,str(Jcs.object(b.get("scope")),"engine_name"));need(Arrays.equals(value.envelope(),raw));
    // Caller owns commit. Only readback on a distinct committed owner transaction confirms installation.
  }

  static void bindings(Connection c,Map<String,Object> body,long revision,long now,boolean topology){
    var scope=Jcs.object(body.get("scope"));String tenant=str(scope,"tenant");
    var selected=targets(body);
    if("phi-consumer-edge-qualification.v2".equals(body.get("schema"))){
      var actual=rows(c,"SELECT PROCESS_,TASK_KEY_,BINDING_DIGEST_ FROM MZO_HUMAN_DECISION_BINDING WHERE TENANT_=? AND ENVIRONMENT_=? AND AUTHORITY_REV_=? ORDER BY PROCESS_,TASK_KEY_ LIMIT 44",tenant,scope.get("environment"),revision);
      need(actual.size()==selected.size());
      for(var row:actual)need(selected.stream().filter(t->t.get("process_definition_id").equals(row.get("process_")) && t.get("task_key").equals(row.get("task_key_")) && t.get("binding_digest").equals(row.get("binding_digest_"))).count()==1);
    }
    for(var target:selected){
      var b=one(c,"SELECT * FROM MZO_HUMAN_DECISION_BINDING WHERE TENANT_=? AND ENVIRONMENT_=? AND PROCESS_=? AND TASK_KEY_=? AND AUTHORITY_REV_=?",tenant,scope.get("environment"),target.get("process_definition_id"),target.get("task_key"),revision);
      need(Boolean.TRUE.equals(b.get("active_")) && ((Number)b.get("valid_until_")).longValue()>now/1000
          && num(body,"valid_until_ms",true)<=Math.multiplyExact(((Number)b.get("valid_until_")).longValue(),1000));
      for(var pair:Map.of("process_key","process_key_","binding_digest","binding_digest_","consumer_digest","consumer_digest_","process_digest","process_digest_").entrySet())need(target.get(pair.getKey()).equals(b.get(pair.getValue())));
      if(topology)validateDeployment(c,target,tenant,(String)b.get("required_group_"));
    }
  }

  static void validateDeployment(Connection c,Map<String,Object> target,String tenant,String requiredGroup){
    topology(deploymentBytes(c,target,tenant),target,requiredGroup);
  }
  private static byte[] deploymentBytes(Connection c,Map<String,Object> target,String tenant){
    var row=one(c,"SELECT P.KEY_ AS key,P.TENANT_ID_ AS tenant,B.BYTES_ AS bytes FROM ACT_RE_PROCDEF P JOIN ACT_GE_BYTEARRAY B ON B.DEPLOYMENT_ID_=P.DEPLOYMENT_ID_ AND B.NAME_=P.RESOURCE_NAME_ WHERE P.ID_=?",target.get("process_definition_id"));
    need(tenant.equals(row.get("tenant")) && target.get("process_key").equals(row.get("key")));
    byte[] bytes=(byte[])row.get("bytes");need(Jcs.digest(bytes).equals(target.get("process_digest")));return bytes;
  }
  static Map<String,List<String>> topology(byte[] bytes,Map<String,Object> target,String requiredGroup){
    try {
      var factory=DocumentBuilderFactory.newInstance();factory.setNamespaceAware(true);
      factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl",true);factory.setFeature("http://xml.org/sax/features/external-general-entities",false);factory.setFeature("http://xml.org/sax/features/external-parameter-entities",false);
      factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD,"");factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA,"");
      var doc=factory.newDocumentBuilder().parse(new ByteArrayInputStream(bytes));
      String ns="http://www.omg.org/spec/BPMN/20100524/MODEL",camunda="http://camunda.org/schema/1.0/bpmn";
      var processes=doc.getElementsByTagNameNS(ns,"process");need(processes.getLength()==1 && str(target,"process_key").equals(((Element)processes.item(0)).getAttribute("id")));
      Map<String,Element> nodes=new HashMap<>();Map<String,List<Element>> flows=new HashMap<>();var all=doc.getElementsByTagNameNS(ns,"*");
      for(int i=0;i<all.getLength();i++){Element e=(Element)all.item(i);if(e.hasAttribute("id"))need(nodes.put(e.getAttribute("id"),e)==null);if(e.getLocalName().equals("sequenceFlow"))flows.computeIfAbsent(e.getAttribute("sourceRef"),k->new ArrayList<>()).add(e);}
      Element source=nodes.get(str(target,"task_key"));need(source!=null&&source.getLocalName().equals("userTask"));
      String candidates=source.getAttributeNS(camunda,"candidateGroups");
      boolean resolvedEsc="SP-OP-ESCALATION-001".equals(target.get("process_key")) && "UT_TratarEscalonamento".equals(target.get("task_key")) && "${roteamento.grupo_atendimento}".equals(candidates);
      // Only this canonical ESC expression is admitted; the actual native candidate is checked by AtomicHumanCommand.
      need(!requiredGroup.isBlank() && (resolvedEsc || candidates.equals(requiredGroup)));
      Map<String,List<String>> selectedPaths=new HashMap<>();
      for(Object value:(List<?>)target.get("edges")){
        List<String> selected=new ArrayList<>();
        var edge=Jcs.object(value);String cursor=str(target,"task_key"),outcome=str(edge,"outcome");Set<String> seen=new HashSet<>();
        while(!cursor.equals(edge.get("activity_id"))){
          need(seen.add(cursor)&&seen.size()<=32);Element node=nodes.get(cursor);need(node!=null);
          String type=node.getLocalName();need(cursor.equals(target.get("task_key")) || type.equals("exclusiveGateway"));
          List<Element> outgoing=flows.getOrDefault(cursor,List.of());need(!outgoing.isEmpty());List<Element> chosen=new ArrayList<>();Element fallback=null;
          for(Element flow:outgoing){
            if(flow.getAttribute("id").equals(node.getAttribute("default"))){fallback=flow;continue;}
            var expressions=flow.getElementsByTagNameNS(ns,"conditionExpression");
            if(expressions.getLength()==0){need(outgoing.size()==1);chosen.add(flow);}
            else {need(expressions.getLength()==1);String expression=expressions.item(0).getTextContent().trim();
              var pattern=java.util.regex.Pattern.compile("\\$\\{\\s*(decisao_auditor|decisao_admissibilidade)\\s*==\\s*'([^']+)'\\s*}").matcher(expression);need(pattern.matches());
              String field=target.get("process_key").equals("SP-OP-PAGTO-001")?"decisao_admissibilidade":"decisao_auditor";need(pattern.group(1).equals(field));if(pattern.group(2).equals(outcome))chosen.add(flow);
            }
          }if(chosen.isEmpty()&&fallback!=null)chosen.add(fallback);need(chosen.size()==1);
          selected.add(chosen.get(0).getAttribute("id"));cursor=chosen.get(0).getAttribute("targetRef");
        }
        Element activity=nodes.get(cursor);need(activity!=null&&activity.getLocalName().equals("serviceTask")&&"external".equals(activity.getAttributeNS(camunda,"type"))&&edge.get("topic").equals(activity.getAttributeNS(camunda,"topic")));
        need(selectedPaths.put(outcome,List.copyOf(selected))==null);
      }
      return Map.copyOf(selectedPaths);
    }catch(IllegalStateException e){throw e;}catch(Exception e){throw EngineStore.unavailable();}
  }
}

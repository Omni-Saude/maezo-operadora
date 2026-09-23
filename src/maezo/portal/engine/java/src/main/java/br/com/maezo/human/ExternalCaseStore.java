package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static br.com.maezo.human.ExternalCaseModels.*;

import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.function.Consumer;
import java.time.Instant;
import java.util.*;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.mapping.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Fixed scoped reads and enlisted writes on the actual engine PostgreSQL connection. */
final class ExternalCaseStore {
  static final String S="tenant=? AND environment=? AND engine_name=? AND database_incarnation=?";
  final Connection connection;final SqlSession session;final Map<String,Object> scope;final int timeout;final String engineSchema;final String nativeSchema;
  /** Native mzo_* relations the external path reads; resolved in the pinned native schema (ADR-0060 D3). */
  static final List<String> NATIVE_TABLES=List.of("mzo_human_tenant","mzo_portal_read_revocation");
  /** The tenant CAS row, locked; positional parameter: tenant. */
  static String tenantLockSql(String nativeSchema){return "SELECT rev_ FROM \""+StaffCaseStore.schema(nativeSchema)+"\".mzo_human_tenant WHERE tenant_=? FOR UPDATE";}
  /** Revoked read keys of the scope; positional parameters: tenant, environment, engine, incarnation. */
  static String revokedSql(String nativeSchema){return "SELECT fingerprint_ FROM \""+StaffCaseStore.schema(nativeSchema)+"\".mzo_portal_read_revocation WHERE TENANT_=? AND ENVIRONMENT_=? AND ENGINE_=? AND INCARNATION_=? ORDER BY fingerprint_";}
  ExternalCaseStore(CommandContext context,Map<String,Object> scope,int timeout,String engineSchema,String nativeSchema){
    this.nativeSchema=StaffCaseStore.schema(nativeSchema);this.engineSchema=EngineSchema.require(engineSchema,nativeSchema);
    this.session=context.getDbSqlSession().getSqlSession();this.connection=session.getConnection();this.scope=scope;this.timeout=timeout;
    if(timeout<1||timeout>10||!scope.get("engine_name").equals(context.getProcessEngineConfiguration().getProcessEngineName()))throw unavailable();
    String prefix=context.getProcessEngineConfiguration().getDatabaseTablePrefix();
    String schema=context.getProcessEngineConfiguration().getDatabaseSchema();
    EngineSchema.requireEngineConfiguration(engineSchema,schema,prefix);
    try{if(connection.getAutoCommit()||!connection.getMetaData().getDatabaseProductName().equals("PostgreSQL"))throw unavailable();}
    catch(SQLException ex){throw unavailable();}
    if(one("SELECT login_role FROM maezo_external.mzo_external_caller_scope WHERE "+S+" AND login_role=session_user AND capability='native'",args())==null)throw unavailable();
    for(String table:EngineSchema.TABLES)EngineSchema.requireTable(one(EngineSchema.PIN,engineSchema,table));
    for(String table:NATIVE_TABLES)EngineSchema.requireTable(one(EngineSchema.PIN,nativeSchema,table));
  }
  Object[] args(Object... rest){Object[] all=new Object[rest.length+4];int i=0;
    for(String key:List.of("tenant","environment","engine_name","database_incarnation"))all[i++]=scope.get(key);
    System.arraycopy(rest,0,all,4,rest.length);return all;}
  List<Map<String,Object>> rows(String sql,int max,Object...args){
    try(PreparedStatement ps=connection.prepareStatement(sql)){
      ps.setQueryTimeout(timeout);ps.setMaxRows(max+1);for(int i=0;i<args.length;i++)ps.setObject(i+1,args[i]);
      try(ResultSet rs=ps.executeQuery()){var result=new ArrayList<Map<String,Object>>();long bytes=0;
        while(rs.next()){if(result.size()>=max)throw unavailable();var row=new HashMap<String,Object>();
          for(int i=1;i<=rs.getMetaData().getColumnCount();i++){Object v=rs.getObject(i);
            if(v!=null)bytes+=v instanceof byte[] raw?raw.length:v.toString().getBytes(StandardCharsets.UTF_8).length;
            if(bytes>MAX)throw unavailable();row.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),v);}
          result.add(row);}return result;}
    }catch(SQLException ex){throw unavailable();}
  }
  Map<String,Object> one(String sql,Object... args){var r=rows(sql,1,args);return r.isEmpty()?null:r.get(0);}
  long write(String sql,Object...args){
    CountedWrites.install(session.getConfiguration());
    Number count=session.selectOne(CountedWrites.ID,new EnlistedWrites.Write(sql,args));return count.longValue();
  }
  /** Same MyBatis affectData enlistment as EnlistedWrites, returning one count for bulk coverage. */
  static final class CountedWrites implements SqlSource {
    static final String ID="br.com.maezo.external.enlisted.count.v1";final Configuration configuration;
    CountedWrites(Configuration configuration){this.configuration=configuration;}
    static void install(Configuration c){synchronized(c){if(c.hasStatement(ID,false)){
      if(!(c.getMappedStatement(ID).getSqlSource() instanceof CountedWrites))throw unavailable();return;}
      c.addMappedStatement(new MappedStatement.Builder(c,ID,new CountedWrites(c),SqlCommandType.SELECT)
        .dirtySelect(true).flushCacheRequired(true).useCache(false)
        .resultMaps(List.of(new ResultMap.Builder(c,ID+".count",Long.class,List.of()).build())).build());}}
    public BoundSql getBoundSql(Object parameter){var write=(EnlistedWrites.Write)parameter;var mappings=new ArrayList<ParameterMapping>();
      for(int i=0;i<write.arguments().length;i++)mappings.add(new ParameterMapping.Builder(configuration,"p"+i,Object.class).build());
      var bound=new BoundSql(configuration,"WITH external_written AS ("+write.sql()+" RETURNING 1) SELECT count(*) FROM external_written",mappings,parameter);
      for(int i=0;i<write.arguments().length;i++)bound.setAdditionalParameter("p"+i,write.arguments()[i]);return bound;}
  }
  /** Bounded row memory, no history-count cap; caller checks the retained deadline per row. */
  void stream(String sql,Consumer<Map<String,Object>> consume,Object...args){
    try(PreparedStatement ps=connection.prepareStatement(sql)){
      ps.setQueryTimeout(timeout);ps.setFetchSize(32);for(int i=0;i<args.length;i++)ps.setObject(i+1,args[i]);
      try(ResultSet rs=ps.executeQuery()){while(rs.next()){var row=new HashMap<String,Object>();long bytes=0;
        for(int i=1;i<=rs.getMetaData().getColumnCount();i++){Object value=rs.getObject(i);if(value!=null)bytes+=value.toString().getBytes(StandardCharsets.UTF_8).length;
          if(bytes>MAX)throw unavailable();row.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),value);}
        consume.accept(row);}}
    }catch(SQLException ex){throw unavailable();}
  }
  static final class CoverageDigest {
    final MessageDigest digest;long count;
    CoverageDigest(){try{digest=MessageDigest.getInstance("SHA-256");digest.update((byte)'[');}catch(NoSuchAlgorithmException ex){throw unavailable();}}
    void add(Map<String,Object> event){if(count>0)digest.update((byte)',');digest.update(Jcs.canonical(event));count=Math.incrementExact(count);}
    String finish(){digest.update((byte)']');return HexFormat.of().formatHex(digest.digest());}
  }
  long lock(){
    if(one("SELECT accepted_generation FROM maezo_external.mzo_external_source_generation WHERE "+S+" FOR UPDATE",args())==null)throw unavailable();
    // Cursor-like JDBC traversal avoids materializing scope payloads; generation excludes new heads.
    try(PreparedStatement ps=connection.prepareStatement("SELECT source_ref FROM maezo_external.mzo_external_source_head WHERE "+S+" ORDER BY source_ref COLLATE \"C\" FOR UPDATE")){
      ps.setQueryTimeout(timeout);ps.setFetchSize(32);Object[] a=args();for(int i=0;i<a.length;i++)ps.setObject(i+1,a[i]);
      try(ResultSet rs=ps.executeQuery()){while(rs.next()){ /* locks only; no payload collection */ }}
    }catch(SQLException ex){throw unavailable();}
    one("SELECT epoch FROM maezo_external.mzo_external_checkpoint_accepted WHERE "+S+" FOR UPDATE",args());
    var tenant=one(tenantLockSql(nativeSchema),scope.get("tenant"));
    if(tenant==null)throw unavailable();return ((Number)tenant.get("rev_")).longValue();
  }
  Set<String> revoked(){Set<String> pins=new TreeSet<>();for(var r:rows(revokedSql(nativeSchema),1024,args()))pins.add((String)r.get("fingerprint_"));return pins;}
  Authority authority(ExternalCaseModels.Configuration c){
    var a=one("SELECT e.canonical_designation,e.installation_receipt,c.designation_digest FROM maezo_external.mzo_external_authority_current c JOIN maezo_external.mzo_external_authority_event e USING(tenant,environment,engine_name,database_incarnation,designation_revision) WHERE c.tenant=? AND c.environment=? AND c.engine_name=? AND c.database_incarnation=? AND c.designation_digest=e.designation_digest AND c.authority_revision=e.authority_revision",args());
    if(a==null||!c.designationDigest().equals(a.get("designation_digest")))throw unavailable();
    return new Authority(c,(byte[])a.get("canonical_designation"),(byte[])a.get("installation_receipt"),revoked());
  }
  Map<String,Object> generation(){return one("SELECT accepted_generation,published_generation FROM maezo_external.mzo_external_source_generation WHERE "+S,args());}
  Map<String,Object> head(String ref){return one("SELECT * FROM maezo_external.mzo_external_source_head WHERE "+S+" AND source_ref=?",args(ref));}
  Map<String,Object> event(String ref,long revision){return one("SELECT * FROM maezo_external.mzo_external_source_event WHERE "+S+" AND source_ref=? AND source_revision=?",args(ref,revision));}
  Map<String,Object> receipt(String id){return one("SELECT * FROM maezo_external.mzo_external_publication_receipt WHERE "+S+" AND publication_id=?",args(id));}
  static Map<String,Object> json(Object value){if(value instanceof byte[] b)return canonical(b);if(value==null)throw unavailable();return map(Jcs.parse(value.toString().getBytes(StandardCharsets.UTF_8)));}
  static long numberColumn(Map<String,Object> row,String column){if(row==null||!(row.get(column) instanceof Number n))throw unavailable();return n.longValue();}
  static Instant instantColumn(Object value){if(value instanceof Timestamp t)return t.toInstant();if(value instanceof java.time.OffsetDateTime t)return t.toInstant();throw unavailable();}
  List<Object> heads(){var result=new ArrayList<Object>();for(var h:rows("SELECT source_ref,source_namespace,source_revision,payload_digest,identity,revoked FROM maezo_external.mzo_external_source_head WHERE "+S+" ORDER BY source_ref COLLATE \"C\"",1024,args())){
    var id=json(h.get("identity"));result.add(record("namespace",h.get("source_namespace"),"source_ref",h.get("source_ref"),"source_revision",h.get("source_revision").toString(),"source_digest",h.get("payload_digest"),"upstream_resource_key",id.get("upstream_resource_key"),"case_ref",id.get("case_ref"),"state",Boolean.TRUE.equals(h.get("revoked"))?"revoked":"active"));}return result;}
  void exactCheckpoint(Map<String,Object> statement){
    var accepted=one("SELECT checkpoint_ref,epoch,checkpoint_digest FROM maezo_external.mzo_external_checkpoint_accepted WHERE "+S,args());
    var g=generation();
    if(accepted==null||!statement.get("checkpoint_ref").equals(accepted.get("checkpoint_ref"))
        ||number(statement.get("epoch"))!=numberColumn(accepted,"epoch")
        ||numberColumn(g,"accepted_generation")!=numberColumn(g,"published_generation")
        )throw unavailable();
    checkpointBarrier(statement,heads(),numberColumn(g,"accepted_generation"),numberColumn(g,"published_generation"));
    var accounting=one("SELECT count(*) AS total,min(e.source_generation) AS first_generation,max(e.source_generation) AS last_generation,count(t.source_generation) AS terminal FROM maezo_external.mzo_external_source_event e LEFT JOIN maezo_external.mzo_external_event_terminal t ON t.tenant=e.tenant AND t.environment=e.environment AND t.engine_name=e.engine_name AND t.database_incarnation=e.database_incarnation AND t.source_generation=e.source_generation AND t.source_ref=e.source_ref AND t.source_revision=e.source_revision AND t.source_digest=e.payload_digest WHERE e.tenant=? AND e.environment=? AND e.engine_name=? AND e.database_incarnation=?",args());
    long acceptedGeneration=numberColumn(g,"accepted_generation");
    if(numberColumn(accounting,"total")!=acceptedGeneration||numberColumn(accounting,"terminal")!=acceptedGeneration
      ||acceptedGeneration>0&&(numberColumn(accounting,"first_generation")!=1||numberColumn(accounting,"last_generation")!=acceptedGeneration))throw unavailable();
    var storedManifest=new ArrayList<Object>();
    for(var entry:rows("SELECT namespace,source_ref,source_revision,source_digest,upstream_resource_key,case_ref,state FROM maezo_external.mzo_external_checkpoint_head_entry WHERE "+S+" AND checkpoint_ref=? AND epoch=? ORDER BY source_ref COLLATE \"C\"",1024,args(statement.get("checkpoint_ref"),number(statement.get("epoch")))))
      storedManifest.add(record("namespace",entry.get("namespace"),"source_ref",entry.get("source_ref"),"source_revision",entry.get("source_revision").toString(),"source_digest",entry.get("source_digest"),"upstream_resource_key",entry.get("upstream_resource_key"),"case_ref",entry.get("case_ref"),"state",entry.get("state")));
    if(!storedManifest.equals(list(statement.get("heads"))))throw unavailable();
    for(Object v:list(statement.get("heads"))){var h=map(v);var projected=one("SELECT c.source_revision,c.revoked,c.publication_id,e.payload_digest FROM maezo_external.mzo_external_case c JOIN maezo_external.mzo_external_source_event e USING(tenant,environment,engine_name,database_incarnation,source_ref,source_revision) WHERE c.tenant=? AND c.environment=? AND c.engine_name=? AND c.database_incarnation=? AND c.case_ref=?",args(h.get("case_ref")));
      if(projected==null||numberColumn(projected,"source_revision")!=number(h.get("source_revision"))||!h.get("source_digest").equals(projected.get("payload_digest"))||Boolean.TRUE.equals(projected.get("revoked"))!=h.get("state").equals("revoked")||receipt((String)projected.get("publication_id"))==null)throw unavailable();
      var proof=receipt((String)projected.get("publication_id"));
      if(!"case".equals(proof.get("kind"))||revoked().contains(proof.get("requester_fingerprint")))throw unavailable();
      var committed=canonical((byte[])proof.get("canonical_receipt"));
      if(!h.get("source_ref").equals(committed.get("source_ref"))||!h.get("source_revision").equals(committed.get("source_revision"))||!h.get("source_digest").equals(committed.get("source_digest")))throw unavailable();
    }
  }
  static void checkpointBarrier(Map<String,Object> statement,List<Object> actual,long accepted,long published){
    if(accepted<0||published<0||accepted!=published||!list(statement.get("heads")).equals(actual))throw unavailable();
  }
  /** Native identity/status from actual ACT tables, no case payload state or general REST. */
  Map<String,Object> nativeState(Map<String,Object> identity){
    var rows=rows(EngineSchema.identitySql(engineSchema),1,identity.get("process_instance_ref"),identity.get("process_instance_ref"),identity.get("process_definition_id"));
    if(rows.size()!=1)throw unavailable();return nativeProjection(identity,scope.get("tenant"),rows.get(0));
  }
  Map<String,Map<String,Object>> nativeStates(List<Map<String,Object>> identities){
    if(identities.isEmpty())return Map.of();
    var parameters=new ArrayList<Object>();
    for(var id:identities){parameters.add(id.get("case_ref"));parameters.add(id.get("process_instance_ref"));parameters.add(id.get("process_definition_id"));}
    String sql=EngineSchema.statesSql(engineSchema,identities.size());
    var raw=rows(sql,identities.size(),parameters.toArray());var states=new HashMap<String,Map<String,Object>>();
    var byRef=new HashMap<String,Map<String,Object>>();for(var id:identities)byRef.put(str(id,"case_ref"),id);
    for(var row:raw){String ref=(String)row.get("case_ref");if(!byRef.containsKey(ref)||states.put(ref,nativeProjection(byRef.get(ref),scope.get("tenant"),row))!=null)throw unavailable();}
    if(states.size()!=identities.size())throw unavailable();return states;
  }
  static Map<String,Object> nativeProjection(Map<String,Object> id,Object tenant,Map<String,Object> r){
    if(!tenant.equals(r.get("definition_tenant"))||!id.get("process_definition_id").equals(r.get("definition_id"))
        ||!id.get("process_definition_key").equals(r.get("definition_key"))||number(id.get("process_definition_version"))!=numberColumn(r,"definition_version")
        ||!id.get("process_definition_digest").equals(r.get("definition_digest")))throw unavailable();
    boolean active=r.get("active_id")!=null,historic=r.get("historic_instance")!=null;
    if(!active&&!historic||active&&r.get("ended_at")!=null||!active&&r.get("ended_at")==null)throw unavailable();
    if(active&&(!id.get("process_instance_ref").equals(r.get("active_id"))||!id.get("process_instance_ref").equals(r.get("active_instance"))||!id.get("process_definition_id").equals(r.get("active_definition"))||!tenant.equals(r.get("active_tenant"))))throw unavailable();
    if(historic&&(!id.get("process_instance_ref").equals(r.get("historic_instance"))||!id.get("process_definition_id").equals(r.get("historic_definition"))||!tenant.equals(r.get("historic_tenant"))))throw unavailable();
    return record("identity",id,"state",active?"active":"ended","ended_at",r.get("ended_at")==null?null:time(instantColumn(r.get("ended_at"))));
  }
}

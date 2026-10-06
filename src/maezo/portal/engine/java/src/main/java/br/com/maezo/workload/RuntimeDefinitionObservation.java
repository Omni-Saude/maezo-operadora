package br.com.maezo.workload;

import br.com.maezo.human.Jcs;
import jakarta.servlet.http.HttpServletResponse;
import java.io.*;
import java.lang.management.ManagementFactory;
import java.nio.file.*;
import java.sql.*;
import java.time.*;
import java.util.*;
import java.util.jar.*;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.impl.ProcessEngineImpl;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Actual native READ observations; detached results are emitted only after commit and new READ. */
final class RuntimeDefinitionObservation {
  private RuntimeDefinitionObservation(){}
  private static final ThreadLocal<CommandContext> COMMITTING_OBSERVATION=new ThreadLocal<>();
  static boolean isCommitting(CommandContext context){return context!=null && COMMITTING_OBSERVATION.get()==context;}
  static boolean committingNativeRead(CommandContext context,Object command) {
    if(!isCommitting(context))return false;
    require(command!=null && Set.of(org.cibseven.bpm.engine.impl.ProcessDefinitionQueryImpl.class,
        org.cibseven.bpm.engine.impl.cmd.GetDeploymentProcessModelCmd.class,
        org.cibseven.bpm.engine.impl.cmd.GetDeploymentResourceCmd.class).contains(command.getClass()));
    return true;
  }
  static final class StageToken {
    final WorkloadPlugin plugin;final BoundaryPolicy.Peer peer;final Capability capability;
    final WorkloadServlet.VerifiedObservationRequest request;final RuntimeObservationAdmission admission;
    private final Thread originalThread=Thread.currentThread();
    private CommandContext previousContext;private CapturedObservation previous;
    private Map<String,Object> physicalAnchor,installationAnchor;
    private EmissionValidationStage activeValidation;
    StageToken(WorkloadPlugin plugin,BoundaryPolicy.Peer peer,Capability cap,WorkloadServlet.VerifiedObservationRequest request,RuntimeObservationAdmission admission) {
      this.plugin=plugin;this.peer=peer;capability=cap;this.request=request;this.admission=admission;
    }
    void ownerThread(){require(Thread.currentThread()==originalThread);}
    void current(CommandContext ctx,Connection connection) {
      ownerThread();admission.current();plugin.authenticated(peer);
      require(Context.getCommandContext()==ctx && ctx.getDbSqlSession().getSqlSession().getConnection()==connection
          && ctx.isAuthorizationCheckEnabled() && ctx.isTenantCheckEnabled());
      var config=plugin.observationConfiguration();var engine=plugin.engine();
      require(engine instanceof ProcessEngineImpl nativeEngine && nativeEngine.getProcessEngineConfiguration()==config
          && Context.getProcessEngineConfiguration()==config && ProcessEngines.getProcessEngines().size()==1
          && ProcessEngines.getProcessEngines().get(admission.document.get("engine_name"))==engine
          && config.isAuthorizationEnabled() && config.isTenantCheckEnabled() && "false".equals(config.getDatabaseSchemaUpdate()));
      try {require(!connection.getAutoCommit() && connection.getTransactionIsolation()==Connection.TRANSACTION_READ_COMMITTED
          && "PostgreSQL".equals(connection.getMetaData().getDatabaseProductName()) && !connection.isClosed());}
      catch(SQLException e){throw Refused.unavailable();}
    }
  }
  static final class CapturedObservation {
    final CommandContext context;final Connection connection;final StageToken token;
    final WorkloadCommand.ObservationStage stage;final int sequence;
    final String execution=UUID.randomUUID().toString();final String started;
    private final long sampleInitialWall,sampleInitialNano;
    Map<String,Object> entry,committing,definition,profile,clock,stageWitness;
    String cutoff,afterCommit;boolean committed;
    CapturedObservation(CommandContext ctx,Connection c,StageToken token,WorkloadCommand.ObservationStage stage,int sequence) {
      require(token!=null && token.admission!=null);
      context=ctx;connection=c;this.token=token;this.stage=stage;this.sequence=sequence;
      // Measurement starts here; the authorization budget remains admission.initialWall/initialNano.
      token.admission.timeOnly();sampleInitialWall=System.currentTimeMillis();sampleInitialNano=System.nanoTime();
      started=RuntimeObservationAdmission.time(Instant.ofEpochMilli(sampleInitialWall));token.admission.timeOnly();
    }
    void finishClock() {
      token.admission.timeOnly();long wall=System.currentTimeMillis(),nano=System.nanoTime();token.admission.timeOnly();
      require(wall>=sampleInitialWall && nano>=sampleInitialNano);
      cutoff=RuntimeObservationAdmission.time(Instant.ofEpochMilli(wall));
      clock=map("source","jvm-system-currentTimeMillis","sample_started_at",started,
          "sample_finished_at",cutoff,"engine_clock",cutoff,
          "monotonic_elapsed_ns",Long.toString(nano-sampleInitialNano),"resolution_nominal_ms",1L,
          "accuracy_status","unqualified_wall_clock","absolute_utc_uncertainty_ms",null,"uncertainty_status","not_independently_calibrated");
    }
  }
  static CapturedObservation capture(WorkloadPlugin plugin,CommandContext ctx,BoundaryPolicy.Peer peer,Capability cap,
      WorkloadServlet.VerifiedObservationRequest request,StageToken token,WorkloadCommand.ObservationStage stage,int sequence) {
    require(token.plugin==plugin && token.peer==peer && token.capability==cap && token.request==request
        && ((stage==WorkloadCommand.ObservationStage.CAPTURE && sequence==0 && token.previous==null)
        || (stage==WorkloadCommand.ObservationStage.EMISSION_RECHECK && sequence>=1 && sequence<=8 && token.previous!=null
        && token.previous.committed && token.previousContext!=ctx && sequence==token.previous.sequence+1)));
    try {
    Connection c=ctx.getDbSqlSession().getSqlSession().getConnection();token.physicalAnchor=null;token.installationAnchor=null;token.current(ctx,c);
    plugin.reauthorizeCapability(ctx,peer,cap);
    CapturedObservation result=new CapturedObservation(ctx,c,token,stage,sequence);
    if(token.activeValidation!=null)token.activeValidation.nativeEntry(ctx,c);
    // The entry facts precede locks. Binding facts require fresh native reads, but no lock is acquired until afterwards.
    if(token.admission.measurement!=null)token.admission.measurement.openStage(sequence,result.execution,ctx,c);
    result.entry=measuredEndpoint(result,NativeMeasurementPort.Endpoint.ENTRY,false);
    if(token.activeValidation!=null)token.activeValidation.fixedLocks();
    result.definition=readTarget(token,ctx,c,true);token.current(ctx,c);
    byte[] profile=token.admission.profile();result.profile=map("input_profile","portal-auth-intake.v1","origin","admitted-profile-file",
        "sha256",Jcs.digest(profile),"base64",Base64.getEncoder().encodeToString(profile),"size",(long)profile.length);
    var guarded=observeStageSample(plugin,ctx,c,request,token,true);sameStable(result.entry,guarded);sameSession(result.entry,guarded);
    ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
      boolean previous=WorkloadPlugin.GUARDED.get();WorkloadPlugin.GUARDED.set(true);
      try {
        require(COMMITTING_OBSERVATION.get()==null);COMMITTING_OBSERVATION.set(ctx);
        if(token.activeValidation!=null)token.activeValidation.committing(ctx,c);
        token.current(ctx,c);plugin.reauthorizeCapability(ctx,peer,cap);
        result.committing=measuredEndpoint(result,NativeMeasurementPort.Endpoint.COMMITTING,true);
        sameStable(result.entry,result.committing);sameSession(result.entry,result.committing);
        require(result.definition.equals(readTarget(token,ctx,c,true)));token.current(ctx,c);
        result.finishClock();
        token.current(ctx,c);
      }catch(RuntimeException|Error failure){abortMeasurement(token,failure);throw failure;}
      finally {COMMITTING_OBSERVATION.remove();if(previous)WorkloadPlugin.GUARDED.set(true);else WorkloadPlugin.GUARDED.remove();}
    });
    ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->{
      result.committed=true;if(token.activeValidation!=null)token.activeValidation.committed(ctx);
    });
    return result;
    }catch(RuntimeException|Error failure){abortMeasurement(token,failure);throw failure;}
  }
  static CapturedObservation confirm(CapturedObservation result,StageToken token) {
    try {
    require(result.token==token && result.committed && result.committing!=null && Context.getCommandContext()==null);
    if(token.activeValidation!=null)token.activeValidation.contextClosed(result);
    token.admission.current();result.afterCommit=token.admission.time();
    require(Instant.parse(result.cutoff).compareTo(Instant.parse(result.afterCommit))<=0);
    checkStageBracket(result);
    if(token.previous!=null) {
      sameStable(token.previous.entry,result.entry);
      var old=session(token.previous.committing);var fresh=session(result.entry);
      require(Instant.parse(Json.string(old,"observed_at")).compareTo(Instant.parse(Json.string(fresh,"observed_at")))<=0);
      if(old.get("transaction_id_if_assigned")!=null && fresh.get("transaction_id_if_assigned")!=null)
        require(!old.get("transaction_id_if_assigned").equals(fresh.get("transaction_id_if_assigned")));
      if(old.get("backend_pid").equals(fresh.get("backend_pid")))require(old.get("backend_start").equals(fresh.get("backend_start")));
    }
    result.stageWitness=immutable(map("stage",result.stage.name(),"stage_sequence",(long)result.sequence,
        "server_stage_execution_uuid",result.execution,"entry",result.entry,"committing",result.committing,
        "native_commit_confirmed",true,"jvm_after_commit_at",result.afterCommit));
    RuntimeObservationAdmission.validate("StageWitness",result.stageWitness);
    token.previous=result;token.previousContext=result.context;
    if(token.admission.measurement!=null)token.admission.measurement.stageClosed();
    token.admission.current();return result;
    }catch(RuntimeException|Error failure){abortMeasurement(token,failure);throw failure;}
  }
  private static Map<String,Object> measuredEndpoint(CapturedObservation result,NativeMeasurementPort.Endpoint endpoint,boolean locked) {
    var token=result.token;var ctx=result.context;var c=result.connection;var port=token.admission.measurement;
    token.current(ctx,c);token.plugin.reauthorizeCapability(ctx,token.peer,token.capability);
    if(port==null)return observeStageSample(token.plugin,ctx,c,token.request,token,locked);
    port.hold(endpoint);port.awaitRootRelease();
    token.current(ctx,c);token.plugin.reauthorizeCapability(ctx,token.peer,token.capability);
    var sample=observeStageSample(token.plugin,ctx,c,token.request,token,locked);
    port.seal();
    token.current(ctx,c);token.plugin.reauthorizeCapability(ctx,token.peer,token.capability);
    var fresh=observeStageSample(token.plugin,ctx,c,token.request,token,locked);
    sameStable(sample,fresh);sameEndpointSession(sample,fresh);return fresh;
  }
  private static void sameEndpointSession(Map<String,Object> a,Map<String,Object> b) {
    var x=session(a);var y=session(b);
    for(String key:List.of("backend_pid","backend_start","transaction_id_if_assigned","transaction_started_at","backend_ssl","backend_tls_version"))
      require(Objects.equals(x.get(key),y.get(key)));
    sameSession(a,b);
  }
  private static void abortMeasurement(StageToken token,Throwable cause) {
    if(token.admission.measurement!=null)try{token.admission.measurement.abort();}
    catch(RuntimeException|Error cleanup){cause.addSuppressed(cleanup);}
  }
  static ObservationEmission completed(CapturedObservation result,StageToken token){return new ObservationEmission(confirm(result,token),token);}
  private static void checkStageBracket(CapturedObservation result) {
    Instant low=Instant.parse(result.started).minusMillis(5000),high=Instant.parse(result.afterCommit).plusMillis(5000);
    for(var endpoint:List.of(result.entry,result.committing)) {
      var s=session(endpoint);Instant observed=Instant.parse(Json.string(s,"observed_at"));
      require(!observed.isBefore(low) && !observed.isAfter(high) && observed.isBefore(result.token.admission.deadline));
    }
  }
  static Map<String,Object> observeStageSample(WorkloadPlugin plugin,CommandContext ctx,Connection c,
      WorkloadServlet.VerifiedObservationRequest carrier,StageToken token) {
    return observeStageSample(plugin,ctx,c,carrier,token,true);
  }
  private static Map<String,Object> observeStageSample(WorkloadPlugin plugin,CommandContext ctx,Connection c,
      WorkloadServlet.VerifiedObservationRequest carrier,StageToken token,boolean locked) {
    require(token.plugin==plugin && token.request==carrier);token.current(ctx,c);
    var physical=physicalWitness(token,ctx,c);var db=Json.object(physical.get("stable_database_projection"));
    var session=Json.object(physical.get("session_transaction"));
    var target=readTarget(token,ctx,c,locked);var install=installation(token);
    var binding=map("policy_digest",token.admission.policy.digest,"observation_admission_sha256",token.admission.digest,
        "capability_digest",token.capability.digest,"tenant",token.admission.policy.tenant,"environment",token.admission.policy.environment,
        "engine_name",plugin.engine().getName(),"definition_id",target.get("definition_id"),"deployment_id",target.get("deployment_id"),
        "process_key",target.get("process_key"),"process_version",target.get("process_version"),"xml_sha256",target.get("xml_sha256"),
        "profile_sha256",Jcs.digest(token.admission.profile()),"original_deadline",token.admission.document.get("original_deadline"),
        "candidate_expectations",expectations(token));
    token.current(ctx,c);
    var sample=immutable(map("stable_database_projection",db,"binding_projection",binding,"runtime_installation_projection",install,"session_transaction",session));
    RuntimeObservationAdmission.validate("StageSample",sample);return sample;
  }
  private static Map<String,Object> physicalWitness(StageToken token,CommandContext ctx,Connection c) {
    token.current(ctx,c);
    Map<String,Object> db=new TreeMap<>(),session=new TreeMap<>();
    String sql="SELECT current_database(),d.oid::text,(pg_control_system()).system_identifier::text,"
        +"e.oid::text,n.oid::text,current_user::text,session_user::text,pg_backend_pid()::text,a.backend_start,"
        +"s.ssl,s.version,pg_current_xact_id_if_assigned()::text,transaction_timestamp(),clock_timestamp() "
        +"FROM pg_database d JOIN pg_namespace e ON e.nspname='cibseven' JOIN pg_namespace n ON n.nspname='maezo_native' "
        +"JOIN pg_stat_activity a ON a.pid=pg_backend_pid() JOIN pg_stat_ssl s ON s.pid=a.pid WHERE d.datname=current_database()";
    try(var statement=c.prepareStatement(sql);var r=statement.executeQuery()) {
      require(r.next());db.putAll(map("database_name",r.getString(1),"database_oid",r.getString(2),"system_identifier",r.getString(3),
          "engine_schema_name","cibseven","engine_schema_oid",r.getString(4),"native_schema_name","maezo_native","native_schema_oid",r.getString(5),
          "current_user",r.getString(6),"session_user",r.getString(7),"witness_projection","provider-native-physical-db-join.v2"));
      session.putAll(map("backend_pid",r.getString(8),"backend_start",sqlTime(r,9),"backend_ssl",r.getBoolean(10),"backend_tls_version",r.getString(11),
          "transaction_id_if_assigned",r.getString(12),"transaction_started_at",sqlTime(r,13),"observed_at",sqlTime(r,14),
          "transaction_id_source","pg_current_xact_id_if_assigned","connection_source","actual-commandcontext-dbsqlsession-connection",
          "context_connection_identity","enlisted-object-identity-checked","auto_commit",false,"transaction_isolation","READ_COMMITTED",
          "transaction_started_at_source","postgresql-transaction_timestamp-on-enlisted-connection","observed_at_source","postgresql-clock_timestamp-on-enlisted-connection"));
      require(!r.next());
    }catch(SQLException e){throw Refused.unavailable();}
    token.current(ctx,c);db.put("catalogue_projection_sha256",catalogue(c,token));
    RuntimeObservationAdmission.validate("StableDatabaseProjection",db);RuntimeObservationAdmission.validate("SessionTransactionWitness",session);
    require(db.equals(Json.object(Json.object(token.admission.document.get("expected_database")).get("stable_projection"))));
    require(!Instant.parse(Json.string(session,"backend_start")).isAfter(Instant.parse(Json.string(session,"transaction_started_at")))
        && !Instant.parse(Json.string(session,"transaction_started_at")).isAfter(Instant.parse(Json.string(session,"observed_at"))));
    var physical=immutable(map("stable_database_projection",db,"session_transaction",session));
    if(token.physicalAnchor==null)token.physicalAnchor=physical;
    else {
      require(token.physicalAnchor.get("stable_database_projection").equals(db));sameSession(token.physicalAnchor,physical);token.physicalAnchor=physical;
    }
    return physical;
  }
  private static void afterWait(StageToken token,CommandContext ctx,Connection c) {
    token.current(ctx,c);token.plugin.reauthorizeCapability(ctx,token.peer,token.capability);
    physicalWitness(token,ctx,c);var currentInstallation=installation(token);
    if(token.installationAnchor==null)token.installationAnchor=currentInstallation;
    else require(token.installationAnchor.equals(currentInstallation));
    token.current(ctx,c);
  }
  private static String sqlTime(ResultSet r,int column)throws SQLException {
    return RuntimeObservationAdmission.time(r.getObject(column,java.time.OffsetDateTime.class).toInstant());
  }
  /** Exact closed projection: two namespaces plus ACT3, stable canonical primitive bytes. */
  private static String catalogue(Connection c,StageToken token) {
    List<Object> schemas=new ArrayList<>(),relations=new ArrayList<>();
    try(var s=c.prepareStatement("SELECT nspname,oid::text,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname IN ('cibseven','maezo_native') ORDER BY nspname");var r=s.executeQuery()) {
      while(r.next())schemas.add(map("name",r.getString(1),"oid",r.getString(2),"owner",r.getString(3)));
    }catch(SQLException e){throw Refused.unavailable();}
    require(schemas.size()==2);
    try(var s=c.prepareStatement("SELECT n.nspname,c.relname,c.oid::text,pg_get_userbyid(c.relowner),c.relkind::text,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text "
        +"FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='cibseven' AND c.relname IN ('act_re_procdef','act_re_deployment','act_ge_bytearray') ORDER BY n.nspname,c.relname");var r=s.executeQuery()) {
      while(r.next())relations.add(map("schema",r.getString(1),"name",r.getString(2),"oid",r.getString(3),"owner",r.getString(4),"kind",r.getString(5),"rls",r.getBoolean(6),"force_rls",r.getBoolean(7),"acl",r.getString(8)));
    }catch(SQLException e){throw Refused.unavailable();}
    require(relations.size()==3);
    for(Object relation:relations) {
      var row=Json.object(relation);require("r".equals(row.get("kind")) && !Json.bool(row,"rls") && !Json.bool(row,"force_rls"));
      try(var s=c.prepareStatement("SELECT to_regclass(?)::oid::text")) {
        s.setString(1,Json.string(row,"name"));try(var r=s.executeQuery()){require(r.next() && row.get("oid").equals(r.getString(1)) && !r.next());}
      }catch(SQLException e){throw Refused.unavailable();}
    }
    String prefix=token.plugin.observationConfiguration().getDatabaseTablePrefix();require(prefix.isEmpty() || "cibseven.".equals(prefix));
    token.admission.current();return Json.digest(map("schema","provider-native-act-catalogue.v1","schemas",schemas,"relations",relations));
  }
  private static Map<String,Object> readTarget(StageToken token,CommandContext ctx,Connection c,boolean lock) {
    token.current(ctx,c);var d=token.admission.document;String suffix=lock?" FOR SHARE":"";
    String id=Json.string(d,"definition_id"),deployment=Json.string(d,"deployment_id"),tenant=token.admission.policy.tenant;
    String resource;
    try(var s=c.prepareStatement("SELECT KEY_,VERSION_,DEPLOYMENT_ID_,TENANT_ID_,SUSPENSION_STATE_,RESOURCE_NAME_ FROM cibseven.ACT_RE_PROCDEF WHERE ID_=? AND TENANT_ID_=?"+suffix)) {
      s.setString(1,id);s.setString(2,tenant);try(var r=s.executeQuery()) {
        require(r.next() && d.get("process_key").equals(r.getString(1)) && Json.number(d,"process_version")==r.getLong(2)
            && deployment.equals(r.getString(3)) && tenant.equals(r.getString(4)) && r.getInt(5)==1);
        resource=r.getString(6);require(resource!=null && resource.matches("[!-~]{1,256}") && !r.next());
      }
    }catch(SQLException e){throw Refused.unavailable();}
    afterWait(token,ctx,c);
    try(var s=c.prepareStatement("SELECT ID_ FROM cibseven.ACT_RE_DEPLOYMENT WHERE ID_=? AND TENANT_ID_=?"+suffix)) {
      s.setString(1,deployment);s.setString(2,tenant);try(var r=s.executeQuery()){require(r.next() && deployment.equals(r.getString(1)) && !r.next());}
    }catch(SQLException e){throw Refused.unavailable();}
    token.current(ctx,c);byte[] row;
    try(var s=c.prepareStatement("SELECT BYTES_ FROM cibseven.ACT_GE_BYTEARRAY WHERE DEPLOYMENT_ID_=? AND NAME_=?"+suffix)) {
      s.setString(1,deployment);s.setString(2,resource);try(var r=s.executeQuery()) {
        require(r.next());try(var stream=r.getBinaryStream(1)){require(stream!=null);row=stream.readNBytes(524289);}
        require(row.length>0 && row.length<=524288 && !r.next());
      }
    }catch(SQLException|IOException e){throw Refused.unavailable();}
    afterWait(token,ctx,c);
    var nativeDefinition=WorkloadCommand.definition(token.plugin.engine(),token.capability.target,tenant);
    require(deployment.equals(nativeDefinition.getDeploymentId()) && resource.equals(nativeDefinition.getResourceName()));
    token.current(ctx,c);byte[] raw;
    try(var stream=token.plugin.engine().getRepositoryService().getProcessModel(id)){require(stream!=null);raw=stream.readNBytes(524289);}
    catch(IOException e){throw Refused.unavailable();}
    afterWait(token,ctx,c);require(raw.length>0 && raw.length<=524288 && Arrays.equals(row,raw) && d.get("xml_sha256").equals(Jcs.digest(raw)));
    validateXml(raw);afterWait(token,ctx,c);
    return immutable(map("definition_id",id,"deployment_id",deployment,"process_key",nativeDefinition.getKey(),"process_version",(long)nativeDefinition.getVersion(),
        "tenant",nativeDefinition.getTenantId(),"resource_name",resource,"xml_sha256",Jcs.digest(raw),"xml_base64",Base64.getEncoder().encodeToString(raw),"xml_size",(long)raw.length,"suspended",false));
  }
  static void validateXml(byte[] raw) {
    require(raw.length>0 && raw.length<=524288);
    try {
      var f=DocumentBuilderFactory.newInstance();f.setNamespaceAware(true);f.setFeature("http://apache.org/xml/features/disallow-doctype-decl",true);
      f.setFeature("http://xml.org/sax/features/external-general-entities",false);f.setFeature("http://xml.org/sax/features/external-parameter-entities",false);
      f.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD,"");f.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA,"");
      var doc=f.newDocumentBuilder().parse(new ByteArrayInputStream(raw));
      var processes=doc.getElementsByTagNameNS("http://www.omg.org/spec/BPMN/20100524/MODEL","process");
      require(processes.getLength()==1 && "SP-OP-AUTH-001".equals(((org.w3c.dom.Element)processes.item(0)).getAttribute("id")));
    }catch(Exception e){throw Refused.unavailable();}
  }
  private static Map<String,Object> expectations(StageToken token) {
    return map("candidate_sha",token.admission.document.get("candidate_sha"),"image_id",token.admission.document.get("expected_image_id"),"validation_origin","root-crosslink-required");
  }
  private static Map<String,Object> installation(StageToken token) {
    token.admission.current();var config=token.plugin.observationConfiguration();var root=Path.of(System.getProperty("catalina.base"));
    require(root.isAbsolute());var d=token.admission.document;
    Map<String,Object> pins=new TreeMap<>();
    Map<String,String> names=Map.of("descriptor_sha256","conf/bpm-platform.xml","native_jar_sha256","lib/maezo-human-command.jar",
        "support_jar_sha256","lib/provider-auth-test-support.jar","rest_spi_jar_sha256","webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar");
    names.forEach((key,path)->{String digest=Json.string(d,key);requirePinned(token,root.resolve(path),digest);pins.put(key,digest);});
    String schema=resourceDigest(RuntimeObservationAdmission.class,"provider-native-observation-schema-v1.json",root.resolve("lib/maezo-human-command.jar"));
    require(schema.equals(d.get("observation_schema_sha256")));pins.put("observation_schema_sha256",schema);
    String variant=Json.string(d,"startup_variant");
    for(String kind:List.of("descriptor","vendor")) {
      Path jar=root.resolve(kind.equals("descriptor")?"lib/provider-auth-test-support.jar":"lib/maezo-human-command.jar");
      String hash=jarResource(jar,"provider-native/"+variant+"/secured-startup-"+(kind.equals("descriptor")?"descriptors":"vendor")+".sha256");
      String key="startup_"+kind+"_inventory_sha256";require(hash.equals(d.get(key)));pins.put(key,hash);
    }
    Class<?> authValues;
    try{authValues=Class.forName("br.com.maezo.human.AuthValues",false,WorkloadPlugin.class.getClassLoader());}
    catch(ClassNotFoundException e){throw Refused.unavailable();}
    List<Class<?>> types=List.of(WorkloadPlugin.class,ProcessEngineImpl.class,RuntimeDefinitionObservation.class,authValues);
    List<Object> provenance=new ArrayList<>();
    for(Class<?> type:types.stream().sorted(Comparator.comparing(Class::getName)).toList()) {
      try {
        require(type.getClassLoader()==WorkloadPlugin.class.getClassLoader());
        Path jar=Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI());
        require(jar.startsWith(root.resolve("lib")) && jar.getFileName().toString().endsWith(".jar"));
        String jarHash=binaryDigest(jar,token.admission);requireClassPinned(token,root,jar,jarHash);
        String entry=type.getName().replace('.','/')+".class",classHash=jarResource(jar,entry);
        try(var stream=type.getResourceAsStream("/"+entry)) {require(stream!=null && classHash.equals(Jcs.digest(stream.readAllBytes())));}
        provenance.add(map("class_name",type.getName(),"jar_relative_path",root.relativize(jar).toString(),"jar_sha256",jarHash,"class_sha256",classHash,"loader_group","engine-common"));
      }catch(Exception e){throw Refused.unavailable();}
    }
    String version;
    try{version=(String)ProcessEngine.class.getField("VERSION").get(null);}catch(ReflectiveOperationException e){throw Refused.unavailable();}
    require("2.1.0".equals(version));token.admission.current();
    var jvm=map("boot_uuid",RuntimeObservationAdmission.BOOT,"pid_namespace",ProcessHandle.current().pid(),"start_time_epoch_ms",ManagementFactory.getRuntimeMXBean().getStartTime(),
        "engine_registry_size",(long)ProcessEngines.getProcessEngines().size(),"engine_registry_name",token.plugin.engine().getName());
    return immutable(map("engine_name",token.plugin.engine().getName(),"authorization_enabled",config.isAuthorizationEnabled(),"tenant_check_enabled",config.isTenantCheckEnabled(),
        "schema_update",config.getDatabaseSchemaUpdate(),"jvm_process",jvm,"class_provenance",provenance,"mounted_pins",pins));
  }
  private static void requireClassPinned(StageToken token,Path root,Path jar,String digest) {
    if(jar.equals(root.resolve("lib/maezo-human-command.jar"))) {requirePinned(token,jar,digest);return;}
    Path main=root.resolve("lib/maezo-human-command.jar");requirePinned(token,main,Json.string(token.admission.document,"native_jar_sha256"));
    String entry="provider-native/"+Json.string(token.admission.document,"startup_variant")+"/secured-startup-vendor.sha256";
    token.admission.timeOnly();String inventoryHash=jarResource(main,entry);token.admission.timeOnly();
    require(inventoryHash.equals(token.admission.document.get("startup_vendor_inventory_sha256")));
    try(var archive=new JarFile(main.toFile());var stream=archive.getInputStream(archive.getJarEntry(entry))) {
      byte[] raw=stream.readNBytes(Json.LIMIT+1);token.admission.timeOnly();require(raw.length<=Json.LIMIT && Jcs.digest(raw).equals(inventoryHash));
      String relative=root.relativize(jar).toString();boolean found=false;Set<String> paths=new HashSet<>();
      for(String line:new String(raw,java.nio.charset.StandardCharsets.UTF_8).split("\n")) {
        require(line.matches("[0-9a-f]{64} [A-Za-z0-9_./$@-]+") && !line.contains("..") && paths.add(line.substring(65)));
        if(line.substring(65).equals(relative)){require(line.substring(0,64).equals(digest));found=true;}
      }
      require(found);
    }catch(IOException e){throw Refused.unavailable();}finally{token.admission.timeOnly();}
  }
  private static void requirePinned(StageToken token,Path path,String digest) {
    var refs=token.admission.policy.files.stream().filter(f->path.toString().equals(f.get("path")) && digest.equals(f.get("sha256"))).toList();
    require(refs.size()==1 && digest.equals(binaryDigest(path,token.admission)));
  }
  static String binaryDigest(Path path) {return binaryDigest(path,null);}
  private static String binaryDigest(Path path,RuntimeObservationAdmission owned) {
    if(owned!=null)owned.timeOnly();
    try {
      require(path.isAbsolute() && path.equals(path.toRealPath()) && Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS));
      var before=Files.readAttributes(path,"unix:dev,ino,size,lastModifiedTime,nlink,mode,uid",LinkOption.NOFOLLOW_LINKS);
      long uid=((Number)before.get("uid")).longValue();
      long own=((Number)Files.getAttribute(Path.of(System.getProperty("user.home")),"unix:uid")).longValue();
      require(((Number)before.get("nlink")).intValue()==1 && (uid==0 || uid==own)
          && (((Number)before.get("mode")).intValue()&0022)==0);
      long expectedSize=((Number)before.get("size")).longValue();require(expectedSize>0);
      var digest=java.security.MessageDigest.getInstance("SHA-256");
      long count=0;
      try(var stream=Files.newInputStream(path,LinkOption.NOFOLLOW_LINKS)) {
        byte[] b=new byte[65536];
        while(true) {
          if(owned!=null)owned.timeOnly();int n=stream.read(b);if(owned!=null)owned.timeOnly();if(n==-1)break;
          count+=n;require(count<=expectedSize);digest.update(b,0,n);
        }
      }
      if(owned!=null)owned.timeOnly();require(count==expectedSize);
      require(before.equals(Files.readAttributes(path,"unix:dev,ino,size,lastModifiedTime,nlink,mode,uid",LinkOption.NOFOLLOW_LINKS)));
      return HexFormat.of().formatHex(digest.digest());
    }catch(Exception e){throw Refused.unavailable();}
    finally{if(owned!=null)owned.timeOnly();}
  }
  static String resourceDigest(Class<?> type,String resource,Path expectedJar) {
    try {
      require(Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(expectedJar));
      var all=Collections.list(type.getClassLoader().getResources(resource));require(all.size()==1);
      var url=all.get(0);require("jar".equals(url.getProtocol()));
      var connection=(java.net.JarURLConnection)url.openConnection();require(Path.of(connection.getJarFileURL().toURI()).equals(expectedJar));
      return jarResource(expectedJar,resource);
    }catch(Exception e){throw Refused.unavailable();}
  }
  static String jarResource(Path jar,String entry) {
    binaryDigest(jar);
    try(var file=new JarFile(jar.toFile())) {
      require(file.stream().filter(e->e.getName().equals(entry)).count()==1);
      try(var stream=file.getInputStream(file.getJarEntry(entry))) {byte[] raw=stream.readNBytes(Json.LIMIT+1);require(raw.length>0 && raw.length<=Json.LIMIT);return Jcs.digest(raw);}
    }catch(IOException e){throw Refused.unavailable();}
  }
  static void sameStable(Map<String,Object> a,Map<String,Object> b) {
    for(String key:List.of("stable_database_projection","binding_projection","runtime_installation_projection"))require(a.get(key).equals(b.get(key)));
  }
  static Map<String,Object> session(Map<String,Object> sample){return Json.object(sample.get("session_transaction"));}
  static void sameSession(Map<String,Object> a,Map<String,Object> b) {
    var x=session(a);var y=session(b);
    for(String key:List.of("backend_pid","backend_start","transaction_started_at","backend_ssl","backend_tls_version"))require(x.get(key).equals(y.get(key)));
    if(x.get("transaction_id_if_assigned")!=null)require(x.get("transaction_id_if_assigned").equals(y.get("transaction_id_if_assigned")));
    require(!Instant.parse(Json.string(x,"observed_at")).isAfter(Instant.parse(Json.string(y,"observed_at"))));
  }
  static Map<String,Object> map(Object... pairs) {
    require(pairs.length%2==0);var m=new TreeMap<String,Object>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;
  }
  static Map<String,Object> immutable(Map<String,Object> map){return Json.parse(Json.bytes(map));}
  private static void require(boolean value){RuntimeObservationAdmission.require(value);}

  /** No public factory, caller flag or callback can produce a completed validation stage. */
  private static final class EmissionValidationStage {
    private enum Phase {STREAM_BOUND_NEEDS_READ,ENTRY_PINS,NEW_READ_CONTEXT,SQL_ENTRY_AND_NATIVE_READ,
      FIXED_LOCKS_AND_RESOURCE,COMMITTING_NATIVE_READ,COMMITTED_MARKED,READ_CONTEXT_CLOSED,
      POSTCOMMIT_SELF_VALIDATION,ASSEMBLING_IMMUTABLE_BYTES,TERMINAL_OWNED_GUARDS,COMPLETED_READY,FIRST_BYTE_HANDOFF,
      WRITING,FLUSHING,CLOSING,SERVER_COMPLETE,REFUSED_TERMINAL,UNKNOWN_TERMINAL}
    private final ObservationEmission owner;private final Thread thread=Thread.currentThread();private final int sequence;
    private Phase phase=Phase.STREAM_BOUND_NEEDS_READ;
    private CommandContext context;private Connection connection;private byte[] prepared;
    private EmissionValidationStage(ObservationEmission owner,int sequence) {
      this.owner=owner;this.sequence=sequence;require(sequence>=1 && sequence<=8);
    }
    private void pureGuard() {
      require(Thread.currentThread()==thread);owner.token.admission.timeOnly();
    }
    private void advance(Phase expected,Phase next) {pureGuard();require(phase==expected);phase=next;}
    private void entryPins() {
      advance(Phase.STREAM_BOUND_NEEDS_READ,Phase.ENTRY_PINS);owner.token.admission.current();
      installation(owner.token);pureGuard();advance(Phase.ENTRY_PINS,Phase.NEW_READ_CONTEXT);
    }
    private void nativeEntry(CommandContext ctx,Connection c) {
      owner.token.current(ctx,c);require(owner.token.activeValidation==this);
      context=ctx;connection=c;advance(Phase.NEW_READ_CONTEXT,Phase.SQL_ENTRY_AND_NATIVE_READ);
    }
    private void fixedLocks() {advance(Phase.SQL_ENTRY_AND_NATIVE_READ,Phase.FIXED_LOCKS_AND_RESOURCE);}
    private void committing(CommandContext ctx,Connection c) {
      require(ctx==context && c==connection);owner.token.current(ctx,c);
      advance(Phase.FIXED_LOCKS_AND_RESOURCE,Phase.COMMITTING_NATIVE_READ);
    }
    private void committed(CommandContext ctx) {
      require(ctx==context && Context.getCommandContext()==ctx);
      advance(Phase.COMMITTING_NATIVE_READ,Phase.COMMITTED_MARKED);
    }
    private void contextClosed(CapturedObservation actual) {
      require(actual.context==context && actual.connection==connection && actual.committed && actual.committing!=null
          && actual.sequence==sequence && Context.getCommandContext()==null);
      advance(Phase.COMMITTED_MARKED,Phase.READ_CONTEXT_CLOSED);
      advance(Phase.READ_CONTEXT_CLOSED,Phase.POSTCOMMIT_SELF_VALIDATION);
    }
    private void postCommitOwnedGuards() {
      pureGuard();require(phase==Phase.POSTCOMMIT_SELF_VALIDATION && Context.getCommandContext()==null);
      owner.token.admission.current();installation(owner.token);pureGuard();
    }
    private void assembled(byte[] bytes) {
      advance(Phase.POSTCOMMIT_SELF_VALIDATION,Phase.ASSEMBLING_IMMUTABLE_BYTES);
      require(bytes.length>0 && bytes.length<=Json.LIMIT);prepared=bytes.clone();pureGuard();
    }
    private void terminalOwnedGuards() {
      advance(Phase.ASSEMBLING_IMMUTABLE_BYTES,Phase.TERMINAL_OWNED_GUARDS);
      owner.token.admission.current();var installed=installation(owner.token);
      require(installed.equals(owner.capture.entry.get("runtime_installation_projection")));
      owner.token.admission.timeOnly();require(Context.getCommandContext()==null && prepared!=null);
      advance(Phase.TERMINAL_OWNED_GUARDS,Phase.COMPLETED_READY);
    }
    /** Memory/pure-time only after readiness. Unknown re-entry/thread handoff refuses, never retries. */
    private byte[] firstByte() {
      advance(Phase.COMPLETED_READY,Phase.FIRST_BYTE_HANDOFF);require(Context.getCommandContext()==null);return prepared;
    }
    private void writing(){advance(Phase.FIRST_BYTE_HANDOFF,Phase.WRITING);}
    private void flushing(){advance(Phase.WRITING,Phase.FLUSHING);}
    private void closing(){advance(Phase.FLUSHING,Phase.CLOSING);}
    private void complete(){advance(Phase.CLOSING,Phase.SERVER_COMPLETE);}
    private void failed(boolean bytesPossible){phase=bytesPossible?Phase.UNKNOWN_TERMINAL:Phase.REFUSED_TERMINAL;}

  }

  static final class ObservationEmission {
    private final CapturedObservation capture;private final StageToken token;private final List<Map<String,Object>> stages=new ArrayList<>();
    private boolean emitted,attempted;
    private ObservationEmission(CapturedObservation capture,StageToken token){this.capture=capture;this.token=token;}
    private EmissionValidationStage prepareForEmission() {
      require(!emitted && stages.size()<8 && token.activeValidation==null);token.admission.current();
      var validation=new EmissionValidationStage(this,stages.size()+1);
      token.activeValidation=validation;
      try {
        validation.entryPins();var fresh=token.plugin.recheckObservation(token,stages.size()+1);
        stages.add(fresh.stageWitness);validation.postCommitOwnedGuards();
        byte[] prepared=bytes();validation.assembled(prepared);validation.terminalOwnedGuards();
        return validation;
      }finally{token.activeValidation=null;}
    }
    private byte[] bytes() {
      var install=Json.object(capture.entry.get("runtime_installation_projection"));
      var result=map("schema","provider-native-runtime-snapshot.v4","request_sha256",token.request.digest(),"observation_admission_sha256",token.admission.digest,
          "nonce",token.admission.document.get("nonce"),"original_deadline",token.admission.document.get("original_deadline"),"policy_digest",token.admission.policy.digest,
          "capability_digest",token.capability.digest,"identity",token.peer.identity(),"peer_certificate_sha256",token.peer.certificate(),"peer_spki_sha256",token.peer.spki(),
          "engine_name",install.get("engine_name"),"cibseven_version","2.1.0","authorization_enabled",install.get("authorization_enabled"),"tenant_check_enabled",install.get("tenant_check_enabled"),
          "schema_update",install.get("schema_update"),"definition",capture.definition,"profile",capture.profile,"jvm_process",install.get("jvm_process"),"class_provenance",install.get("class_provenance"),
          "mounted_pins",install.get("mounted_pins"),"candidate_expectations",expectations(token),"clock",capture.clock,"cutoff","command-transaction-COMMITTING-as-of",
          "authority_semantics","technical-observation-only-no-effect-authorization","database_witness",capture.stageWitness,
          "emission_recheck",map("boundary","new-workloadcommand-read-context-after-capture-commit","observed_at",stages.get(stages.size()-1).get("jvm_after_commit_at"),
              "original_cutoff_preserved",true,"authority_semantics","current-read-rechecked-not-future-lease","observed_at_source","jvm-system-currentTimeMillis-after-new-read-commit","read_stages",List.copyOf(stages),"validation_protocol","finite-private-emission-stage.v1"),
          "cutoff_observed_at",capture.cutoff,"capture_transaction_commit_confirmed",true);
      var response=map("protocol","maezo.engine-result.v1","capability_digest",token.capability.digest,"result",result);
      RuntimeObservationAdmission.validate("Response",response);
      // Explicitly bound all metadata independent of base64 payloads.
      var metadata=new TreeMap<>(result);var definitionMetadata=new TreeMap<>(capture.definition);definitionMetadata.remove("xml_base64");
      var profileMetadata=new TreeMap<>(capture.profile);profileMetadata.remove("base64");metadata.put("definition",definitionMetadata);metadata.put("profile",profileMetadata);
      require(Json.bytes(map("protocol","maezo.engine-result.v1","capability_digest",token.capability.digest,"result",metadata)).length<=65536);
      byte[] raw=Json.bytes(response);token.admission.current();return raw;
    }
    void writeVerified(HttpServletResponse response)throws IOException {
      require(!emitted && !attempted);token.ownerThread();attempted=true;
      boolean started=false;EmissionValidationStage validation=null;jakarta.servlet.ServletOutputStream out=null;
      try {
        token.admission.current();out=response.getOutputStream();token.admission.current();
        validation=prepareForEmission();byte[] raw=validation.firstByte();validation.writing();
        // COMPLETED_READY -> first write has only private memory/pure time guards, no new I/O or callback.
        for(int offset=0;offset<raw.length;offset+=8192) {
          if(offset>0)token.admission.current();else token.admission.timeOnly();
          started=true;out.write(raw,offset,Math.min(8192,raw.length-offset));token.admission.current();
        }
        validation.flushing();out.flush();token.admission.current();validation.closing();out.close();token.admission.current();validation.complete();
        if(token.admission.measurement!=null)token.admission.measurement.complete(Jcs.digest(raw));
        token.admission.timeOnly();emitted=true;
      }catch(IOException|RuntimeException|Error failure) {
        abortMeasurement(token,failure);
        if(validation!=null)validation.failed(started);
        if(started){try{if(out!=null)out.close();}catch(IOException ignored){}throw new IOException("observation emission incomplete");}
        throw failure;
      }
    }
  }
}

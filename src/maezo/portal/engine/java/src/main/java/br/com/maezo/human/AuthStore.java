package br.com.maezo.human;

import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import org.apache.ibatis.session.SqlSession;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** E04 native durable CAS/receipt store. Every write joins the current CIB transaction. */
final class AuthStore {
  private final Connection connection;
  private final SqlSession session;
  private final int timeout;
  final String tenant;
  final Map<String,Object> scope;
  private final StaffCaseInstallation.Configuration staffConfiguration;

  AuthStore(CommandContext context,Map<String,Object> scope,int timeout) {
    this(context,scope,timeout,null);
  }
  AuthStore(CommandContext context,Map<String,Object> scope,int timeout,StaffCaseInstallation.Configuration staff) {
    if(staff!=null&&!scope.equals(staff.authScope()))throw Rejected.denied();
    this.staffConfiguration=staff;
    this.scope=PortalReadModels.copy(scope);
    Jcs.keys(scope,"tenant","environment","engine_name","database_incarnation","installation_ref","installation_revision");
    for(String key:List.of("tenant","environment","engine_name","database_incarnation","installation_ref"))Jcs.ref(scope,key);
    PortalReadModels.number(scope.get("installation_revision"));
    if(timeout<1||timeout>10 || !scope.get("engine_name").equals(context.getProcessEngineConfiguration().getProcessEngineName()))throw Rejected.denied();
    this.timeout=timeout;this.tenant=Jcs.ref(scope,"tenant");
    this.session=context.getDbSqlSession().getSqlSession();this.connection=session.getConnection();
    try {
      if(connection.getAutoCommit()||!"PostgreSQL".equals(connection.getMetaData().getDatabaseProductName()))throw Rejected.denied();
    } catch(SQLException failure){throw EngineStore.unavailable();}
  }

  Connection connection(){return connection;}

  /** Lock shared tenant before guide/instance/occurrence; publisher/revoker uses the same order. */
  void lock() {
    one("SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_=? FOR UPDATE",tenant);
    var installed=one("SELECT INCARNATION_,REV_,SCOPE_ FROM MZO_AUTH_INSTALLATION WHERE TENANT_=?",tenant);
    if(!scope.get("database_incarnation").equals(installed.get("incarnation_"))
        || PortalReadModels.number(scope.get("installation_revision"))!=((Number)installed.get("rev_")).longValue()
        || !scope.equals(parse(installed.get("scope_"))))throw Rejected.denied();
  }

  List<Map<String,Object>> rows(String sql,Object...args) {
    try(var statement=connection.prepareStatement(sql)) {
      statement.setQueryTimeout(timeout);statement.setMaxRows(3);
      for(int index=0;index<args.length;index++)statement.setObject(index+1,args[index]);
      try(var values=statement.executeQuery()) {
        var result=new ArrayList<Map<String,Object>>();
        while(values.next()) {
          if(result.size()==2)throw EngineStore.unavailable();
          var row=new HashMap<String,Object>();int bytes=0;
          for(int index=1;index<=values.getMetaData().getColumnCount();index++) {
            Object value=values.getObject(index);
            if(value instanceof String text)bytes=Math.addExact(bytes,text.getBytes(StandardCharsets.UTF_8).length);
            if(bytes>262144)throw EngineStore.unavailable();
            row.put(values.getMetaData().getColumnLabel(index).toLowerCase(Locale.ROOT),value);
          }
          result.add(row);
        }
        return result;
      }
    } catch(SQLException failure){throw EngineStore.unavailable();}
  }

  Map<String,Object> optional(String sql,Object...args) {
    var result=rows(sql,args);if(result.size()>1)throw EngineStore.unavailable();
    return result.isEmpty()?null:result.get(0);
  }
  Map<String,Object> one(String sql,Object...args) {
    var result=optional(sql,args);if(result==null)throw Rejected.denied();return result;
  }
  void write(String sql,Object...args) {
    // The existing dirty SELECT/RETURNING mapper enlists under CIB BATCH. No direct commit/flush.
    var count=session.<Integer>selectList(EnlistedWrites.ID,new EnlistedWrites.Write(sql,args.clone()));
    if(count.size()!=1)throw Rejected.conflict();
  }
  static Map<String,Object> parse(Object value) {
    if(!(value instanceof String text))throw EngineStore.unavailable();
    return Jcs.object(Jcs.parse(text.getBytes(StandardCharsets.UTF_8)));
  }
  static String text(Object value){return new String(Jcs.canonical(value),StandardCharsets.UTF_8);}
  static long next(long value){try{return Math.addExact(value,1);}catch(ArithmeticException failure){throw Rejected.conflict();}}

  Map<String,Object> publication(String id,String digest) {
    var found=optional("SELECT DIGEST_,RECEIPT_ FROM MZO_AUTH_INPUT_VERSION WHERE TENANT_=? AND PUBLICATION_=?",tenant,id);
    if(found==null)return null;if(!digest.equals(found.get("digest_")))throw Rejected.conflict();return parse(found.get("receipt_"));
  }
  Map<String,Object> inputHead(String kind,String resource) {
    return optional("SELECT * FROM MZO_AUTH_INPUT_HEAD WHERE TENANT_=? AND KIND_=? AND RESOURCE_=? FOR UPDATE",tenant,kind,resource);
  }
  /** Caller authenticates the exact producer/payload and source barrier before advancing this head. */
  void publish(Map<String,Object> publication,String digest,Map<String,Object> receipt,AuthTrust.Key publisher) {
    String kind=Jcs.string(publication,"kind"),resource=Jcs.ref(publication,"resource_ref"),id=Jcs.ref(publication,"publication_id");
    long previous=PortalReadModels.number(publication.get("expected_generation")),generation=next(previous);
    String state=Jcs.string(publication,"state");Object payload=publication.get("payload"),hash=publication.get("payload_digest");
    var existing=inputHead(kind,resource);
    if(existing==null && previous!=0 || existing!=null && ((Number)existing.get("generation_")).longValue()!=previous)throw Rejected.conflict();
    Object[] values={generation,state,id,digest,text(publication.get("source")),payload==null?null:text(payload),hash,
      Timestamp.from(PortalReadModels.time(publication.get("valid_until"))),publisher.id(),publisher.designationDigest(),tenant,kind,resource};
    if(existing==null) {
      write("INSERT INTO MZO_AUTH_INPUT_HEAD(GENERATION_,STATE_,PUBLICATION_,PUBLICATION_DIGEST_,SOURCE_,PAYLOAD_,PAYLOAD_DIGEST_,VALID_UNTIL_,PUBLISHER_KEY_,PUBLISHER_DIGEST_,TENANT_,KIND_,RESOURCE_) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",values);
    } else {
      var update=Arrays.copyOf(values,values.length+1);update[values.length]=previous;
      write("UPDATE MZO_AUTH_INPUT_HEAD SET GENERATION_=?,STATE_=?,PUBLICATION_=?,PUBLICATION_DIGEST_=?,SOURCE_=?,PAYLOAD_=?,PAYLOAD_DIGEST_=?,VALID_UNTIL_=?,PUBLISHER_KEY_=?,PUBLISHER_DIGEST_=? WHERE TENANT_=? AND KIND_=? AND RESOURCE_=? AND GENERATION_=?",update);
    }
    write("INSERT INTO MZO_AUTH_INPUT_VERSION(TENANT_,KIND_,RESOURCE_,GENERATION_,PUBLICATION_,DIGEST_,REQUEST_,RECEIPT_,PUBLISHER_KEY_,PUBLISHER_DIGEST_) VALUES(?,?,?,?,?,?,?,?,?,?)",tenant,kind,resource,generation,id,digest,text(publication),text(receipt),publisher.id(),publisher.designationDigest());
  }

  Map<String,Object> guide(String guide) {
    // No FOR UPDATE: the runtime role has SELECT,INSERT only (D-J.4 matrix); the tenant row lock taken by
    // lock() serializes starts and PK(TENANT_,GUIDE_) refuses a racing second claim.
    return optional("SELECT * FROM MZO_AUTH_GUIDE_CLAIM WHERE TENANT_=? AND GUIDE_=?",tenant,guide);
  }
  Map<String,Object> caseLink(String caseRef) {
    return optional("SELECT * FROM MZO_AUTH_GUIDE_CLAIM WHERE TENANT_=? AND CASE_=?",tenant,caseRef);
  }
  Map<String,Object> instanceLink(String instance) {
    return optional("SELECT * FROM MZO_AUTH_GUIDE_CLAIM WHERE TENANT_=? AND INSTANCE_=?",tenant,instance);
  }
  void claimGuide(String guide,String intake,String command,String digest,String principal,String instance,String caseRef,Map<String,Object> definition) {
    write("INSERT INTO MZO_AUTH_GUIDE_CLAIM(TENANT_,GUIDE_,INTAKE_,COMMAND_,DIGEST_,PRINCIPAL_,INSTANCE_,CASE_,DEFINITION_) VALUES(?,?,?,?,?,?,?,?,?)",tenant,guide,intake,command,digest,principal,instance,caseRef,text(definition));
    write("INSERT INTO MZO_AUTH_INSTANCE_HEAD(TENANT_,INSTANCE_,REV_,GENERATION_,CURRENT_REQUEST_) VALUES(?,?,0,0,NULL)",tenant,instance);
    // The actual guide and instance claims have succeeded in this enlisted TX.
    // No historical receipt replay, guessed case ID or reader initializes this head.
    if(staffConfiguration!=null)new StaffCaseEventStore(this).initializeClaim(caseRef);
  }
  Map<String,Object> instanceHead(String instance) {
    return one("SELECT * FROM MZO_AUTH_INSTANCE_HEAD WHERE TENANT_=? AND INSTANCE_=? FOR UPDATE",tenant,instance);
  }
  Map<String,Object> occurrence(String request) {
    return optional("SELECT * FROM MZO_AUTH_DOC_OCCURRENCE WHERE TENANT_=? AND REQUEST_=? FOR UPDATE",tenant,request);
  }
  Map<String,Object> producerOccurrence(String instance,String externalTask) {
    return optional("SELECT * FROM MZO_AUTH_DOC_OCCURRENCE WHERE TENANT_=? AND INSTANCE_=? AND PRODUCER_TASK_=? FOR UPDATE",tenant,instance,externalTask);
  }
  Map<String,Object> publicationOccurrence(String externalTask) {
    return optional("SELECT * FROM MZO_AUTH_DOC_OCCURRENCE WHERE TENANT_=? AND PUBLICATION_TASK_=? FOR UPDATE",tenant,externalTask);
  }
  void openOccurrence(Map<String,Object> record,long expectedHeadRevision) {
    String instance=Jcs.ref(record,"process_instance_id"),request=Jcs.ref(record,"request_ref");
    long generation=PortalReadModels.number(record.get("generation"));
    write("INSERT INTO MZO_AUTH_DOC_OCCURRENCE(TENANT_,REQUEST_,INSTANCE_,GENERATION_,REV_,PRODUCER_TASK_,STATE_,RECORD_) VALUES(?,?,?,?,0,?,'created',?)",tenant,request,instance,generation,Jcs.ref(record,"producer_external_task_id"),text(record));
    write("UPDATE MZO_AUTH_INSTANCE_HEAD SET REV_=?,GENERATION_=?,CURRENT_REQUEST_=? WHERE TENANT_=? AND INSTANCE_=? AND REV_=?",next(expectedHeadRevision),generation,request,tenant,instance,expectedHeadRevision);
    if(staffConfiguration!=null)new StaffCaseEventStore(this).advance(instance);
  }
  void updateOccurrence(Map<String,Object> record,long expectedRevision) {
    var binding=record.get("binding");String subscription=binding==null?null:Jcs.ref(Jcs.object(binding),"subscription_id");
    write("UPDATE MZO_AUTH_DOC_OCCURRENCE SET REV_=?,PUBLICATION_TASK_=?,SUBSCRIPTION_=?,STATE_=?,RECORD_=? WHERE TENANT_=? AND REQUEST_=? AND REV_=?",next(expectedRevision),record.get("publication_external_task_id"),subscription,Jcs.string(record,"state"),text(record),tenant,Jcs.ref(record,"request_ref"),expectedRevision);
    if(staffConfiguration!=null)new StaffCaseEventStore(this).advance(Jcs.ref(record,"process_instance_id"));
  }
  Map<String,Object> effectReceipt(String command,String digest) {
    var row=optional("SELECT DIGEST_,RECEIPT_ FROM MZO_AUTH_EFFECT_RECEIPT WHERE TENANT_=? AND COMMAND_=?",tenant,command);
    if(row==null)return null;if(!digest.equals(row.get("digest_")))throw Rejected.conflict();return parse(row.get("receipt_"));
  }
  void saveEffectReceipt(Map<String,Object> receipt) {
    write("INSERT INTO MZO_AUTH_EFFECT_RECEIPT(TENANT_,COMMAND_,DIGEST_,OPERATION_,PRINCIPAL_,ADMISSION_,REQUEST_,RECEIPT_) VALUES(?,?,?,?,?,?,?,?)",tenant,Jcs.ref(receipt,"command_id"),Jcs.hash(receipt,"command_digest"),Jcs.string(receipt,"operation"),Jcs.ref(receipt,"actor_principal_ref"),Jcs.ref(receipt,"admission_ref"),receipt.get("request_ref"),text(receipt));
  }
  boolean revoked(String key) {
    return optional("SELECT REV_ FROM MZO_AUTH_REVOKED_KEY WHERE TENANT_=? AND KEY_ID_=?",tenant,key)!=null;
  }
}

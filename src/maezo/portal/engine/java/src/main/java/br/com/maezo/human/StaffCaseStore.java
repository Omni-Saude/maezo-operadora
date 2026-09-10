package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.sql.*;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** SC1 fixed SQL against the actual enlisted engine connection; no second case DB. */
final class StaffCaseStore {
  static final String S="tenant=? AND environment=? AND engine_name=? AND database_incarnation=?";
  static final Set<String> OWNED=Set.of("mzo_staff_case_designation_event","mzo_staff_case_designation_current",
    "mzo_staff_case_source_event","mzo_staff_case_source_head","mzo_staff_case_publication_receipt",
    "mzo_staff_case_grant","mzo_staff_case_continuity","mzo_staff_native_event_head",
    "mzo_staff_case_policy_version","mzo_staff_case_policy_current","mzo_staff_case_policy_dependency");
  record RelationPin(long oid,String owner) {
    RelationPin {if(oid<1||owner==null||!owner.matches("[A-Za-z_][A-Za-z0-9_]{0,62}"))throw unavailable();}
  }
  final org.apache.ibatis.session.SqlSession session;
  final AuthStore auth;
  final Map<String,Object> scope;
  final int timeout;
  StaffCaseStore(CommandContext context,Map<String,Object> authScope,int timeout,
      String nativeRole,Map<String,RelationPin> pins) {
    auth=new AuthStore(context,authScope,timeout);this.timeout=timeout;
    session=context.getDbSqlSession().getSqlSession();
    scope=record("tenant",authScope.get("tenant"),"environment",authScope.get("environment"),
      "engine_name",authScope.get("engine_name"),"database_incarnation",authScope.get("database_incarnation"));
    if(!pins.keySet().equals(OWNED))throw unavailable();
    String schema=context.getProcessEngineConfiguration().getDatabaseSchema();
    String prefix=context.getProcessEngineConfiguration().getDatabaseTablePrefix();
    if(schema!=null&&!schema.equals("public")||prefix!=null&&!prefix.isEmpty()&&!prefix.equals("public."))throw unavailable();
    var user=auth.one("SELECT session_user::text AS actual,current_user::text AS effective");
    if(!nativeRole.equals(user.get("actual"))||!nativeRole.equals(user.get("effective")))throw unavailable();
    for(String table:new TreeSet<>(OWNED)) {
      var pin=pins.get(table);
      var row=auth.one("""
        SELECT c.oid::bigint AS oid,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
          pg_get_userbyid(c.relowner) AS owner,
          pg_has_role(session_user,c.relowner,'MEMBER') AS owner_member,
          has_table_privilege(session_user,c.oid,'SELECT') AS can_read
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=?
        """,table);
      if(((Number)row.get("oid")).longValue()!=pin.oid()||!pin.owner().equals(row.get("owner"))
          ||!"r".equals(row.get("relkind"))||!Boolean.FALSE.equals(row.get("relrowsecurity"))
          ||!Boolean.FALSE.equals(row.get("relforcerowsecurity"))||!Boolean.FALSE.equals(row.get("owner_member"))
          ||!Boolean.TRUE.equals(row.get("can_read")))throw unavailable();
      boolean immutable=table.endsWith("_event")||table.endsWith("_receipt")||table.endsWith("_continuity")||table.endsWith("_cursor")||table.endsWith("_version")||table.endsWith("_dependency");
      boolean installed=table.startsWith("mzo_staff_case_designation_");
      var writes=auth.one("""
        SELECT has_table_privilege(session_user,CAST(? AS oid),'INSERT') AS ins,
          has_table_privilege(session_user,CAST(? AS oid),'UPDATE') AS upd,
          has_table_privilege(session_user,CAST(? AS oid),'DELETE') AS del,
          has_table_privilege(session_user,CAST(? AS oid),'TRUNCATE') AS trunc
        """,pin.oid(),pin.oid(),pin.oid(),pin.oid());
      if(!Boolean.FALSE.equals(writes.get("del"))||!Boolean.FALSE.equals(writes.get("trunc"))
          ||!Boolean.valueOf(!installed).equals(writes.get("ins"))
          ||!Boolean.valueOf(!installed&&!immutable).equals(writes.get("upd")))throw unavailable();
    }
  }
  Object[] args(Object...rest){var all=new Object[rest.length+4];int i=0;
    for(String k:List.of("tenant","environment","engine_name","database_incarnation"))all[i++]=scope.get(k);
    System.arraycopy(rest,0,all,4,rest.length);return all;}
  Map<String,Object> one(String sql,Object...args){return auth.one(sql,args);}
  Map<String,Object> optional(String sql,Object...args){return auth.optional(sql,args);}
  void write(String sql,Object...args){auth.write(sql,args);}
  Map<String,Object> designation() {
    return one("SELECT e.canonical_designation,e.installation_proof,c.designation_digest,c.designation_revision FROM mzo_staff_case_designation_current c JOIN mzo_staff_case_designation_event e USING(tenant,environment,engine_name,database_incarnation,designation_revision) WHERE c.tenant=? AND c.environment=? AND c.engine_name=? AND c.database_incarnation=? AND c.designation_digest=e.designation_digest FOR UPDATE OF c",args());
  }
  Map<String,Object> sourceHead(String source){return optional("SELECT * FROM mzo_staff_case_source_head WHERE "+S+" AND source_ref=? FOR UPDATE",args(source));}
  Map<String,Object> grant(String ref){return optional("SELECT * FROM mzo_staff_case_grant WHERE "+S+" AND grant_ref=? FOR UPDATE",args(ref));}
  Map<String,Object> peekGrant(String caseRef,Map<String,Object> principal){
    return optional("SELECT * FROM mzo_staff_case_grant WHERE "+S+" AND case_ref=? AND issuer=? AND subject=? AND principal_ref=? AND membership_revision=? AND state='active' AND effective ORDER BY grant_ref COLLATE \"C\"",args(caseRef,principal.get("issuer"),principal.get("subject"),principal.get("principal_ref"),number(principal.get("membership_revision"))));
  }
  Map<String,Object> exactGrant(String caseRef,Map<String,Object> principal){
    return optional("SELECT * FROM mzo_staff_case_grant WHERE "+S+" AND case_ref=? AND issuer=? AND subject=? AND principal_ref=? AND membership_revision=? AND state='active' AND effective ORDER BY grant_ref COLLATE \"C\" FOR UPDATE",args(caseRef,principal.get("issuer"),principal.get("subject"),principal.get("principal_ref"),number(principal.get("membership_revision"))));
  }
  Map<String,Object> publication(String id){var row=optional("SELECT canonical_publication FROM mzo_staff_case_source_event WHERE "+S+" AND publication_id=?",args(id));return row==null?null:AuthStore.parse(row.get("canonical_publication"));}
  Map<String,Object> receipt(String id,String digest){var row=optional("SELECT request_digest,canonical_receipt FROM mzo_staff_case_publication_receipt WHERE "+S+" AND publication_id=?",args(id));
    if(row==null)return null;if(!digest.equals(row.get("request_digest")))throw conflict();return AuthStore.parse(row.get("canonical_receipt"));}
  boolean revoked(String fingerprint){return optional("SELECT fingerprint_ FROM mzo_portal_read_revocation WHERE tenant_=? AND environment_=? AND engine_=? AND incarnation_=? AND fingerprint_=?",args(fingerprint))!=null;}

  /** Caller verifies exact independently signed request, policy/membership, retained
   * native identity and current installation BEFORE invoking this same-TX CAS. */
  void publish(Map<String,Object> request,Map<String,Object> receipt) {
    StaffCaseModels.publication(request);var source=str(request,"source_ref");
    var payload=obj(request,"payload");String kind=str(request,"kind");
    // Sorted policy locks precede source/grant locks. No caller-controlled affected list.
    var dependencies=kind.equals("case_grant")?policyPins(payload):List.<Map<String,Object>>of();
    var priorPolicy=kind.equals("policy_head")?policyHead(str(payload,"policy_ref")):null;
    long expected=number(request.get("expected_source_revision")),next=number(request.get("source_revision"));
    var head=sourceHead(source);
    if(head==null&&expected!=0||head!=null&&((Number)head.get("source_revision")).longValue()!=expected)throw conflict();
    if(head==null)write("INSERT INTO mzo_staff_case_source_head(tenant,environment,engine_name,database_incarnation,source_ref,source_revision) VALUES(?,?,?,?,?,?)",args(source,next));
    else write("UPDATE mzo_staff_case_source_head SET source_revision=? WHERE "+S+" AND source_ref=? AND source_revision=?",prepend(next,args(source,expected)));
    String id=str(request,"publication_id"),requestDigest=hash(request);
    write("INSERT INTO mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,source_ref,source_revision,publication_id,request_digest,canonical_publication) VALUES(?,?,?,?,?,?,?,?,?)",args(source,next,id,requestDigest,AuthStore.text(request)));
    if("case_grant".equals(request.get("kind"))) {
      var old=grant(str(payload,"grant_ref"));long revision=number(payload.get("grant_revision"));
      if(old==null&&revision!=1||old!=null&&revision!=Math.addExact(((Number)old.get("grant_revision")).longValue(),1))throw conflict();
      if(old!=null){
        if(!source.equals(old.get("source_ref")))throw denied();
        for(String field:List.of("case_ref","issuer","subject","principal_ref","identity_digest"))
          if(!payload.get(field).equals(old.get(field)))throw conflict();
      }
      Object[] values={revision,id,source,payload.get("case_ref"),payload.get("issuer"),payload.get("subject"),payload.get("principal_ref"),number(payload.get("membership_revision")),payload.get("state"),payload.get("identity_digest")};
      if(old==null)write("INSERT INTO mzo_staff_case_grant(grant_revision,publication_id,source_ref,case_ref,issuer,subject,principal_ref,membership_revision,state,identity_digest,effective,tenant,environment,engine_name,database_incarnation,grant_ref) VALUES(?,?,?,?,?,?,?,?,?,?,true,?,?,?,?,?)",concat(values,args(payload.get("grant_ref"))));
      else write("UPDATE mzo_staff_case_grant SET grant_revision=?,publication_id=?,source_ref=?,case_ref=?,issuer=?,subject=?,principal_ref=?,membership_revision=?,state=?,identity_digest=?,effective=true WHERE "+S+" AND grant_ref=? AND grant_revision=?",concat(values,args(payload.get("grant_ref"),revision-1)));
      for(var pin:dependencies)write("INSERT INTO mzo_staff_case_policy_dependency(tenant,environment,engine_name,database_incarnation,grant_ref,grant_revision,policy_ref,head_revision,head_digest) VALUES(?,?,?,?,?,?,?,?,?)",args(payload.get("grant_ref"),revision,pin.get("policy_ref"),number(pin.get("head_revision")),pin.get("head_digest")));
    }else if(kind.equals("policy_head")){
      publishPolicy(payload,priorPolicy,id);
    }else{
      var old=grant(str(payload,"target_ref"));long expectedGrant=number(payload.get("expected_revision"));
      if(old==null||!source.equals(old.get("source_ref"))||((Number)old.get("grant_revision")).longValue()!=expectedGrant)throw conflict();
      write("UPDATE mzo_staff_case_grant SET state='revoked',effective=false,grant_revision=?,publication_id=? WHERE "+S+" AND grant_ref=? AND grant_revision=?",concat(new Object[]{Math.addExact(expectedGrant,1),id},args(payload.get("target_ref"),expectedGrant)));
    }
    write("INSERT INTO mzo_staff_case_publication_receipt(tenant,environment,engine_name,database_incarnation,publication_id,request_digest,canonical_receipt) VALUES(?,?,?,?,?,?,?)",args(id,requestDigest,AuthStore.text(receipt)));
  }

  Map<String,Object> policyHead(String ref) {
    return optional("SELECT v.canonical_head,v.head_digest,c.head_revision FROM mzo_staff_case_policy_current c JOIN mzo_staff_case_policy_version v USING(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision) WHERE c.tenant=? AND c.environment=? AND c.engine_name=? AND c.database_incarnation=? AND c.policy_ref=? AND c.head_digest=v.head_digest FOR UPDATE OF c",args(ref));
  }
  List<Map<String,Object>> policyPins(Map<String,Object> grant) {
    var wanted=new TreeMap<String,Map<String,Object>>();
    for(Object value:list(grant.get("decisions"))){var d=map(value);String ref=str(d,"policy_ref");
      var prior=wanted.putIfAbsent(ref,d);if(prior!=null&&(!prior.get("policy_revision").equals(d.get("policy_revision"))
        ||!prior.get("policy_digest").equals(d.get("policy_digest"))))throw denied();}
    var result=new ArrayList<Map<String,Object>>();
    for(var entry:wanted.entrySet()){
      var row=policyHead(entry.getKey());if(row==null)throw unavailable();
      var head=StaffCaseModels.shape("policy_head",AuthStore.parse(row.get("canonical_head")));var d=entry.getValue();
      if(!"active".equals(head.get("state"))||!d.get("policy_revision").equals(head.get("policy_revision"))
          ||!d.get("policy_digest").equals(head.get("policy_digest"))
          ||!obj(d,"decision_proof").get("key_fingerprint").equals(head.get("decision_issuer_key_fingerprint"))
          ||!obj(d,"decision_proof").get("purpose").equals(head.get("decision_purpose")))throw denied();
      if(!hash(head).equals(row.get("head_digest")))throw unavailable();
      result.add(record("policy_ref",head.get("policy_ref"),"policy_revision",head.get("policy_revision"),
        "policy_digest",head.get("policy_digest"),"head_revision",head.get("head_revision"),"head_digest",hash(head),"head",head));
    }
    return result;
  }
  void publishPolicy(Map<String,Object> head,Map<String,Object> prior,String publication) {
    long expected=number(head.get("expected_head_revision")),next=number(head.get("head_revision"));
    if(prior==null&&expected!=0||prior!=null&&((Number)prior.get("head_revision")).longValue()!=expected)throw conflict();
    if(prior==null&&!"active".equals(head.get("state")))throw conflict();
    if(prior!=null){var old=AuthStore.parse(prior.get("canonical_head"));
      if(!head.get("source_ref").equals(old.get("source_ref"))||!head.get("decision_issuer_key_fingerprint").equals(old.get("decision_issuer_key_fingerprint"))
          ||!head.get("decision_purpose").equals(old.get("decision_purpose")))throw denied();
      if("revoked".equals(head.get("state"))){if(!"active".equals(old.get("state"))||!head.get("policy_revision").equals(old.get("policy_revision"))
          ||!head.get("policy_digest").equals(old.get("policy_digest")))throw conflict();}
      else if(number(head.get("policy_revision"))<=number(old.get("policy_revision")))throw conflict();
    }
    String ref=str(head,"policy_ref"),headDigest=hash(head);
    write("INSERT INTO mzo_staff_case_policy_version(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest,publication_id,canonical_head) VALUES(?,?,?,?,?,?,?,?,?)",args(ref,next,headDigest,publication,AuthStore.text(head)));
    if(prior==null)write("INSERT INTO mzo_staff_case_policy_current(tenant,environment,engine_name,database_incarnation,policy_ref,head_revision,head_digest) VALUES(?,?,?,?,?,?,?)",args(ref,next,headDigest));
    else write("UPDATE mzo_staff_case_policy_current SET head_revision=?,head_digest=? WHERE "+S+" AND policy_ref=? AND head_revision=?",concat(new Object[]{next,headDigest},args(ref,expected)));
    // Entire dependency fan-out is one enlisted statement/transaction. No paginated
    // partial revocation. This slice admits no checkpoints/document/history grants.
    ExternalCaseStore.CountedWrites.install(session.getConfiguration());
    session.selectOne(ExternalCaseStore.CountedWrites.ID,new EnlistedWrites.Write(
      "UPDATE mzo_staff_case_grant g SET effective=false WHERE g.tenant=? AND g.environment=? AND g.engine_name=? AND g.database_incarnation=? AND EXISTS(SELECT 1 FROM mzo_staff_case_policy_dependency d WHERE d.tenant=g.tenant AND d.environment=g.environment AND d.engine_name=g.engine_name AND d.database_incarnation=g.database_incarnation AND d.grant_ref=g.grant_ref AND d.grant_revision=g.grant_revision AND d.policy_ref=?)",args(ref)));
  }
  void requireRecordedPolicies(Map<String,Object> grantRow,List<Map<String,Object>> currentPins) {
    // Derive expected closure from the immutable full payload before calling this.
    for(var pin:currentPins){var row=optional("SELECT head_digest FROM mzo_staff_case_policy_dependency WHERE "+S+" AND grant_ref=? AND grant_revision=? AND policy_ref=? AND head_revision=?",args(grantRow.get("grant_ref"),grantRow.get("grant_revision"),pin.get("policy_ref"),number(pin.get("head_revision"))));
      if(row==null||!pin.get("head_digest").equals(row.get("head_digest")))throw unavailable();}
    var count=one("SELECT count(*) AS n FROM mzo_staff_case_policy_dependency WHERE "+S+" AND grant_ref=? AND grant_revision=?",args(grantRow.get("grant_ref"),grantRow.get("grant_revision")));
    if(((Number)count.get("n")).longValue()!=currentPins.size()||currentPins.isEmpty())throw unavailable();
  }
  void taskRows(Map<String,Object> identity,java.util.function.Predicate<Map<String,Object>> visitor) {
    String sql="SELECT ID_,PROC_INST_ID_,PROC_DEF_ID_,TASK_DEF_KEY_,REV_,CREATE_TIME_,TENANT_ID_ FROM public.ACT_RU_TASK WHERE TENANT_ID_=? AND PROC_INST_ID_=? ORDER BY ID_ COLLATE \"C\" FOR SHARE";
    try(var statement=auth.connection().prepareStatement(sql)){
      statement.setQueryTimeout(timeout);statement.setFetchSize(16);statement.setString(1,str(scope,"tenant"));statement.setString(2,str(identity,"process_instance_ref"));
      try(var rs=statement.executeQuery()){while(rs.next()){
        var row=new HashMap<String,Object>();for(int i=1;i<=rs.getMetaData().getColumnCount();i++)row.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),rs.getObject(i));
        if(!visitor.test(row))break;
      }}
    }catch(SQLException failure){throw unavailable();}
  }
  Map<String,Object> continuity(String ref){var row=optional("SELECT continuity_digest,canonical_continuity FROM mzo_staff_case_continuity WHERE "+S+" AND continuity_ref=?",args(ref));
    if(row==null)throw conflict();var value=AuthStore.parse(row.get("canonical_continuity"));if(!hash(value).equals(row.get("continuity_digest")))throw unavailable();return value;}
  void continuity(String ref,Map<String,Object> value){if(Jcs.canonical(value).length>MAX)throw unavailable();
    write("INSERT INTO mzo_staff_case_continuity(tenant,environment,engine_name,database_incarnation,continuity_ref,continuity_digest,canonical_continuity) VALUES(?,?,?,?,?,?,?)",args(ref,hash(value),AuthStore.text(value)));}
  static Object[] prepend(Object first,Object[] rest){return concat(new Object[]{first},rest);}
  static Object[] concat(Object[] first,Object[] second){var all=Arrays.copyOf(first,first.length+second.length);System.arraycopy(second,0,all,first.length,second.length);return all;}
}

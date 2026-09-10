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
    "mzo_staff_case_grant","mzo_staff_case_continuity","mzo_staff_case_cursor","mzo_staff_native_event_head");
  record RelationPin(long oid,String owner) {
    RelationPin {if(oid<1||owner==null||!owner.matches("[A-Za-z_][A-Za-z0-9_]{0,62}"))throw unavailable();}
  }
  final AuthStore auth;
  final Map<String,Object> scope;
  final int timeout;
  StaffCaseStore(CommandContext context,Map<String,Object> authScope,int timeout,
      String nativeRole,Map<String,RelationPin> pins) {
    auth=new AuthStore(context,authScope,timeout);this.timeout=timeout;
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
      boolean immutable=table.endsWith("_event")||table.endsWith("_receipt")||table.endsWith("_continuity")||table.endsWith("_cursor");
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
  Map<String,Object> exactGrant(String caseRef,Map<String,Object> principal){
    return optional("SELECT * FROM mzo_staff_case_grant WHERE "+S+" AND case_ref=? AND issuer=? AND subject=? AND principal_ref=? AND membership_revision=? AND state='active' ORDER BY grant_ref COLLATE \"C\" FOR UPDATE",args(caseRef,principal.get("issuer"),principal.get("subject"),principal.get("principal_ref"),number(principal.get("membership_revision"))));
  }
  Map<String,Object> publication(String id){var row=optional("SELECT canonical_publication FROM mzo_staff_case_source_event WHERE "+S+" AND publication_id=?",args(id));return row==null?null:AuthStore.parse(row.get("canonical_publication"));}
  Map<String,Object> receipt(String id,String digest){var row=optional("SELECT request_digest,canonical_receipt FROM mzo_staff_case_publication_receipt WHERE "+S+" AND publication_id=?",args(id));
    if(row==null)return null;if(!digest.equals(row.get("request_digest")))throw conflict();return AuthStore.parse(row.get("canonical_receipt"));}
  boolean revoked(String fingerprint){return optional("SELECT fingerprint_ FROM mzo_portal_read_revocation WHERE tenant_=? AND environment_=? AND engine_=? AND incarnation_=? AND fingerprint_=?",args(fingerprint))!=null;}

  /** Caller verifies exact independently signed request, policy/membership, retained
   * native identity and current installation BEFORE invoking this same-TX CAS. */
  void publish(Map<String,Object> request,Map<String,Object> receipt) {
    StaffCaseModels.publication(request);var source=str(request,"source_ref");
    long expected=number(request.get("expected_source_revision")),next=number(request.get("source_revision"));
    var head=sourceHead(source);
    if(head==null&&expected!=0||head!=null&&((Number)head.get("source_revision")).longValue()!=expected)throw conflict();
    if(head==null)write("INSERT INTO mzo_staff_case_source_head(tenant,environment,engine_name,database_incarnation,source_ref,source_revision) VALUES(?,?,?,?,?,?)",args(source,next));
    else write("UPDATE mzo_staff_case_source_head SET source_revision=? WHERE "+S+" AND source_ref=? AND source_revision=?",prepend(next,args(source,expected)));
    String id=str(request,"publication_id"),requestDigest=hash(request);var payload=obj(request,"payload");
    write("INSERT INTO mzo_staff_case_source_event(tenant,environment,engine_name,database_incarnation,source_ref,source_revision,publication_id,request_digest,canonical_publication) VALUES(?,?,?,?,?,?,?,?,?)",args(source,next,id,requestDigest,AuthStore.text(request)));
    if("case_grant".equals(request.get("kind"))) {
      var old=grant(str(payload,"grant_ref"));long revision=number(payload.get("grant_revision"));
      if(old==null&&revision!=1||old!=null&&revision!=Math.addExact(((Number)old.get("grant_revision")).longValue(),1))throw conflict();
      if(old!=null&&!source.equals(old.get("source_ref")))throw denied();
      Object[] values={revision,id,source,payload.get("case_ref"),payload.get("issuer"),payload.get("subject"),payload.get("principal_ref"),number(payload.get("membership_revision")),payload.get("state"),payload.get("identity_digest")};
      if(old==null)write("INSERT INTO mzo_staff_case_grant(grant_revision,publication_id,source_ref,case_ref,issuer,subject,principal_ref,membership_revision,state,identity_digest,tenant,environment,engine_name,database_incarnation,grant_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",concat(values,args(payload.get("grant_ref"))));
      else write("UPDATE mzo_staff_case_grant SET grant_revision=?,publication_id=?,source_ref=?,case_ref=?,issuer=?,subject=?,principal_ref=?,membership_revision=?,state=?,identity_digest=? WHERE "+S+" AND grant_ref=? AND grant_revision=?",concat(values,args(payload.get("grant_ref"),revision-1)));
    }else{
      var old=grant(str(payload,"target_ref"));long expectedGrant=number(payload.get("expected_revision"));
      if(old==null||!source.equals(old.get("source_ref"))||((Number)old.get("grant_revision")).longValue()!=expectedGrant)throw conflict();
      write("UPDATE mzo_staff_case_grant SET state='revoked',grant_revision=?,publication_id=? WHERE "+S+" AND grant_ref=? AND grant_revision=?",concat(new Object[]{Math.addExact(expectedGrant,1),id},args(payload.get("target_ref"),expectedGrant)));
    }
    write("INSERT INTO mzo_staff_case_publication_receipt(tenant,environment,engine_name,database_incarnation,publication_id,request_digest,canonical_receipt) VALUES(?,?,?,?,?,?,?)",args(id,requestDigest,AuthStore.text(receipt)));
  }
  static Object[] prepend(Object first,Object[] rest){return concat(new Object[]{first},rest);}
  static Object[] concat(Object[] first,Object[] second){var all=Arrays.copyOf(first,first.length+second.length);System.arraycopy(second,0,all,first.length,second.length);return all;}
}

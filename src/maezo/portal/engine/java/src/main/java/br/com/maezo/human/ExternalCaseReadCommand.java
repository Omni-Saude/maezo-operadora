package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static br.com.maezo.human.ExternalCaseModels.*;
import static br.com.maezo.human.ExternalCaseStore.numberColumn;

import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Own-case list/detail and private finalization. Identity lease is retained by gateway;
 * native code never opens the identity database or enters staff task authorization. */
public final class ExternalCaseReadCommand implements Command<ExternalCaseReadCommand.Result> {
  public static final class Result {
    private byte[] bytes;private boolean committed;private Runnable guard;
    public byte[] bytes(){if(!committed||bytes==null)throw unavailable();guard.run();return bytes.clone();}
  }
  private final Verified verified;private final PortalReadTrust membershipTrust;
  private final PortalReadTrust.Admission membershipAdmission;private final PortalReadTrust.NativeKeySet keys;
  public ExternalCaseReadCommand(Verified v,PortalReadTrust membershipTrust,
      PortalReadTrust.Admission membershipAdmission,PortalReadTrust.NativeKeySet keys){
    if(v==null||membershipTrust==null||membershipAdmission==null||keys==null)throw unavailable();
    this.verified=v;this.membershipTrust=membershipTrust;this.membershipAdmission=membershipAdmission;this.keys=keys;
  }
  @Override public Result execute(CommandContext context){
    verified.current();membershipAdmission.requireCurrent();keys.requireCurrent();
    if(!verified.configuration.purpose().equals("portal-external-case-read"))throw denied();
    var r=verified.request;Jcs.keys(r,"schema","scope","request_id","principal","audience","session_valid_until","operation","query","continuity_proof","public_projection_digest");
    if(!"portal-external-case-read-request.v1".equals(r.get("schema")))throw invalid();
    check("r",r.get("request_id"));check("beneficiary|provider",r.get("audience"));
    check("t",r.get("session_valid_until"));
    if(!Instant.now().isBefore(time(r.get("session_valid_until"))))throw denied();
    var principal=validate("principal",r.get("principal"));
    if(!principal.get("tenant").equals(verified.configuration.scope().get("tenant"))
        ||!membershipTrust.scope.get("tenant").equals(principal.get("tenant"))
        ||!membershipTrust.scope.get("environment").equals(verified.configuration.scope().get("environment"))
        ||!membershipTrust.engine.equals(verified.configuration.scope().get("engine_name"))
        ||!membershipTrust.incarnation.equals(verified.configuration.scope().get("database_incarnation")))throw denied();
    boolean finalize="finalize".equals(r.get("operation"));Map<String,Object> previous=null;Map<String,Object> query;
    Instant observed=Instant.now();
    if(finalize){if(r.get("query")!=null)throw invalid();check("h",r.get("public_projection_digest"));
      previous=verifyToken(r.get("continuity_proof"),"external-case-finalize",MAX);query=obj(previous,"query");observed=time(previous.get("observed_at"));
      if(!hash(principal).equals(previous.get("principal_digest"))||!r.get("audience").equals(previous.get("audience"))||!r.get("scope").equals(previous.get("scope")))throw conflict();
    }else{if(!Set.of("list","detail").contains(r.get("operation"))||r.get("continuity_proof")!=null||r.get("public_projection_digest")!=null)throw invalid();query=map(r.get("query"));}
    String operation=finalize?str(previous,"operation"):str(r,"operation");validateQuery(query,operation,r.get("audience"));
    var db=new ExternalCaseStore(context,verified.configuration.scope(),verified.admission.statementTimeoutSeconds());long authorityRevision=db.lock();
    var authority=db.authority(verified.configuration);if(authority.revoked.contains(verified.configuration.transportFingerprint()))throw denied();
    var memberDb=new PortalReadStore(context,membershipTrust,membershipAdmission.statementTimeoutSeconds());
    var membership=externalMembership(memberDb,principal,r.get("audience"));
    Map<String,Object> checkpoint=currentCheckpoint(db,authority);
    Instant until=min(observed.plusSeconds(10),authority.until(),time(membership.get("valid_until")),verified.until,verified.admission.validUntil(),membershipAdmission.validUntil(),time(r.get("session_valid_until")));
    var g=db.generation();var anchor=record("scope",r.get("scope"),"principal_digest",hash(principal),"audience",r.get("audience"),"membership_digest",hash(membership),"authority_revision",Long.toString(authorityRevision),"designation_digest",authority.designation,"accepted_generation",g.get("accepted_generation").toString(),"published_generation",g.get("published_generation").toString(),"checkpoint_ref",checkpoint.get("checkpoint_ref"),"checkpoint_epoch",checkpoint.get("epoch"),"checkpoint_digest",checkpoint.get("checkpoint_digest"));
    if(finalize&&!anchor.equals(previous.get("anchor")))throw conflict();
    String after="";long limit=operation.equals("list")?number(query.get("limit")):1;
    if(operation.equals("list")&&query.get("cursor")!=null){var cursor=verifyToken(query.get("cursor"),"external-case-cursor",2048);
      if(!anchor.equals(cursor.get("anchor"))||!Objects.equals(query.get("kind"),cursor.get("kind"))||!query.get("limit").equals(cursor.get("limit")))throw conflict();
      after=str(cursor,"last_case_ref");until=min(until,time(cursor.get("valid_until")));}
    var sources=new ArrayList<Map<String,Object>>();var grants=new ArrayList<Object>();
    for(var row:db.rows("SELECT DISTINCT c.case_ref COLLATE \"C\" AS case_ref,c.source_ref,c.source_revision FROM maezo_external.mzo_external_case c JOIN maezo_external.mzo_external_case_grant g USING(tenant,environment,engine_name,database_incarnation,case_ref) WHERE c.tenant=? AND c.environment=? AND c.engine_name=? AND c.database_incarnation=? AND g.principal_ref=? AND g.issuer=? AND g.subject_ref=? AND g.membership_revision=? AND g.audience=? AND g.operation=? AND NOT c.revoked AND NOT g.revoked AND g.valid_until>clock_timestamp() AND c.case_ref>? ORDER BY c.case_ref COLLATE \"C\"",1024,
        db.args(principal.get("principal_ref"),principal.get("issuer"),principal.get("subject"),number(principal.get("membership_revision")),r.get("audience"),operation,after))){
      String caseRef=(String)row.get("case_ref");if(operation.equals("detail")&&!caseRef.equals(query.get("case_ref")))continue;
      var event=db.event((String)row.get("source_ref"),numberColumn(row,"source_revision"));var head=db.head((String)row.get("source_ref"));
      if(event==null||head==null||numberColumn(head,"source_revision")!=numberColumn(row,"source_revision")||!event.get("payload_digest").equals(head.get("payload_digest")))throw unavailable();
      var source=authority.source(canonical((byte[])event.get("canonical_payload")));var identity=obj(source,"identity");
      if(!identity.get("case_ref").equals(caseRef)||!compatible(r.get("audience"),identity.get("kind")))continue;
      if(operation.equals("list")&&query.get("kind")!=null&&!query.get("kind").equals(identity.get("kind")))continue;
      var eligible=new ArrayList<Object>();for(Object value:list(source.get("disclosure_grants"))){var grant=map(value);
        if(eligibleGrant(source,grant,principal,r.get("audience"),operation))eligible.add(grant);}
      if(eligible.isEmpty())continue;sources.add(source);grants.add(eligible);
      if(sources.size()>limit)break;
    }
    boolean more=sources.size()>limit;if(more){sources.remove(sources.size()-1);grants.remove(grants.size()-1);}
    if(operation.equals("detail")&&sources.isEmpty())throw absent();
    var identities=new ArrayList<Map<String,Object>>();for(var source:sources)identities.add(obj(source,"identity"));
    // Exactly one native PostgreSQL statement snapshot for every authorized case on this page.
    var states=db.nativeStates(identities);var items=new ArrayList<Object>();var evidence=new ArrayList<Object>();
    for(int i=0;i<sources.size();i++){var source=sources.get(i);var id=obj(source,"identity");var state=states.get(str(id,"case_ref"));
      items.add(record("case_ref",id.get("case_ref"),"kind",id.get("kind"),"state",state.get("state"),"record_revision",source.get("source_revision"),"state_observed_at",time(observed)));
      evidence.add(record("source_digest",hash(source),"grants_digest",hash(grants.get(i)),"native_digest",hash(state)));}
    until=min(until,authority.until(),keys.current().notAfter);String cursor=null;
    if(more)cursor=token(record("anchor",anchor,"kind",query.get("kind"),"limit",query.get("limit"),"last_case_ref",map(items.get(items.size()-1)).get("case_ref"),"valid_until",time(until)),"external-case-cursor",2048);
    var freshness=record("observed_at",time(observed),"valid_until",time(until));
    Map<String,Object> projection=operation.equals("list")?record("schema","portal-external-case-page.v1","audience",r.get("audience"),"items",items,"next_cursor",cursor,"freshness",freshness):record("schema","portal-external-case-detail.v1","case",items.get(0),"freshness",freshness,"allowed_actions",List.of());
    Map<String,Object> answer;
    if(finalize){
      // Retain original ceilings/bytes; recomputation must not renew serialized freshness.
      projection.put("freshness",previous.get("freshness"));
      if(operation.equals("list"))projection.put("next_cursor",previous.get("next_cursor"));
      if(!hash(evidence).equals(previous.get("evidence_digest"))||!hash(projection).equals(previous.get("projection_digest"))
          ||!r.get("public_projection_digest").equals(previous.get("projection_digest")))throw conflict();
      until=min(until,time(previous.get("valid_until")));
      answer=record("schema","portal-external-case-finalized.v1","public_projection_digest",previous.get("projection_digest"),"valid_until",time(until));
    }else{
      String proof=token(record("scope",r.get("scope"),"principal_digest",hash(principal),"audience",r.get("audience"),"anchor",anchor,"operation",operation,"query",query,"observed_at",time(observed),"valid_until",time(until),"freshness",freshness,"next_cursor",cursor,"evidence_digest",hash(evidence),"projection_digest",hash(projection)),"external-case-finalize",MAX);
      answer=record("schema","portal-external-case-observation.v1","projection",projection,"continuity_proof",proof);
    }
    Instant ceiling=until;var out=new Result();out.bytes=bounded(answer);out.guard=()->{verified.current();authority.current();membershipAdmission.requireCurrent();keys.requireCurrent();if(!Instant.now().isBefore(ceiling))throw unavailable();};out.guard.run();
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->out.guard.run());
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->out.committed=true);return out;
  }
  static void validateQuery(Map<String,Object> q,String operation,Object audience){
    if(operation.equals("detail")){Jcs.keys(q,"case_ref");check("case",q.get("case_ref"));return;}
    if(!operation.equals("list"))throw invalid();Jcs.keys(q,"kind","limit","cursor");check("?authorization|reimbursement|account",q.get("kind"));
    long limit=number(q.get("limit"));if(limit<1||limit>100)throw invalid();if(q.get("kind")!=null&&!compatible(audience,q.get("kind")))throw denied();
    if(q.get("cursor")!=null&&(!(q.get("cursor") instanceof String s)||s.length()>2048))throw invalid();
  }
  static boolean eligibleGrant(Map<String,Object> source,Map<String,Object> grant,Map<String,Object> principal,Object audience,String operation){
    if(!source.get("state").equals("active")||!grant.get("state").equals("active")||!grant.get("audience").equals(audience)
        ||!list(grant.get("operations")).contains(operation)||!strings(grant.get("fields")).containsAll(FIELDS)
        ||!list(principal.get("subject_bindings")).contains(grant.get("owner"))||!list(source.get("owners")).contains(grant.get("owner")))return false;
    for(String key:List.of("issuer","subject","principal_ref","membership_revision"))if(!principal.get(key).equals(grant.get(key)))return false;return true;
  }
  private Map<String,Object> currentCheckpoint(ExternalCaseStore db,Authority authority){
    var current=db.one("SELECT c.*,e.canonical_signed_checkpoint FROM maezo_external.mzo_external_checkpoint_current c JOIN maezo_external.mzo_external_checkpoint_event e USING(tenant,environment,engine_name,database_incarnation,checkpoint_ref,epoch) WHERE c.tenant=? AND c.environment=? AND c.engine_name=? AND c.database_incarnation=?",db.args());
    if(current==null||!Instant.now().isBefore(ExternalCaseStore.instantColumn(current.get("valid_until"))))throw unavailable();
    var packet=canonical((byte[])current.get("canonical_signed_checkpoint"));var statement=authority.checkpoint(packet);
    if(!hash(packet).equals(current.get("checkpoint_digest"))||!authority.designation.equals(current.get("designation_digest")))throw unavailable();
    var accepted=db.one("SELECT checkpoint_digest FROM maezo_external.mzo_external_checkpoint_accepted WHERE "+ExternalCaseStore.S,db.args());
    var g=db.generation();if(accepted==null||!current.get("checkpoint_digest").equals(accepted.get("checkpoint_digest"))
        ||numberColumn(current,"accepted_generation")!=numberColumn(g,"accepted_generation")
        ||numberColumn(current,"published_generation")!=numberColumn(g,"published_generation"))throw unavailable();
    db.exactCheckpoint(statement);var receipt=db.receipt((String)current.get("publication_id"));
    if(receipt==null||!receipt.get("kind").equals("checkpoint")||authority.revoked.contains(receipt.get("requester_fingerprint")))throw unavailable();
    for(Object item:list(statement.get("heads"))){var h=map(item);var event=db.event(str(h,"source_ref"),number(h.get("source_revision")));
      if(event==null)throw unavailable();authority.source(canonical((byte[])event.get("canonical_payload")));}
    authority.ceilings.add(ExternalCaseStore.instantColumn(current.get("valid_until")));
    return record("checkpoint_ref",statement.get("checkpoint_ref"),"epoch",statement.get("epoch"),"checkpoint_digest",hash(packet));
  }
  private Map<String,Object> externalMembership(PortalReadStore db,Map<String,Object> principal,Object audience){
    var row=db.membership(str(principal,"principal_ref"));if(row==null)throw unavailable();
    var m=validate("membership",PortalReadStore.json(row.get("payload_")));var source=validate("source",PortalReadStore.json(row.get("source_")));
    db.proof((String)row.get("publication_"),"membership",source);membershipAdmission.verifySource("membership",source);
    if(!m.get("audience").equals(audience)||!m.get("state").equals("active"))throw denied();
    for(String key:List.of("issuer","subject","principal_ref","membership_revision","memberships","subject_bindings"))if(!principal.get(key).equals(m.get(key)))throw conflict();
    var human=db.human(str(principal,"principal_ref"));if(human==null||!Boolean.TRUE.equals(human.get("active_"))||!principal.get("issuer").equals(human.get("issuer_"))||!principal.get("subject").equals(human.get("subject_")))throw unavailable();
    var flat=new TreeSet<Object>();for(Object mb:list(m.get("memberships")))flat.addAll(list(map(mb).get("groups")));
    var actual=new HashSet<>(list(Jcs.parse(human.get("groups_").toString().getBytes(java.nio.charset.StandardCharsets.UTF_8))));if(!flat.equals(actual))throw unavailable();
    var receipt=db.one("SELECT KEY_FINGERPRINT_ FROM MZO_PORTAL_READ_PUBLICATION_RECEIPT WHERE "+PortalReadStore.SCOPE+" AND PUBLICATION_=?",db.args(row.get("publication_")));
    if(receipt==null)throw unavailable();
    var publisher=membershipTrust.keys.values().stream().filter(k->k.fingerprint().equals(receipt.get("key_fingerprint_"))).findFirst().orElseThrow(PortalReadModels::unavailable);
    Instant now=Instant.now();if(now.isBefore(publisher.notBefore())||!now.isBefore(publisher.notAfter()))throw unavailable();
    verified.admission.requireMembershipProvenance(source,human);
    Instant until=min(time(m.get("reviewed_until")),time(source.get("valid_until")),Instant.ofEpochSecond(numberColumn(human,"valid_until_")),publisher.notAfter());
    if(time(source.get("observed_at")).isAfter(Instant.now())||!Instant.now().isBefore(until))throw unavailable();
    return record("membership",m,"source",source,"publication_id",row.get("publication_"),"human_revision",human.get("rev_").toString(),"valid_until",time(until));
  }
  private String token(Map<String,Object> claims,String stage,int max){
    var key=keys.current();verified.admission.requireContinuityKey(key.id,key.generation,key.digest);var wrapped=record("key_id",key.id,"claims",claims,"mac",Base64.getUrlEncoder().withoutPadding().encodeToString(key.mac(stage,claims,Instant.now())));
    String token=Base64.getUrlEncoder().withoutPadding().encodeToString(bounded(wrapped));if(token.length()>max)throw unavailable();return token;
  }
  private Map<String,Object> verifyToken(Object value,String stage,int max){
    if(!(value instanceof String s)||s.length()>max)throw invalid();var wrapped=canonical(url64(s));Jcs.keys(wrapped,"key_id","claims","mac");
    var key=keys.verification(str(wrapped,"key_id"));if(key==null)throw conflict();verified.admission.requireContinuityKey(key.id,key.generation,key.digest);var claims=obj(wrapped,"claims");
    if(!MessageDigest.isEqual(url64(wrapped.get("mac")),key.mac(stage,claims,Instant.now()))||!Instant.now().isBefore(time(claims.get("valid_until"))))throw conflict();return claims;
  }
  private static Instant min(Instant... values){return Arrays.stream(values).min(Comparator.naturalOrder()).orElseThrow();}
}

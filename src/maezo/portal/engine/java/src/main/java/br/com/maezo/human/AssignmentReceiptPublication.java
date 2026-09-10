package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Separately source-issued receipt disclosure, never a task assignment grant. */
final class AssignmentReceiptPublication implements Command<byte[]> {
  final AssignmentTrust trust;final byte[] raw;final String peer;
  AssignmentReceiptPublication(AssignmentTrust trust,byte[] raw,String peer){this.trust=trust;this.raw=raw.clone();this.peer=peer;}
  public byte[] execute(CommandContext context){
    if(trust==null)throw unavailable();var db=new EngineStore(context,trust.tenant);var installed=AssignmentInstallation.acquire(context,db,trust);long revision=db.lockTenant();
    var envelope=Envelope.verify(raw,trust.human,"human-authority",peer,Instant.now().getEpochSecond(),db::revoked);var request=AssignmentModels.check("receiptpublication",envelope.command());var publisher=obj(trust.config,"publisher");
    if(!envelope.key().workload().equals(publisher.get("workload_ref"))||!envelope.key().fingerprint().equals(publisher.get("key_fingerprint")))throw denied();
    var old=db.rows("SELECT REQUEST_DIGEST_,RECEIPT_ FROM MZO_HUMAN_ASSIGNMENT_RECEIPT_PUBLICATION WHERE TENANT_=? AND PUBLICATION_ID_=?",trust.tenant,request.get("publication_id"));
    if(!old.isEmpty()){if(!envelope.digest().equals(old.get(0).get("request_digest_")))throw conflict();context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,x->{installed.current();envelope.requireCurrent(Instant.now().getEpochSecond());});return str(old.get(0),"receipt_").getBytes(StandardCharsets.UTF_8);}
    if(revision==Long.MAX_VALUE||number(request.get("expected_revision"))!=revision)throw conflict();
    validate(request,trust,db);var disclosure=obj(request,"disclosure");var identity=obj(disclosure,"identity");var source=obj(obj(request,"source"),"source");
    var prior=db.rows("SELECT SOURCE_REVISION_,REQUEST_ FROM MZO_HUMAN_ASSIGNMENT_RECEIPT_DISCLOSURE WHERE TENANT_=? AND TASK_=? AND COMMAND_=?",trust.tenant,identity.get("task_id"),identity.get("command_id"));
    if(!prior.isEmpty()){
      if(number(source.get("source_revision"))<=((Number)prior.get(0).get("source_revision_")).longValue())throw conflict();
      var previous=map(Jcs.parse(str(prior.get(0),"request_").getBytes(StandardCharsets.UTF_8)));var oldDisclosure=obj(previous,"disclosure");
      for(String k:List.of("identity","linkage"))if(!oldDisclosure.get(k).equals(disclosure.get(k)))throw conflict();
    }
    String hash=hash(disclosure);long next=revision+1;
    db.update("INSERT INTO MZO_HUMAN_ASSIGNMENT_RECEIPT_DISCLOSURE(TENANT_,TASK_,COMMAND_,SOURCE_REVISION_,DISCLOSURE_DIGEST_,REQUEST_) VALUES(?,?,?,?,?,?) ON CONFLICT(TENANT_,TASK_,COMMAND_) DO UPDATE SET SOURCE_REVISION_=EXCLUDED.SOURCE_REVISION_,DISCLOSURE_DIGEST_=EXCLUDED.DISCLOSURE_DIGEST_,REQUEST_=EXCLUDED.REQUEST_",trust.tenant,identity.get("task_id"),identity.get("command_id"),number(source.get("source_revision")),hash,AssignmentPublication.text(request));
    db.update("UPDATE MZO_HUMAN_TENANT SET REV_=? WHERE TENANT_=?",next,trust.tenant);
    var receipt=record("schema","human-assignment-receipt-publication-receipt.v1","tenant",trust.tenant,"publication_id",request.get("publication_id"),"request_digest",envelope.digest(),"authority_revision",Long.toString(next),"source_revision",source.get("source_revision"),"disclosure_digest",hash,"state",disclosure.get("state"));byte[] bytes=bounded(receipt);
    db.update("INSERT INTO MZO_HUMAN_ASSIGNMENT_RECEIPT_PUBLICATION(TENANT_,PUBLICATION_ID_,REQUEST_DIGEST_,REQUEST_,RECEIPT_) VALUES(?,?,?,?,?)",trust.tenant,request.get("publication_id"),envelope.digest(),AssignmentPublication.text(request),new String(bytes,StandardCharsets.UTF_8));
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,x->{
      validate(request,trust,db); // Includes the final source SQL and disclosure/policy/grant guards.
      trust.sourceCurrent(obj(request,"source"),Instant.now(),true);installed.current();envelope.requireCurrent(Instant.now().getEpochSecond());
    });return bytes;
  }
  static void validate(Map<String,Object> request,AssignmentTrust trust,EngineStore db){
    AssignmentModels.check("receiptpublication",request);trust.source(obj(request,"source"),Instant.now(),db,true);
    var d=obj(request,"disclosure");var p=obj(d,"policy");var i=obj(d,"identity");var l=obj(d,"linkage");var g=obj(d,"grant");var r=obj(d,"resource");
    String sourceDigest=hash(record("disclosure",d,"artifacts",request.get("artifacts")));
    if(!trust.tenant.equals(i.get("tenant"))||!trust.tenant.equals(p.get("tenant"))||!trust.tenant.equals(request.get("tenant"))||!sourceDigest.equals(obj(request,"source").get("generation_digest"))||!sourceDigest.equals(obj(obj(request,"source"),"source").get("source_digest")))throw denied();
    trust.approved("approved_receipt_policy_pins",str(p,"policy_ref"),hash(p));
    if(!i.get("task_id").equals(l.get("task_id"))||!i.get("principal_ref").equals(g.get("principal_ref"))||!list(p.get("resource_kinds")).contains(r.get("kind"))||list(p.get("contract_pins")).isEmpty())throw denied();
    AssignmentModels.sorted(list(p.get("contract_pins")),"artifact_ref","digest");AssignmentModels.sorted(list(p.get("resource_kinds")));AssignmentModels.sorted(list(p.get("required_consent_scopes")));AssignmentModels.sorted(list(g.get("consent_scopes")));AssignmentModels.sorted(list(d.get("required_subject_bindings")),"kind","resource_ref");
    if(p.get("subject_mode").equals("none")!=list(d.get("required_subject_bindings")).isEmpty()||!list(g.get("consent_scopes")).containsAll(list(p.get("required_consent_scopes"))))throw denied();
    if(r.get("kind").equals("task")){if(!r.get("resource_ref").equals(i.get("task_id")))throw denied();}
    else {var c=obj(r,"case_identity");if(!r.get("resource_ref").equals(c.get("case_ref"))||!l.get("process_instance_id").equals(c.get("process_instance_ref")))throw denied();for(String k:List.of("process_definition_id","process_definition_key","process_definition_version","process_definition_digest"))if(!l.get(k).equals(c.get(k)))throw denied();}
    var required=new HashSet<String>();for(Object pin:list(p.get("contract_pins")))required.add(AssignmentPublication.pin(map(pin)));required.add(AssignmentPublication.pin(obj(p,"review_receipt")));required.add(AssignmentPublication.pin(obj(g,"decision_receipt")));
    var actual=new HashSet<String>();AssignmentModels.sorted(list(request.get("artifacts")),"artifact_ref","digest");for(Object a:list(request.get("artifacts"))){var artifact=map(a);if(!Jcs.digest(b64(artifact.get("bytes_base64"),false,-1)).equals(artifact.get("digest"))||!actual.add(AssignmentPublication.pin(str(artifact,"artifact_ref"),str(artifact,"digest"))))throw denied();}if(!actual.equals(required))throw denied();
    Instant now=Instant.now();for(Object until:List.of(d.get("valid_until"),p.get("valid_until"),g.get("valid_until")))current(now,until);if(time(d.get("valid_until")).isAfter(time(p.get("valid_until")))||time(d.get("valid_until")).isAfter(time(g.get("valid_until"))))throw denied();bounded(request);
  }
}

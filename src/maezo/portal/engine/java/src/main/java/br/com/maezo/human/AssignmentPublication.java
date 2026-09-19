package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Disable/replace and exact publication receipt share the engine transaction. */
final class AssignmentPublication implements Command<byte[]> {
  private final AssignmentTrust trust; private final byte[] raw;private final String peer;
  AssignmentPublication(AssignmentTrust trust,byte[] raw,String peer){this.trust=trust;this.raw=raw.clone();this.peer=peer;}
  public byte[] execute(CommandContext context){
    if(trust==null)throw EngineStore.unavailable();var db=new EngineStore(context,trust.tenant);
    var installed=AssignmentInstallation.acquire(context,db,trust);long revision=db.lockTenant();var now=Instant.now();
    var verified=Envelope.verify(raw,trust.human,"human-authority",peer,now.getEpochSecond(),db::revoked);
    var c=AssignmentModels.check("publication",verified.command());var publisher=obj(trust.config,"publisher");
    if(!verified.key().workload().equals(publisher.get("workload_ref"))||!verified.key().fingerprint().equals(publisher.get("key_fingerprint")))throw denied();
    var oldReceipt=db.rows("SELECT REQUEST_DIGEST_,RECEIPT_ FROM MZO_HUMAN_ASSIGNMENT_PUBLICATION WHERE TENANT_=? AND PUBLICATION_ID_=?",trust.tenant,str(c,"publication_id"));
    if(!oldReceipt.isEmpty()){
      if(!verified.digest().equals(oldReceipt.get(0).get("request_digest_")))throw conflict();
      context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{installed.current();verified.requireCurrent(Instant.now().getEpochSecond());});
      return str(oldReceipt.get(0),"receipt_").getBytes(StandardCharsets.UTF_8);
    }
    if(revision==Long.MAX_VALUE||number(c.get("expected_revision"))!=revision)throw conflict();
    var source=obj(c,"source");trust.source(source,now,db);var provenance=obj(source,"source");long sourceRevision=number(provenance.get("source_revision"));
    var prior=db.rows("SELECT * FROM MZO_HUMAN_ASSIGNMENT_GENERATION WHERE TENANT_=?",trust.tenant);
    Map<String,Object> old=prior.isEmpty()?null:prior.get(0);String oldDigest=old==null?null:str(old,"generation_digest_");
    if(!Objects.equals(oldDigest,c.get("expected_generation_digest"))||old!=null&&sourceRevision<=((Number)old.get("source_revision_")).longValue())throw conflict();
    long next=revision+1;String op=str(c,"operation"), generationDigest;String state;
    if(op.equals("disable")){
      if(old==null||c.get("generation")!=null)throw conflict();
      String expected=hash(record("operation","disable","expected_generation_digest",oldDigest,"source_revision",Long.toString(sourceRevision)));
      if(!expected.equals(source.get("generation_digest"))||!expected.equals(provenance.get("source_digest")))throw denied();
      generationDigest=oldDigest;state="disabled";
      db.update("UPDATE MZO_HUMAN_ASSIGNMENT_GENERATION SET SOURCE_REVISION_=?,AUTHORITY_REVISION_=?,STATE_='disabled',ATTESTATION_=? WHERE TENANT_=?",sourceRevision,next,text(source),trust.tenant);
    }else{
      if(old!=null&&!old.get("state_").equals("disabled"))throw conflict();
      var generation=AssignmentModels.check("generation",c.get("generation"));trust.scope(generation);
      generationDigest=hash(generation);state="active";
      if(number(generation.get("source_revision"))!=sourceRevision||!generationDigest.equals(source.get("generation_digest"))||!generationDigest.equals(provenance.get("source_digest")))throw denied();
      validateGeneration(generation,trust,now);
      Map<String,Object> oldGeneration=old==null?null:map(Jcs.parse(str(old,"generation_").getBytes(StandardCharsets.UTF_8)));
      preserve(oldGeneration,generation);
      for(Object value:list(generation.get("memberships"))){var member=map(value);String ref=str(member,"principal_ref");
        var previous=db.rows("SELECT ISSUER_,SUBJECT_ FROM MZO_HUMAN_PRINCIPAL WHERE TENANT_=? AND PRINCIPAL_=?",trust.tenant,ref);
        if(!previous.isEmpty()&&(!member.get("issuer").equals(previous.get(0).get("issuer_"))||!member.get("subject").equals(previous.get(0).get("subject_"))))throw conflict();
        var groups=new TreeSet<String>();for(Object m:list(member.get("memberships")))for(Object group:list(map(m).get("groups")))groups.add((String)group);
        db.update("INSERT INTO MZO_HUMAN_PRINCIPAL(TENANT_,PRINCIPAL_,ISSUER_,SUBJECT_,REV_,ACTIVE_,VALID_UNTIL_,GROUPS_) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(TENANT_,PRINCIPAL_) DO UPDATE SET REV_=EXCLUDED.REV_,ACTIVE_=EXCLUDED.ACTIVE_,VALID_UNTIL_=EXCLUDED.VALID_UNTIL_,GROUPS_=EXCLUDED.GROUPS_",trust.tenant,ref,member.get("issuer"),member.get("subject"),next,member.get("state").equals("active"),time(member.get("reviewed_until")).getEpochSecond(),text(new ArrayList<>(groups)));
      }
      db.update("INSERT INTO MZO_HUMAN_ASSIGNMENT_GENERATION(TENANT_,SOURCE_REVISION_,AUTHORITY_REVISION_,GENERATION_DIGEST_,STATE_,GENERATION_,ATTESTATION_,VALID_UNTIL_) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(TENANT_) DO UPDATE SET SOURCE_REVISION_=EXCLUDED.SOURCE_REVISION_,AUTHORITY_REVISION_=EXCLUDED.AUTHORITY_REVISION_,GENERATION_DIGEST_=EXCLUDED.GENERATION_DIGEST_,STATE_=EXCLUDED.STATE_,GENERATION_=EXCLUDED.GENERATION_,ATTESTATION_=EXCLUDED.ATTESTATION_,VALID_UNTIL_=EXCLUDED.VALID_UNTIL_",trust.tenant,sourceRevision,next,generationDigest,state,text(generation),text(source),number(generation.get("valid_until")));
    }
    db.update("UPDATE MZO_HUMAN_TENANT SET REV_=? WHERE TENANT_=?",next,trust.tenant);
    var result=record("schema","human-assignment-publication-receipt.v1","tenant",trust.tenant,"publication_id",c.get("publication_id"),"request_digest",verified.digest(),"operation",op,"authority_revision",Long.toString(next),"source_revision",Long.toString(sourceRevision),"generation_digest",generationDigest,"state",state);
    byte[] bytes=bounded(result);
    db.update("INSERT INTO MZO_HUMAN_ASSIGNMENT_PUBLICATION(TENANT_,PUBLICATION_ID_,SOURCE_REVISION_,OPERATION_,REQUEST_DIGEST_,REQUEST_,RECEIPT_) VALUES(?,?,?,?,?,?,?)",trust.tenant,c.get("publication_id"),sourceRevision,op,verified.digest(),text(c),new String(bytes,StandardCharsets.UTF_8));
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
      trust.source(source,Instant.now(),db); // Last blocking source read, for disable and replace.
      if(c.get("generation")!=null)validateGeneration(obj(c,"generation"),trust,Instant.now());
      trust.sourceCurrent(source,Instant.now(),false);installed.current();verified.requireCurrent(Instant.now().getEpochSecond());
    });
    return bytes;
  }
  static String text(Object value){return new String(bounded(value),StandardCharsets.UTF_8);}
  static void preserve(Map<String,Object> old,Map<String,Object> generation){
    if(old==null)return;
    for(Object o:list(old.get("memberships"))){var prior=map(o);var current=list(generation.get("memberships")).stream().map(PortalReadModels::map).filter(m->m.get("principal_ref").equals(prior.get("principal_ref"))).findFirst().orElseThrow(PortalReadModels::conflict);
      if(!prior.get("issuer").equals(current.get("issuer"))||!prior.get("subject").equals(current.get("subject")))throw conflict();
      long a=number(prior.get("membership_revision")),b=number(current.get("membership_revision"));
      if(hash(prior).equals(hash(current))?a!=b:b<=a)throw conflict();
    }
    for(String group:List.of("policies","bindings"))for(Object o:list(old.get(group))){var prior=map(o);String key=group.equals("policies")?"policy_ref":"binding_ref";
      for(Object n:list(generation.get(group))){var current=map(n);if(prior.get(key).equals(current.get(key))&&prior.get("version").equals(current.get("version"))&&!hash(prior).equals(hash(current)))throw conflict();}
    }
  }
  static void validateGeneration(Map<String,Object> generation,AssignmentTrust trust,Instant now){
    AssignmentModels.check("generation",generation);AssignmentModels.currentEpoch(generation.get("valid_until"),now);
    AssignmentModels.sorted(list(generation.get("policies")),"policy_ref","version");
    AssignmentModels.sorted(list(generation.get("bindings")),"binding_ref","version");
    AssignmentModels.sorted(list(generation.get("artifacts")),"artifact_ref","digest");
    AssignmentModels.sorted(list(generation.get("resource_designations")),"task_id");
    var members=list(generation.get("memberships"));AssignmentModels.sorted(members,"principal_ref");
    if(number(generation.get("membership_count"))!=members.size()||!hash(members).equals(generation.get("membership_digest")))throw invalid();
    var persons=new HashSet<String>();for(Object o:members){var m=map(o);if(!m.get("audience").equals("staff")||!persons.add(hash(List.of(m.get("issuer"),m.get("subject")))))throw invalid();AssignmentModels.sorted(list(m.get("memberships")),"membership_ref");for(Object entry:list(m.get("memberships"))){AssignmentModels.sorted(list(map(entry).get("roles")));AssignmentModels.sorted(list(map(entry).get("groups")));}}
    var artifacts=new HashMap<String,byte[]>();var required=new HashSet<String>();
    for(Object o:list(generation.get("artifacts"))){var a=map(o);byte[] bytes=b64(a.get("bytes_base64"),false,-1);if(!Jcs.digest(bytes).equals(a.get("digest"))||artifacts.putIfAbsent(pin(str(a,"artifact_ref"),str(a,"digest")),bytes)!=null)throw invalid();}
    var policies=new HashMap<String,Map<String,Object>>();
    for(Object o:list(generation.get("policies"))){var p=map(o);String digest=hash(p);trust.approved("approved_policy_pins",str(p,"policy_ref"),digest);AssignmentModels.currentEpoch(p.get("valid_until"),now);
      if(!trust.tenant.equals(p.get("tenant"))||policies.putIfAbsent(pin(str(p,"policy_ref"),str(p,"version")),p)!=null)throw invalid();
      required.add(pin(str(p,"policy_ref"),digest));required.add(pin(obj(p,"review_receipt")));for(Object c:list(p.get("contract_pins")))required.add(pin(map(c)));
      AssignmentModels.sorted(list(p.get("rules")),"operation");
    }
    var bindings=new HashSet<String>();
    for(Object o:list(generation.get("bindings"))){var b=map(o);trust.scope(b);trust.approved("approved_binding_pins",str(b,"binding_ref"),hash(b));
      if(!bindings.add(pin(str(b,"binding_ref"),str(b,"version"))))throw invalid();
      var policy=policies.get(pin(str(b,"policy_ref"),str(b,"policy_version")));if(policy==null||!hash(policy).equals(b.get("policy_digest")))throw invalid();
      var ops=list(policy.get("rules")).stream().map(x->map(x).get("operation")).toList();if(!ops.containsAll(list(b.get("allowed_operations"))))throw invalid();
      for(String key:List.of("deployment_receipt","qualification_receipt","subject_policy","consent_policy"))required.add(pin(obj(b,key)));
      for(Object c:list(b.get("contract_pins")))required.add(pin(map(c)));
      required.add(pin(str(b,"form_key"),str(b,"form_digest")));required.add(pin(str(b,"catalog_ref"),str(b,"catalog_digest")));
      if(!obj(b,"deployment_receipt").equals(obj(trust.config,"deployment_receipt")))throw denied();
    }
    if(!required.equals(artifacts.keySet()))throw invalid();
    bounded(generation);
  }
  static String pin(Map<String,Object> p){return pin(str(p,"artifact_ref"),str(p,"digest"));}
  static String pin(String ref,String digest){return ref+"\u0000"+digest;}
}

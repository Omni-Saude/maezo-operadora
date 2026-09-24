package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static br.com.maezo.human.StaffCaseModels.*;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.*;

/** Actual installed, independently rooted staff source/key/namespace verification.
 * Configuration is explicit protected deployment input; no key/owner is discovered
 * from an untrusted request and no valid designation row is manufactured here.
 */
public final class StaffCaseInstallation {
  /** {@code nativeSchema} is the PostgreSQL schema of every {@code mzo_*} relation
   * (ADR-0060 D3, {@code maezo_native} in dev). It is a pin, never a constant: it enters
   * {@link #digest()}, so the designation/publication digest binds it, and
   * {@link StaffCaseStore} compares it exactly (D5) and requires the unqualified name to
   * resolve to the pinned OID (D4). */
  /** {@code engineSchema} (D-I, T1.8b) is the schema of the engine's {@code ACT_*}
   * relations ({@code cibseven} in D-C2); also a pin bound by {@link #digest()}. */
  public record Configuration(Map<String,Object> authScope,String designationDigest,PublicKey rootKey,
      PrivateKey resultKey,PublicKey resultPublicKey,String nativeRole,String nativeSchema,String engineSchema,
      Map<String,StaffCaseStore.RelationPin> relationPins,int maximumSeconds) {
    public Configuration {
      authScope=Collections.unmodifiableMap(copy(authScope));relationPins=Map.copyOf(relationPins);
      check("h",designationDigest);StaffCaseStore.schema(nativeSchema);EngineSchema.require(engineSchema,nativeSchema);
      if(rootKey==null||resultKey==null||resultPublicKey==null||nativeRole==null||maximumSeconds<1||maximumSeconds>10)throw unavailable();
      if(Jcs.digest(rootKey.getEncoded()).equals(Jcs.digest(resultPublicKey.getEncoded())))throw unavailable();
    }
    public String digest(){var pins=new TreeMap<String,Object>();relationPins.forEach((name,p)->pins.put(name,record("oid",Long.toString(p.oid()),"owner",p.owner())));
      return hash(record("schema","staff-case-native-configuration.v2","auth_scope",authScope,"designation_digest",designationDigest,
        "root_key_fingerprint",Jcs.digest(rootKey.getEncoded()),"result_key_fingerprint",Jcs.digest(resultPublicKey.getEncoded()),
        "native_role",nativeRole,"native_schema",nativeSchema,"engine_schema",engineSchema,"relation_pins",pins,"maximum_seconds",Integer.toString(maximumSeconds)));}
  }
  final Configuration config;
  final StaffCaseStore store;
  final PortalReadTrust.Admission admission;
  final Map<String,Object> designation;
  final Map<String,Map<String,Object>> entries=new TreeMap<>();
  final List<Instant> ceilings=new ArrayList<>();
  final Set<String> usedKeys=new HashSet<>();
  Instant last;
  StaffCaseInstallation(Configuration config,StaffCaseStore store,PortalReadTrust.Admission admission) {
    this.config=config;this.store=store;this.admission=admission;
    admission.requireCurrent();ceilings.add(admission.validUntil());
    var row=store.designation();designation=shape("designation",AuthStore.parse(row.get("canonical_designation")));
    var installed=shape("proof",AuthStore.parse(row.get("installation_proof")));
    if(!hash(designation).equals(config.designationDigest())||!config.designationDigest().equals(row.get("designation_digest"))
        ||!store.scope.equals(designation.get("scope"))||!"active".equals(designation.get("state"))
        ||number(designation.get("designation_revision"))!=Math.addExact(number(designation.get("expected_previous_revision")),1))throw denied();
    verifySignature(config.rootKey(),installed,designation,"installation");
    fresh(designation,"issued_at","valid_until");fresh(installed,"issued_at","expires_at");
    if(store.revoked(Jcs.digest(config.rootKey().getEncoded())))throw denied();
    var publicKeys=new HashSet<String>();var logins=new HashSet<String>();var peers=new HashSet<String>();var refs=new HashSet<String>();
    var items=list(designation.get("entries"));if(items.isEmpty()||items.size()>32)throw invalid();
    for(Object value:items){var entry=shape("entry",value);String fingerprint=str(entry,"key_fingerprint"),role=str(entry,"role");
      var key=key(entry.get("public_key"));
      if(!Jcs.digest(key.getEncoded()).equals(fingerprint)||!publicKeys.add(fingerprint)||!refs.add(str(entry,"entry_ref"))
          ||!logins.add(str(entry,"login_role"))||fingerprint.equals(Jcs.digest(config.rootKey().getEncoded()))
          ||entry.get("certificate_spki")!=null&&!peers.add(str(entry,"certificate_spki")))throw denied();
      var purposes=list(entry.get("purposes"));if(purposes.isEmpty()||!PURPOSES.get(role).containsAll(purposes))throw denied();
      if(Set.of("case_issuer","read_requester").contains(role)){
        var operations=list(entry.get("operations"));
        if(!(operations.equals(List.of("detail"))||operations.equals(List.of("detail","list")))||list(entry.get("projections")).isEmpty())throw denied();
      }else if(!list(entry.get("operations")).isEmpty()||!list(entry.get("projections")).isEmpty())throw denied();
      if(!time(entry.get("not_before")).isBefore(time(entry.get("valid_until"))))throw invalid();
      entries.put(fingerprint,entry);
    }
    var result=entry(Jcs.digest(config.resultPublicKey().getEncoded()),"native_result","native_result",null,null);
    if(!config.nativeRole().equals(result.get("login_role")))throw denied();
    current();
  }
  Instant current(){Instant now=Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MICROS);
    if(last!=null&&now.isBefore(last))throw unavailable();last=now;admission.requireCurrent();
    if(now.isBefore(admission.observedAt())||!now.isBefore(admission.validUntil()))throw unavailable();
    for(Instant end:ceilings)if(!now.isBefore(end))throw unavailable();return now;}
  Instant until(){current();return ceilings.stream().min(Instant::compareTo).orElseThrow();}
  void fresh(Map<String,Object> value,String from,String to){Instant a=time(value.get(from)),b=time(value.get(to)),now=current();
    if(a.isAfter(now)||!now.isBefore(b)||!a.isBefore(b))throw unavailable();ceilings.add(b);}
  void retain(Instant end){ceilings.add(end);current();}
  /**
   * C1 F9: the identity_verifier entry designates the membership source by PREFIX (the published
   * membership carries source_ref = prefix + principal_ref), same rule as T1.7a: the prefix must
   * end in ':' or '/' and be strictly shorter, so "amh:" never matches "amhx:...". Every other
   * role keeps the exact match.
   */
  static boolean sourceMatches(String role,Object designated,String actual){
    if(!(designated instanceof String d)||actual==null)return false;
    if(!"identity_verifier".equals(role))return d.equals(actual);
    return (d.endsWith(":")||d.endsWith("/"))&&actual.length()>d.length()&&actual.startsWith(d);
  }
  Map<String,Object> entry(String fingerprint,String role,String purpose,String source,String namespace){
    var e=entries.get(fingerprint);if(e==null||!role.equals(e.get("role"))||!list(e.get("purposes")).contains(purpose)
        ||store.revoked(fingerprint)||source!=null&&!sourceMatches(role,e.get("source_ref"),source)
        ||namespace!=null&&!namespace.equals(e.get("source_namespace")))throw denied();
    fresh(e,"not_before","valid_until");usedKeys.add(fingerprint);return e;
  }
  void proof(Map<String,Object> proof,Object payload,String role,String purpose,String source,String namespace){
    shape("proof",proof);var e=entry(str(proof,"key_fingerprint"),role,purpose,source,namespace);
    fresh(proof,"issued_at","expires_at");
    if(time(proof.get("issued_at")).isBefore(time(e.get("not_before")))||time(proof.get("expires_at")).isAfter(time(e.get("valid_until"))))throw denied();
    verifySignature(key(e.get("public_key")),proof,payload,purpose);
  }
  void policy(Map<String,Object> head){if(!"active".equals(head.get("state")))throw denied();policyStatement(head);}
  void policyStatement(Map<String,Object> head){
    shape("policy_head",head);if(!store.scope.equals(head.get("scope")))throw denied();
    fresh(head,"observed_at","valid_until");
    proof(obj(head,"proof"),withoutProof(head,"proof"),"case_issuer","staff_policy_head",str(head,"source_ref"),str(head,"policy_ref"));
    var decision=entry(str(head,"decision_issuer_key_fingerprint"),"case_issuer",str(head,"decision_purpose"),str(head,"source_ref"),str(head,"policy_ref"));
    // This first-slice installation uses an exact namespace/issuer entry. No prefix,
    // wildcard, importer-selected policy owner, or cross-issuer delegation exists.
    if(!decision.get("key_fingerprint").equals(obj(head,"proof").get("key_fingerprint")))throw denied();
  }

  Map<String,Object> membership(PortalReadCommand read,Map<String,Object> witness,Map<String,Object> principal) {
    shape("membership",witness);var actor=obj(witness,"actor");
    if(!store.scope.equals(witness.get("scope")))throw denied();
    var row=read.db.membership(str(actor,"principal_ref"));if(row==null)throw unavailable();
    var m=PortalReadModels.validate("membership",PortalReadStore.json(row.get("payload_")));
    var source=PortalReadModels.validate("source",PortalReadStore.json(row.get("source_")));
    if(!source.equals(witness.get("source")))throw conflict();
    read.db.proof(str(row,"publication_"),"membership",source);read.source("membership",source);
    if(!"staff".equals(m.get("audience"))||!"active".equals(m.get("state")))throw denied();
    for(String field:List.of("issuer","subject","principal_ref","membership_revision"))if(!actor.get(field).equals(m.get(field)))throw conflict();
    var human=read.humanIdentity(str(actor,"principal_ref"));
    if(!witness.get("principal_record_revision").equals(human.get("revision"))||!witness.get("principal_record_digest").equals(hash(human))
        ||!actor.get("issuer").equals(human.get("issuer"))||!actor.get("subject").equals(human.get("subject")))throw denied();
    var groups=new TreeSet<Object>();for(Object member:list(m.get("memberships")))groups.addAll(list(map(member).get("groups")));
    if(!groups.equals(new TreeSet<>(list(human.get("groups")))))throw conflict();
    if(principal!=null){
      if(!actor.equals(StaffCaseModels.actor(principal))||!principal.get("session_ref").equals(witness.get("session_ref")))throw denied();
      read.membership(principal,record("publication_id",row.get("publication_"),"source",source,"stored_membership",m));
    }
    proof(obj(witness,"proof"),withoutProof(witness,"proof"),"identity_verifier","membership_current",str(source,"source_ref"),null);
    fresh(witness,"observed_at","valid_until");retain(time(m.get("reviewed_until")));retain(time(source.get("valid_until")));retain(time(human.get("valid_until")));
    read.guard();return record("witness",copy(witness),"membership",m,"source",source,"publication_id",row.get("publication_"),"native_principal",human);
  }
  Map<String,Set<String>> grant(Map<String,Object> publication,Map<String,Object> principal,Map<String,Object> identity,List<Map<String,Object>> pins) {
    return grant(publication,principal,identity,pins,"detail");
  }
  Map<String,Set<String>> grant(Map<String,Object> publication,Map<String,Object> principal,Map<String,Object> identity,List<Map<String,Object>> pins,String operation) {
    StaffCaseModels.publication(publication);var grant=obj(publication,"payload");shape("grant",grant);
    if(!store.scope.equals(grant.get("scope"))||!identity.get("case_ref").equals(grant.get("case_ref"))
        ||!hash(identity).equals(grant.get("identity_digest"))||!"active".equals(grant.get("state")))throw denied();
    for(String field:List.of("issuer","subject","principal_ref","membership_revision"))if(!principal.get(field).equals(grant.get(field)))throw denied();
    fresh(publication,"observed_at","valid_until");fresh(grant,"observed_at","valid_until");
    proof(obj(publication,"proof"),withoutProof(publication,"proof"),"case_issuer","staff_case_grant",str(grant,"source_ref"),null);
    var heads=new HashMap<String,Map<String,Object>>();for(var pin:pins){var head=obj(pin,"head");policy(head);heads.put(str(head,"policy_ref"),head);}
    var subject=principal.containsKey("audience")?AuthModels.validate("actor",principal):StaffCaseModels.actor(principal);
    if(!"staff".equals(subject.get("audience")))throw denied();
    var fields=new HashMap<String,Set<String>>();var slots=new HashSet<String>();
    for(Object value:list(grant.get("decisions"))){var d=shape("decision",value);String projection=str(d,"projection");
      var head=heads.get(str(d,"policy_ref"));if(head==null||!"active".equals(d.get("state"))
        ||!d.get("membership_revision").equals(principal.get("membership_revision"))||!d.get("subject_identity_digest").equals(hash(subject))
        ||!head.get("decision_issuer_key_fingerprint").equals(obj(d,"decision_proof").get("key_fingerprint")))throw denied();
      var issuer=entry(str(obj(d,"decision_proof"),"key_fingerprint"),"case_issuer","staff_case_grant",str(grant,"source_ref"),str(d,"policy_ref"));
      if(!list(issuer.get("projections")).contains(projection)||!list(issuer.get("operations")).contains(d.get("operation")))throw denied();
      proof(obj(d,"decision_proof"),withoutProof(d,"decision_proof"),"case_issuer","staff_case_grant",str(grant,"source_ref"),str(d,"policy_ref"));fresh(d,"observed_at","valid_until");
      if((operation==null||operation.equals(d.get("operation")))&&d.get("resource_identity_digest").equals(hash(identity))){
        var fs=new HashSet<String>();for(Object f:list(d.get("fields")))fs.add((String)f);
        if(projection.equals("staff_current_task.v1"))fs.remove("created_at");
        // Publication-time check (operation==null) sees every operation: the issuer grants staff_summary.v1 for BOTH
        // detail and list (case_issuer.DECISIONS), so uniqueness is per (operation, projection) there — measured in C1.
        if(!slots.add(operation==null?d.get("operation")+"/"+projection:projection))throw denied();
        fields.put(projection,Set.copyOf(fs));
      }else if(d.get("resource_identity_digest").equals(hash(identity))){
        // Same resource, another operation's decision of the SAME signed grant (issuer grants detail AND list):
        // verified above, simply not used by this operation.
      }else if(!projection.equals("staff_current_task.v1")||!list(d.get("fields")).contains("created_at"))throw denied();
    }
    var required=operation==null?List.<String>of():operation.equals("list")?List.of("staff_summary.v1"):List.of("staff_summary.v1","staff_identity.v1");
    for(String projection:required)if(!FIELDS.get(projection).equals(fields.get(projection)))throw denied();
    current();return fields;
  }
  Map<String,Object> signedRead(byte[] raw,String peer){
    var r=canonical(raw);Jcs.keys(r,"schema","purpose","scope","installation_digest","request_id","principal","membership_witness","session_valid_until","operation","query","issued_at","expires_at","key_fingerprint","signature");
    if(!"staff-case-native-read.v1".equals(r.get("schema"))||!store.scope.equals(r.get("scope"))||!config.designationDigest().equals(r.get("installation_digest")))throw denied();
    String op=str(r,"operation"),purpose=str(r,"purpose");
    if(!Set.of("detail","list","finalize").contains(op)||!purpose.equals(op.equals("finalize")?"staff-case-finalize.v1":"staff-case-read.v1"))throw invalid();
    shape("principal",r.get("principal"));shape("membership",r.get("membership_witness"));shape(op,r.get("query"));check("r",r.get("request_id"));
    verifyTransport(r,peer,"read_requester",purpose);
    // Finalization authenticates transport/purpose here. Projection authority is
    // checked only after the exact immutable original operation is recovered.
    requireInitialReadCapabilities(entry(str(r,"key_fingerprint"),"read_requester",purpose,null,null),op);
    retain(time(r.get("session_valid_until")));return r;
  }
  static void requireInitialReadCapabilities(Map<String,Object> entry,String operation){
    if(!Set.of("detail","list","finalize").contains(operation))throw invalid();
    if(!"read_requester".equals(entry.get("role")))throw denied();
    if(!operation.equals("finalize"))StaffCaseModels.requireReadCapabilities(entry,operation);
  }
  Map<String,Object> signedPublication(byte[] raw,String peer){
    var e=canonical(raw);Jcs.keys(e,"schema","purpose","scope","key_fingerprint","configuration_digest","issued_at","expires_at","request","signature");
    if(!"staff-case-envelope.v1".equals(e.get("schema"))||!"staff-case-publication.v1".equals(e.get("purpose"))
        ||!store.scope.equals(e.get("scope"))||!config.digest().equals(e.get("configuration_digest")))throw denied();
    verifyTransport(e,peer,"publication_importer","staff-case-publication.v1");
    var request=publication(e.get("request"));if(!store.scope.equals(request.get("scope")))throw denied();
    entry(str(e,"key_fingerprint"),"publication_importer","staff-case-publication.v1",str(request,"source_ref"),null);return request;
  }
  void verifyTransport(Map<String,Object> request,String peer,String role,String purpose){
    var e=entry(str(request,"key_fingerprint"),role,purpose,null,null);
    if(peer==null||!peer.equals(e.get("certificate_spki")))throw denied();fresh(request,"issued_at","expires_at");
    if(time(request.get("expires_at")).isAfter(time(request.get("issued_at")).plusSeconds(config.maximumSeconds())))throw denied();
    verifyDetached(key(e.get("public_key")),request);current();
  }
  Map<String,Object> resultProof(Object payload){
    String fingerprint=Jcs.digest(config.resultPublicKey().getEncoded());entry(fingerprint,"native_result","native_result",null,null);
    var p=record("schema","staff-case-proof.v1","purpose","native_result","algorithm","Ed25519","key_fingerprint",fingerprint,
      "issued_at",time(current()),"expires_at",time(until()),"statement_digest",hash(payload));
    try{Signature signer=Signature.getInstance("Ed25519");signer.initSign(config.resultKey());signer.update(Jcs.canonical(p));
      p.put("signature",Base64.getEncoder().encodeToString(signer.sign()));}
    catch(GeneralSecurityException failure){throw unavailable();}
    verifySignature(config.resultPublicKey(),p,payload,"native_result");current();return p;
  }
  void finalDesignation(){var row=store.designation();if(!config.designationDigest().equals(row.get("designation_digest"))
    ||!hash(designation).equals(hash(AuthStore.parse(row.get("canonical_designation")))))throw conflict();
    if(store.revoked(Jcs.digest(config.rootKey().getEncoded())))throw denied();
    for(String fingerprint:usedKeys)if(store.revoked(fingerprint))throw denied();current();}
  static Map<String,Object> canonical(byte[] raw){if(raw.length>MAX)throw invalid();var value=map(Jcs.parse(raw));if(!Arrays.equals(raw,Jcs.canonical(value)))throw invalid();return value;}
  static PublicKey key(Object encoded){try{return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(base64(encoded)));}catch(GeneralSecurityException failure){throw denied();}}
  static void verifyDetached(PublicKey key,Map<String,Object> signed){var unsigned=copy(signed);byte[] signature=base64(unsigned.remove("signature"));
    if(signature.length!=64)throw denied();try{Signature v=Signature.getInstance("Ed25519");v.initVerify(key);v.update(Jcs.canonical(unsigned));if(!v.verify(signature))throw denied();}catch(GeneralSecurityException failure){throw denied();}}
  static void verifySignature(PublicKey key,Map<String,Object> proof,Object statement,String purpose){
    if(!purpose.equals(proof.get("purpose"))||!hash(statement).equals(proof.get("statement_digest"))||!Jcs.digest(key.getEncoded()).equals(proof.get("key_fingerprint")))throw denied();verifyDetached(key,proof);
  }
}

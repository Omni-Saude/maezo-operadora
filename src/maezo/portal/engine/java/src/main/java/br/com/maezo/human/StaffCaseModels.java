package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.util.*;

/** SC1 first exact-detail grammar; no legacy/external audience reinterpretation. */
final class StaffCaseModels {
  private StaffCaseModels() {}
  static final Map<String,Set<String>> FIELDS=Map.of(
    "staff_summary.v1",Set.of("case_ref","kind","state","record_revision","state_observed_at"),
    "staff_identity.v1",Set.of("upstream_resource_key","case_ref","process_instance_ref","process_definition_id","process_definition_key","process_definition_version","process_definition_digest","kind"),
    "staff_current_task.v1",Set.of("task_id","task_definition_key","task_revision","created_at","due_at","assignee_ref"));
  static final Map<String,Set<String>> PURPOSES=Map.of(
    "installer",Set.of("installation"),"publication_importer",Set.of("staff-case-publication.v1"),
    "identity_verifier",Set.of("membership_current"),"case_issuer",Set.of("staff_case_grant"),
    "native_facts",Set.of("native_case_facts"),"native_result",Set.of("native_result"),
    "read_requester",Set.of("staff-case-read.v1","staff-case-finalize.v1"));
  static final String PROJECTIONS="staff_summary.v1|staff_identity.v1|staff_current_task.v1";
  static final Map<String,String> SHAPES=Map.ofEntries(
    Map.entry("proof","schema:staff-case-proof.v1 purpose:installation|membership_current|staff_case_grant|native_case_facts|native_result algorithm:Ed25519 key_fingerprint:h issued_at:t expires_at:t statement_digest:h signature:b64"),
    Map.entry("entry","entry_ref:r role:installer|publication_importer|identity_verifier|case_issuer|native_facts|read_requester|native_result source_namespace:r source_ref:r key_fingerprint:h certificate_spki:?h public_key:b64 login_role:r purposes:[purpose projections:[projection operations:[detail not_before:t valid_until:t"),
    Map.entry("designation","schema:staff-case-designation.v1 scope:@scope designation_ref:r designation_revision:n expected_previous_revision:n authority_ref:r authority_revision:n entries:[@entry issued_at:t valid_until:t state:active|revoked"),
    Map.entry("decision","decision_ref:r policy_ref:r policy_revision:n policy_digest:h subject_identity_digest:h membership_revision:n resource_identity_digest:h operation:detail projection:projection fields:[r receipt_ref:r receipt_digest:h decision_proof:@proof observed_at:t valid_until:t state:active|revoked"),
    Map.entry("grant","grant_ref:r scope:@scope case_ref:case identity_digest:h issuer:s subject:r principal_ref:r membership_revision:n audience:staff grant_revision:n source_ref:r source_revision:n decisions:[@decision observed_at:t valid_until:t state:active|revoked"),
    Map.entry("membership","schema:staff-case-membership-witness.v1 scope:@scope actor:@actor session_ref:r principal_record_revision:n principal_record_digest:h source:@source observed_at:t valid_until:t proof:@proof"),
    Map.entry("revoke","target_kind:case_grant target_ref:r expected_revision:n"),
    Map.entry("detail","case_ref:case task_limit:n task_cursor:?r"),
    Map.entry("finalize","continuity_ref:r frozen_projection_digest:h subordinate_continuities:[r"),
    Map.entry("readpin","kind:designation|identity|membership|case_grant|native_case|native_task|task_disclosure|source_key ref:r revision:n digest:h valid_until:t"),
    Map.entry("receipt","schema:staff-case-publication-receipt.v1 publication_id:r request_digest:h scope:@scope source_ref:r source_revision:n payload_digest:h disposition:committed committed_at:t native_receipt_ref:r valid_until:t proof:@proof")
  );
  static Map<String,Object> shape(String name,Object value){
    if(name.equals("scope"))return ExternalCaseModels.shape("scope",value);
    if(name.equals("actor"))return AuthModels.validate("actor",value);
    if(name.equals("source")||name.equals("principal"))return PortalReadModels.validate(name,value);
    var m=map(value);String spec=SHAPES.get(name);if(spec==null)throw invalid();var names=new HashSet<String>();
    for(String part:spec.split(" ")){String[] f=part.split(":",2);names.add(f[0]);
      if(!m.containsKey(f[0]))throw invalid();check(f[1],m.get(f[0]));}
    if(!m.keySet().equals(names))throw invalid();
    if(name.equals("membership")&&(!"staff".equals(obj(m,"actor").get("audience"))||!"membership_current".equals(obj(m,"proof").get("purpose"))))throw invalid();
    if(name.equals("decision")){var fs=list(m.get("fields"));if(fs.isEmpty()||!FIELDS.get(str(m,"projection")).containsAll(fs))throw invalid();}
    if(name.equals("grant")){var ds=list(m.get("decisions"));if(ds.isEmpty()||ds.size()>3)throw invalid();var seen=new HashSet<>();for(Object d:ds)if(!seen.add(map(d).get("projection")))throw invalid();}
    if(name.equals("detail")&&(number(m.get("task_limit"))<1||number(m.get("task_limit"))>100))throw invalid();
    return m;
  }
  static void check(String type,Object value){
    if(type.startsWith("?")){if(value!=null)check(type.substring(1),value);return;}
    if(type.startsWith("[")){var seen=new HashSet<String>();for(Object x:list(value)){check(type.substring(1),x);if(!seen.add(hash(x)))throw invalid();}return;}
    if(type.startsWith("@")){shape(type.substring(1),value);return;}
    if(type.equals("b64")){base64(value);return;}
    if(type.equals("case")){ExternalCaseModels.check(type,value);return;}
    if(type.equals("projection")){if(!FIELDS.containsKey(value))throw invalid();return;}
    if(type.equals("purpose")){if(PURPOSES.values().stream().noneMatch(s->s.contains(value)))throw invalid();return;}
    PortalReadModels.type(type,value);
  }
  static byte[] base64(Object value){
    if(!(value instanceof String encoded)||encoded.length()>1024)throw invalid();
    try{byte[] raw=Base64.getDecoder().decode(encoded);
      if(!Base64.getEncoder().encodeToString(raw).equals(encoded))throw invalid();return raw;
    }catch(IllegalArgumentException failure){throw invalid();}
  }
  static Map<String,Object> actor(Map<String,Object> principal){
    shape("principal",principal);return shape("actor",record("principal_ref",principal.get("principal_ref"),
      "issuer",principal.get("issuer"),"subject",principal.get("subject"),"membership_revision",
      principal.get("membership_revision"),"audience","staff"));
  }
  static Map<String,Object> publication(Object value){
    var r=map(value);Jcs.keys(r,"schema","scope","publication_id","expected_source_revision","source_ref","source_revision","kind","membership_witness","payload","payload_digest","observed_at","valid_until","proof");
    if(!"staff-case-publication.v1".equals(r.get("schema")))throw invalid();shape("scope",r.get("scope"));
    for(String key:List.of("publication_id","source_ref"))check("r",r.get(key));
    if(number(r.get("source_revision"))!=Math.addExact(number(r.get("expected_source_revision")),1))throw conflict();
    check("h",r.get("payload_digest"));check("t",r.get("observed_at"));check("t",r.get("valid_until"));shape("proof",r.get("proof"));
    if(r.get("membership_witness")!=null)shape("membership",r.get("membership_witness"));
    String kind=str(r,"kind");if(!Set.of("case_grant","revoke").contains(kind))throw invalid();
    var payload=shape(kind.equals("case_grant")?"grant":"revoke",r.get("payload"));
    if(!hash(payload).equals(r.get("payload_digest")))throw denied();
    if(kind.equals("case_grant")){
      if(r.get("membership_witness")==null||!r.get("scope").equals(payload.get("scope"))
          ||!r.get("source_ref").equals(payload.get("source_ref"))||!r.get("source_revision").equals(payload.get("source_revision")))throw denied();
    }
    return r;
  }
  static Map<String,Object> withoutProof(Map<String,Object> value,String name){var r=copy(value);r.remove(name);return r;}
}

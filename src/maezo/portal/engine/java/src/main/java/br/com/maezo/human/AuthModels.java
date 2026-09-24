package br.com.maezo.human;

import java.util.*;

/** Exact E04 private closed records. Valid shape never supplies native authority. */
final class AuthModels {
  private AuthModels() {}
  /** TISS `numeroGuiaPrestador` (st_texto20); same pattern as Python `auth_profile.GUIDE_NUMBER_PATTERN` (T1.11). */
  static final java.util.regex.Pattern GUIDE_NUMBER=java.util.regex.Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,19}");
  static final Set<String> INPUT_KINDS=Set.of("actor","resource_authority","guide","start_facts","document_custody","document_policy","audit_intent");
  static final Map<String,String> SHAPES=Map.ofEntries(
    Map.entry("scope","tenant:r environment:r engine_name:r database_incarnation:r installation_ref:r installation_revision:p"),
    Map.entry("actor","principal_ref:r issuer:s subject:r membership_revision:n audience:staff|beneficiary|provider"),
    Map.entry("definition","process_key:SP-OP-AUTH-001 definition_id:r definition_digest:h deployment_id:r input_profile:portal-auth-intake.v1 profile_digest:h"),
    Map.entry("pin","kind:actor|resource_authority|guide|start_facts|document_custody|document_policy|audit_intent resource_ref:r head_generation:p source:@source payload_digest:h"),
    Map.entry("audit","intent_ref:r admitted_command_id:r admitted_digest:h source:@source"),
    Map.entry("document","document_ref:r custody_revision:n content_sha256:h storage_version_ref:r policy_digest:h"),
    Map.entry("binding","request_ref:r generation:p request_revision:n binding_revision:p process_instance_id:r definition:@definition scope_execution_id:r subscription_id:r subscription_revision:p subscription_execution_id:r execution_revision:p timer_job_id:r timer_deadline:t"),
    Map.entry("start","schema:human-auth-start.v1 scope:@scope workload_ref:r actor:@actor intake_ref:r command_id:r admission:@audit guide_identity_ref:r definition:@definition input_pins:[@pin start_facts_ref:r start_facts_digest:h projected_variables_digest:h"),
    Map.entry("documents","schema:human-auth-documents.v1 scope:@scope workload_ref:r actor:@actor case_ref:r command_id:r admission:@audit occurrence:@binding input_pins:[@pin document_refs:[@document document_set_digest:h assessment_ref:r assessment_digest:h documentacao_completa:bool"),
    Map.entry("guide","guide_identity_ref:r source_ref:r namespace_ref:r source_guide_ref:r numero_guia_tiss:s cutover_ref:r cutover_revision:n legacy_state:absent_at_cutover|existing|ambiguous|unreconciled prior_instance_id:?r prior_case_ref:?r source:@source"),
    Map.entry("facts","facts_ref:r intake_ref:r guide_identity_ref:r beneficiary_pseudo_id:r provider_ref:r procedure_code:s category:consulta|exame_simples|exame_especial|terapia|internacao|opme|alta_complexidade character:urgencia|eletivo claimed_amount_cents:n document_refs:[@document requer_autorizacao:bool beneficiario_ativo:bool carencia_cumprida:bool documentacao_completa:bool missing_requirement_codes:[r documentary_assessment_ref:r request_digest:h factual_sources:[@source policy_artifacts:[@artifact"),
    Map.entry("occurrence","request_ref:r scope:@scope case_ref:r process_instance_id:r definition:@definition generation:p request_revision:n binding_revision:n creator_execution_id:r producer_external_task_id:r publication_external_task_id:?r state:created|awaiting_publication_worker|bound|consumed|expired|cancelled|replaced created_at:t policy_ref:?r policy_digest:?h binding:?@binding successor_ref:?r terminal_command_id:?r"),
    Map.entry("authority","authority_ref:r actor:@actor beneficiary_ref:r provider_ref:?r resource_kind:guide|intake|case resource_ref:r action:auth.start|auth.documents.respond|auth.receipt.read|auth.document_context.read request_ref:?r relationship_revision:n consent_revision:n grant_ref:r basis_ref:r legal_basis:consent|other_qualified_basis consent_state:valid|not_required|revoked state:active|revoked valid_from:t valid_until:t source:@source"),
    Map.entry("custody","document:@document resource_kind:intake|case resource_ref:r creator_principal_ref:r screening_ref:r screening_revision:n screening_result:clean|quarantined|rejected|pending custody_state:available|revoked|deleted key_custody_ref:r key_custody_revision:n valid_until:t"),
    Map.entry("policy","assessment_ref:r resource_kind:intake|case resource_ref:r request_ref:?r request_revision:n policy:@artifact policy_revision:n recipient_principal_refs:[r required_codes:[r missing_codes:[r submitted_response_digest:?h effective_document_refs:[@document document_set_digest:h complete:bool source:@source valid_until:t"),
    Map.entry("session-binding","session_ref:r authenticated_at:t session_expires_at:t authorization_until:t session_source_revision:p session_record_digest:h"),
    Map.entry("intent","intent_ref:r intake_or_response_ref:r command_id:r actor:@actor admitted_digest:h operation:auth.start|auth.documents.respond state:committed admitted_at:t session_binding:@session-binding"),
    Map.entry("publication","schema:human-auth-input-publication.v1 scope:@scope workload_ref:r publication_id:r kind:actor|resource_authority|guide|start_facts|document_custody|document_policy|audit_intent resource_ref:r expected_generation:n source:@source state:active|frozen|revoked payload:?payload payload_digest:?h valid_until:t"),
    Map.entry("receipt-query","schema:human-auth-receipt-query.v1 scope:@scope workload_ref:r actor:@actor query_id:r operation:auth.start|auth.documents.respond command_id:r expected_command_digest:h intake_or_case_ref:r"),
    Map.entry("context-query","schema:human-auth-document-context-query.v1 scope:@scope workload_ref:r actor:@actor query_id:r case_ref:r request_ref:r"),
    Map.entry("publication-query","schema:human-auth-publication-query.v1 scope:@scope workload_ref:r query_id:r publication_id:r expected_digest:h"),
    Map.entry("receipt","schema:human-auth-effect-receipt.v1 scope:@scope receipt_ref:r command_id:r command_digest:h operation:auth.start|auth.documents.respond actor_principal_ref:r admission_ref:r admitted_digest:h intake_ref:?r case_ref:r process_instance_id:r definition:@definition guide_identity_ref:?r request_ref:?r occurrence_generation:?p subscription_id:?r document_set_digest:?h outcome:started|existing|documents_correlated committed_at:t"),
    Map.entry("lookup","schema:human-auth-receipt-lookup.v1 scope:@scope query_id:r query_digest:h observed_at:t status:committed|absent receipt:?@receipt"),
    Map.entry("context","schema:human-auth-document-context.v1 scope:@scope query_id:r query_digest:h actor:@actor occurrence:@occurrence input_pins:[@pin valid_until:t"),
    Map.entry("publication-receipt","schema:human-auth-input-receipt.v1 scope:@scope publication_id:r request_digest:h kind:actor|resource_authority|guide|start_facts|document_custody|document_policy|audit_intent resource_ref:r previous_generation:n head_generation:p state:active|frozen|revoked payload_digest:?h committed_at:t"),
    Map.entry("publication-lookup","schema:human-auth-publication-lookup.v1 scope:@scope query_id:r query_digest:h status:committed|absent receipt:?@publication-receipt observed_at:t")
  );
  static Map<String,Object> parse(String shape,byte[] bytes){return validate(shape,Jcs.parse(bytes));}
  static Map<String,Object> validate(String shape,Object value) {
    if(shape.equals("source"))return PortalReadModels.validate("source",value);
    if(shape.equals("artifact"))return PortalReadModels.validate("pin",value);
    if(shape.equals("membership"))return PortalReadModels.validate("membership",value);
    var result=Jcs.object(value);String fields=SHAPES.get(shape);if(fields==null)throw Rejected.invalid();
    var names=new HashSet<String>();
    for(String field:fields.split(" ")) {
      String[] pair=field.split(":",2);names.add(pair[0]);
      if(!result.containsKey(pair[0]))throw Rejected.invalid();type(pair[1],result.get(pair[0]));
    }
    if(!names.equals(result.keySet()))throw Rejected.invalid();
    for(String key:List.of("document_refs","effective_document_refs"))if(result.containsKey(key))documents(result.get(key));
    if(result.containsKey("input_pins"))pins(result.get("input_pins"));
    if(shape.equals("guide")) {
      if(!GUIDE_NUMBER.matcher(Jcs.string(result,"numero_guia_tiss")).matches())throw Rejected.invalid();
      boolean existing=result.get("legacy_state").equals("existing");
      if(existing!=(result.get("prior_instance_id")!=null)||existing!=(result.get("prior_case_ref")!=null))throw Rejected.invalid();
    }
    if(shape.equals("publication"))publication(result);
    if(shape.equals("receipt")) {
      boolean start=result.get("operation").equals("auth.start");
      for(String key:List.of("intake_ref","guide_identity_ref"))if(start!=(result.get(key)!=null))throw Rejected.invalid();
      for(String key:List.of("request_ref","occurrence_generation","subscription_id","document_set_digest"))if(start==(result.get(key)!=null))throw Rejected.invalid();
      if(start==result.get("outcome").equals("documents_correlated"))throw Rejected.invalid();
    }
    if(shape.equals("lookup")||shape.equals("publication-lookup")) {
      if(result.get("status").equals("committed")!=(result.get("receipt")!=null))throw Rejected.invalid();
    }
    if(shape.equals("policy")) {
      boolean intake=result.get("resource_kind").equals("intake");
      if(intake!=(result.get("request_ref")==null)||(intake&&result.get("submitted_response_digest")!=null))throw Rejected.invalid();
      if(intake&&PortalReadModels.number(result.get("request_revision"))!=0)throw Rejected.invalid();
      if(!PortalReadModels.hash(result.get("effective_document_refs")).equals(result.get("document_set_digest")))throw Rejected.invalid();
    }
    return result;
  }
  private static void type(String kind,Object value) {
    if(kind.startsWith("?")){if(value!=null)type(kind.substring(1),value);return;}
    if(kind.startsWith("[")) {
      var list=PortalReadModels.list(value);if(list.size()>1024)throw Rejected.invalid();var seen=new HashSet<String>();
      for(Object item:list){type(kind.substring(1),item);if(!seen.add(PortalReadModels.hash(item)))throw Rejected.invalid();}return;
    }
    if(kind.startsWith("@")){validate(kind.substring(1),value);return;}
    if(kind.equals("payload")){Jcs.object(value);return;}
    if(kind.equals("p")){if(PortalReadModels.number(value)<1)throw Rejected.invalid();return;}
    PortalReadModels.type(kind,value);
  }
  static List<Object> documents(Object value) {
    var result=PortalReadModels.list(value);String previous=null;
    for(Object item:result) {
      String ref=Jcs.ref(validate("document",item),"document_ref");
      if(previous!=null&&previous.compareTo(ref)>=0)throw Rejected.invalid();previous=ref;
    }
    return result;
  }
  static void pins(Object value) {
    String previous=null;
    for(Object item:PortalReadModels.list(value)) {
      var pin=validate("pin",item);String key=Jcs.string(pin,"kind")+"\n"+Jcs.ref(pin,"resource_ref");
      if(previous!=null&&previous.compareTo(key)>=0)throw Rejected.invalid();previous=key;
    }
  }
  static void publication(Map<String,Object> value) {
    boolean active=value.get("state").equals("active");
    if(active!=(value.get("payload")!=null)||active!=(value.get("payload_digest")!=null))throw Rejected.invalid();
    if(!active)return;
    String shape=switch(Jcs.string(value,"kind")) {
      case "actor"->"membership";case "resource_authority"->"authority";case "guide"->"guide";
      case "start_facts"->"facts";case "document_custody"->"custody";case "document_policy"->"policy";
      case "audit_intent"->"intent";default->throw Rejected.invalid();
    };
    var payload=validate(shape,value.get("payload"));
    if(!PortalReadModels.hash(payload).equals(value.get("payload_digest")))throw Rejected.invalid();
    Object identity=switch(shape) {
      case "membership"->payload.get("principal_ref");case "authority"->payload.get("authority_ref");
      case "guide"->payload.get("guide_identity_ref");case "facts"->payload.get("facts_ref");
      case "custody"->Jcs.object(payload.get("document")).get("document_ref");
      case "policy"->payload.get("assessment_ref");case "intent"->payload.get("intent_ref");
      default->throw Rejected.invalid();
    };
    if(!value.get("resource_ref").equals(identity))throw Rejected.invalid();
  }
}

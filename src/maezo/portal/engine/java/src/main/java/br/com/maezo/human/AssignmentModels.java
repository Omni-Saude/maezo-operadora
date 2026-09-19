package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.time.Instant;
import java.util.*;

/** Exact E03 schemas. Uses the existing number-free primitive/nested codecs. */
final class AssignmentModels {
  static final Map<String,String> SHAPES = Map.ofEntries(
    Map.entry("receiptidentity", "tenant:r task_id:r command_id:r payload_digest:h principal_ref:r workload_ref:r"),
    Map.entry("receiptlinkage", "task_id:r process_instance_id:r process_definition_id:r process_definition_key:r process_definition_version:n process_definition_digest:h task_definition_key:r binding_ref:r binding_version:n binding_digest:h"),
    Map.entry("receipttaskresource", "kind:task resource_ref:r"),
    Map.entry("receiptcaseresource", "kind:case resource_ref:r case_identity:caseidentity ownership_source_ref:r ownership_source_revision:n ownership_source_digest:h"),
    Map.entry("receiptpolicy", "schema:human-assignment-receipt-policy.v1 policy_ref:r version:n tenant:r contract_pins:[@pin membership_requirement:@requirement resource_kinds:[task|case subject_mode:none|resource_bindings required_consent_scopes:[r projection:human-assignment-receipt-disclosure.v1 review_receipt:@pin valid_until:t"),
    Map.entry("receiptgrant", "issuer:s subject:r principal_ref:r membership_revision:n consent_scopes:[r decision_receipt:@pin valid_until:t"),
    Map.entry("receiptdisclosure", "schema:human-assignment-receipt-disclosure.v1 identity:@receiptidentity linkage:@receiptlinkage resource:receiptresource policy:@receiptpolicy required_subject_bindings:[@subject grant:@receiptgrant projection:human-assignment-receipt-disclosure.v1 state:active|revoked valid_until:t"),
    Map.entry("receiptpublication", "schema:human-assignment-receipt-publication.v1 tenant:r workload_ref:r publication_id:r expected_revision:n source:@attestation disclosure:@receiptdisclosure artifacts:[@artifact"),
    Map.entry("receiptquery", "schema:human-assignment-receipt-authority.v1 tenant:r workload_ref:r principal_ref:r principal_issuer:s principal_subject:r membership_revision:n identity:@receiptidentity"),
    Map.entry("requirement", "roles:[r groups:[r"),
    Map.entry("rule", "operation:claim|release|reassign actor:@requirement target:?@requirement ownership_states:[unassigned|actor_assigned|other_assigned target_relations:[actor|current_assignee|other"),
    Map.entry("policy", "schema:human-assignment-policy.v1 policy_ref:r version:n tenant:r contract_pins:[@pin rules:[@rule review_receipt:@pin valid_until:n"),
    Map.entry("binding", "schema:human-assignment-binding.v1 binding_ref:r version:n tenant:r environment:r engine_name:r database_incarnation:r process_definition_id:r process_definition_key:r process_definition_version:n process_definition_digest:h task_definition_key:r form_key:r form_version:n form_digest:h catalog_ref:r catalog_revision:n catalog_digest:h deployment_receipt:@pin contract_pins:[@pin group_domain:@domain policy_ref:r policy_version:n policy_digest:h allowed_operations:[claim|release|reassign constraint_mode:none|published_resource subject_policy:@pin consent_policy:@pin qualification:qualified|revoked qualification_receipt:@pin valid_until:n"),
    Map.entry("designation", "task_id:r binding_ref:r binding_version:n resource_ref:r resource_revision:n resource_digest:h"),
    Map.entry("generation", "schema:human-staff-assignment-generation.v1 tenant:r environment:r engine_name:r database_incarnation:r source_revision:n state:complete memberships:[@membership membership_count:n membership_digest:h policies:[@policy bindings:[@binding resource_designations:[@designation artifacts:[@artifact valid_until:n"),
    Map.entry("attestation", "schema:human-staff-assignment-source.v1 source:@source tenant:r environment:r engine_name:r database_incarnation:r source_key_id:r algorithm:Ed25519 generation_digest:h signature:s"),
    Map.entry("publication", "schema:human-assignment-publication.v1 tenant:r workload_ref:r publication_id:r expected_revision:n operation:disable|replace source:@attestation generation:?@generation expected_generation_digest:?h"),
    Map.entry("query", "schema:human-assignment-query.v1 operation:context|candidates|authority tenant:r workload_ref:r principal_ref:r principal_issuer:s principal_subject:r task_id:r expected_context:?@context target_ref:?r target_membership_revision:?n requested_operation:?claim|release|reassign"),
    Map.entry("context", "schema_version:portal-assignment-context.v2 task_id:r process_definition_key:r process_definition_version:n process_definition_id:r process_definition_digest:h task_definition_key:r form_key:r form_version:n form_digest:h expected_task_revision:n expected_evidence_revision:n expected_evidence_digest:h expected_membership_revision:n expected_authority_revision:n assignee_ref:?r allowed_operations:[claim|release|reassign valid_until:t binding_ref:r binding_version:n binding_digest:h policy_ref:r policy_version:n policy_digest:h source_revision:n generation_digest:h"),
    Map.entry("command", "schema:human-assignment.v2 tenant:r task_id:r command_id:r principal_ref:r principal_issuer:s principal_subject:r workload_ref:r operation:claim|release|reassign process_definition_id:r process_definition_key:r process_definition_version:n process_definition_digest:h task_definition_key:r form_key:r form_version:n form_digest:h task_revision:n authority_revision:n membership_revision:n evidence_revision:n evidence_ref:r evidence_digest:h assignee_ref:?r audit_intent_ref:r outcome:null binding_ref:r binding_version:n binding_digest:h policy_ref:r policy_version:n policy_digest:h source_revision:n generation_digest:h target_ref:?r target_membership_revision:?n")
  );
  static Map<String,Object> check(String shape, Object value) {
    if (!SHAPES.containsKey(shape)) return validate(shape,value);
    var m=map(value); var names=new HashSet<String>();
    for (String field:SHAPES.get(shape).split(" ")) {
      var parts=field.split(":",2); names.add(parts[0]);
      if (!m.containsKey(parts[0])) throw invalid();
      field(parts[1],m.get(parts[0]));
    }
    if (!m.keySet().equals(names)) throw invalid();
    if (shape.equals("requirement") && (list(m.get("roles")).isEmpty() || list(m.get("groups")).isEmpty())) throw invalid();
    if (shape.equals("rule")) {
      String op=str(m,"operation");
      if (op.equals("reassign")) {
        if(m.get("target")==null || list(m.get("ownership_states")).isEmpty() || list(m.get("target_relations")).isEmpty()) throw invalid();
      } else if(m.get("target")!=null || !list(m.get("target_relations")).isEmpty() || !m.get("ownership_states").equals(List.of(op.equals("claim")?"unassigned":"actor_assigned"))) throw invalid();
    }
    if (Set.of("policy","binding").contains(shape) && number(m.get("version"))==0) throw invalid();
    if (shape.equals("command") && (m.get("operation").equals("reassign") != (m.get("target_ref")!=null && m.get("target_membership_revision")!=null))) throw invalid();
    if(shape.equals("command") && !m.get("operation").equals("reassign") && (m.get("target_ref")!=null || m.get("target_membership_revision")!=null)) throw invalid();
    if(shape.equals("requirement")){sorted(list(m.get("roles")));sorted(list(m.get("groups")));}
    if(shape.equals("rule")){sorted(list(m.get("ownership_states")));sorted(list(m.get("target_relations")));}
    if(shape.equals("policy")){sorted(list(m.get("contract_pins")),"artifact_ref","digest");if(list(m.get("contract_pins")).isEmpty())throw invalid();}
    if(shape.equals("binding")){sorted(list(m.get("allowed_operations")));sorted(list(m.get("contract_pins")),"artifact_ref","digest");if(list(m.get("contract_pins")).isEmpty())throw invalid();}
    return m;
  }
  static void field(String type,Object value) {
    if(type.equals("caseidentity")){ExternalCaseModels.shape("identity",value);return;}
    if(type.equals("receiptresource")){check("case".equals(map(value).get("kind"))?"receiptcaseresource":"receipttaskresource",value);return;}
    if(type.startsWith("?")) {if(value!=null)field(type.substring(1),value);return;}
    if(type.startsWith("[")) {
      var values=list(value);var seen=new HashSet<String>();
      for(var v:values){field(type.substring(1),v);if(!seen.add(hash(v)))throw invalid();} return;
    }
    if(type.startsWith("@")){check(type.substring(1),value);return;}
    if(type.equals("null")){if(value!=null)throw invalid();return;}
    PortalReadModels.type(type,value);
  }
  static void sorted(List<Object> rows,String...keys){
    List<Object> prior=null;
    for(Object row:rows){
      List<Object> identity=keys.length==0?List.of(row):Arrays.stream(keys).map(k->map(row).get(k)).toList();
      if(prior!=null){int compared=0;for(int i=0;i<identity.size()&&compared==0;i++){
        String a=prior.get(i).toString(),b=identity.get(i).toString();
        compared=keys.length>0&&keys[i].equals("version")?Long.compare(number(a),number(b)):UTF8.compare(a,b);
      }if(compared>=0)throw invalid();}prior=identity;
    }
  }
  static void currentEpoch(Object value,Instant now){if(number(value)<=now.getEpochSecond())throw conflict();}
  static boolean requirement(Map<String,Object> rule,Map<String,Object> member,Set<String> candidates){
    for(Object o:list(member.get("memberships"))){var m=map(o);
      if(list(m.get("roles")).containsAll(list(rule.get("roles"))) && list(m.get("groups")).stream().anyMatch(g->list(rule.get("groups")).contains(g)&&candidates.contains(g)))return true;
    }return false;
  }
  static boolean permits(Map<String,Object> rule,Map<String,Object> actor,Map<String,Object> target,String assignee,Set<String> groups,Instant now){
    if(!actor.get("state").equals("active") || !now.isBefore(time(actor.get("reviewed_until"))) || !requirement(obj(rule,"actor"),actor,groups))return false;
    String state=assignee==null?"unassigned":assignee.equals(actor.get("principal_ref"))?"actor_assigned":"other_assigned";
    if(!list(rule.get("ownership_states")).contains(state))return false;
    if(!rule.get("operation").equals("reassign"))return target==null;
    if(target==null || !target.get("state").equals("active") || !now.isBefore(time(target.get("reviewed_until"))) || !requirement(obj(rule,"target"),target,groups))return false;
    var relations=new HashSet<String>();
    if(target.get("principal_ref").equals(actor.get("principal_ref")))relations.add("actor");
    if(target.get("principal_ref").equals(assignee))relations.add("current_assignee");
    if(relations.isEmpty())relations.add("other");
    return list(rule.get("target_relations")).containsAll(relations);
  }
  static HumanCommand identity(Map<String,Object> c){
    var old=new TreeMap<String,Object>();
    for(String key:SHAPES.get("command").split(" ")){String name=key.split(":",2)[0];if(!Set.of("binding_ref","binding_version","binding_digest","policy_ref","policy_version","policy_digest","source_revision","generation_digest","target_ref","target_membership_revision").contains(name))old.put(name,c.get(name));}
    old.put("schema","human-command.v1"); old.put("operation","claim");
    return HumanCommand.parse(old); // Identity-only adapter; not an executable v1 command.
  }
}

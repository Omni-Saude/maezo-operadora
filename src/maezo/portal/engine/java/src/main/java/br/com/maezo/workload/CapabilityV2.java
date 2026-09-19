package br.com.maezo.workload;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.cibseven.bpm.engine.variable.Variables;
import org.cibseven.bpm.engine.variable.value.TypedValue;

/** Exact D7-A document plus deployment-owned source bindings; no inferred human-name filter. */
public final class CapabilityV2 {
  private static final Map<String,Map<String,Object>> SCHEMAS = schemas();
  final Map<String,Object> document, schema, target, identity, sourceTarget;
  final String digest, worker;
  final List<?> attestations;
  final String sourceKind;
  final String sourceWorker;
  final Map<String,Object> binding;
  final Map<String,Object> sourceOwner, acquisitionPolicy;

  CapabilityV2(Map<String,Object> binding) {
    binding=Json.parse(Json.bytes(binding));this.binding=binding;
    Json.keys(binding, "document", "digest", "source_kind", "source_worker_id", "attestations", "source_owner_binding");
    document = Json.object(binding.get("document")); digest = Json.token(binding,"digest");
    Json.keys(document, "protocol", "identity", "target", "schema", "worker_id", "source_target", "acquisition_policy");
    if (!"maezo.engine-capability.v2".equals(document.get("protocol")) || !Json.digest(document).equals(digest)) throw Refused.unavailable();
    schema = Json.object(document.get("schema")); target = target(document.get("target"));
    identity = identity(document.get("identity"));
    if (!schema.equals(SCHEMAS.get(Json.token(schema,"schema_id")))) throw Refused.unavailable();
    worker = Json.string(document,"worker_id");
    sourceTarget = document.get("source_target") == null ? Map.of() : target(document.get("source_target"));
    sourceKind = Json.string(binding,"source_kind"); sourceWorker=Json.string(binding,"source_worker_id"); attestations = Json.list(binding.get("attestations"));
    if (sourceKind.equals("locked_external")) Json.token(binding,"source_worker_id");
    else if (!sourceWorker.isEmpty()) throw Refused.unavailable();
    if (!schema.get("process_key").equals(target.get("process_key")) || !schema.get("workload").equals(identity.get("workload"))
        || !schema.get("topic").equals(target.get("topic")) || !schema.get("message").equals(target.get("message"))) throw Refused.unavailable();
    boolean external = !Json.string(schema,"topic").isEmpty();
    if (external != !worker.isEmpty() || (!worker.isEmpty() && !worker.matches("[!-~]{1,256}"))) throw Refused.unavailable();
    String source = Json.string(schema,"source_process_key");
    if (source.isEmpty() != sourceTarget.isEmpty()) throw Refused.unavailable();
    if (source.isEmpty()) {
      if (!sourceKind.isEmpty() || !attestations.isEmpty()) throw Refused.unavailable();
    } else if (!source.equals(sourceTarget.get("process_key")) || !schema.get("source_topic").equals(sourceTarget.get("topic"))
        || !Set.of("locked_external", "completed_human").contains(sourceKind)) throw Refused.unavailable();
    acquisitionPolicy=Json.object(document.get("acquisition_policy"));
    Json.keys(acquisitionPolicy,"resource_requirement","source_requirement","source_owner_binding_digest");
    boolean resourceRequired=Set.of("external_complete","external_failure","external_bpmn_error","external_unlock","external_extend_lock").contains(schema.get("operation"));
    if(!(resourceRequired?"required":"none").equals(acquisitionPolicy.get("resource_requirement"))
        || !(sourceKind.equals("locked_external")?"required":"none").equals(acquisitionPolicy.get("source_requirement")))throw Refused.unavailable();
    if(sourceKind.equals("locked_external")) {
      sourceOwner=Json.object(binding.get("source_owner_binding"));
      Json.keys(sourceOwner,"identity","native_user","worker_id","target","fetch_capability_digest");
      var owner=identity(sourceOwner.get("identity")); Json.token(sourceOwner,"native_user");
      NativeOutcomeV2.digest(sourceOwner,"fetch_capability_digest");
      if(!identity.get("tenant").equals(owner.get("tenant")) || !identity.get("environment").equals(owner.get("environment"))
          || !sourceWorker.equals(sourceOwner.get("worker_id")) || !sourceTarget.equals(target(sourceOwner.get("target")))
          || !Json.digest(sourceOwner).equals(acquisitionPolicy.get("source_owner_binding_digest")))throw Refused.unavailable();
    } else {
      sourceOwner=Map.of();
      if(binding.get("source_owner_binding")!=null || acquisitionPolicy.get("source_owner_binding_digest")!=null)throw Refused.unavailable();
    }
    Set<String> required = new HashSet<>();
    for (Object item : Json.list(schema.get("fields"))) {
      var f = Json.object(item);
      if (Set.of("engine_fact","prior_human_evidence").contains(f.get("origin"))) required.add(Json.token(f,"name"));
    }
    for (Object field : Json.list(schema.get("correlation_fields"))) if (!"tenant_id".equals(field)) required.add((String)field);
    Set<String> found = new HashSet<>();
    for (Object item : attestations) {
      var a = Json.object(item); Json.keys(a,"name","source_variable","human_task_definition");
      String n = Json.token(a,"name"); Json.token(a,"source_variable"); Json.string(a,"human_task_definition");
      if (!found.add(n)) throw Refused.unavailable();
    }
    if (!found.equals(required)) throw Refused.unavailable();
  }

  static Map<String,Object> identity(Object value) {
    var m = Json.object(value);
    Json.keys(m,"tenant","environment","workload","workload_version","issuer","subject","origin");
    for (String key : m.keySet()) Json.token(m,key);
    if (!"verified_mtls".equals(m.get("origin"))) throw Refused.unavailable();
    return m;
  }
  static Map<String,Object> target(Object value) {
    var m = Json.object(value); Json.keys(m,"process_key","process_version","definition_id","topic","message");
    Json.token(m,"process_key"); Json.token(m,"definition_id"); Json.string(m,"topic"); Json.string(m,"message");
    if (Json.number(m,"process_version") < 1) throw Refused.unavailable(); return m;
  }
  static Map<String,Map<String,Object>> schemas() {
    try (InputStream in = CapabilityV2.class.getResourceAsStream("/engine-schemas-v2.json")) {
      var root = Json.parse(Objects.requireNonNull(in).readAllBytes());
      Json.keys(root,"protocol","schemas");
      if (!"maezo.engine-schemas.v2".equals(root.get("protocol"))) throw Refused.unavailable();
      Map<String,Map<String,Object>> result = new HashMap<>();
      for (Object o : Json.list(root.get("schemas"))) {
        var row = Json.object(o);
        if (result.put(Json.token(row,"schema_id"),row) != null) throw Refused.unavailable();
      }
      return Map.copyOf(result);
    } catch (IOException e) { throw Refused.unavailable(); }
  }

  Map<String,Object> validate(Map<String,Object> request) {
    Json.keys(request,"protocol","capability_digest","operation","process_key","resource_ref","variables","correlation",
        "all_matching","error_code","topic","message","worker_id","parameters","source_ref","command_id","activation_ref","resource_acquisition","source_acquisition");
    if (!"maezo.engine-operation.v2".equals(request.get("protocol")) || !digest.equals(request.get("capability_digest"))) throw Refused.denied();
    for (String field : List.of("operation","process_key","topic","message","all_matching")) {
      if (!schema.get(field).equals(request.get(field))) throw Refused.denied();
    }
    if (!worker.equals(request.get("worker_id"))) throw Refused.denied();
    String resource = Json.string(request,"resource_ref");
    if (resource.isEmpty()) {
      if (!"correlate".equals(schema.get("operation")) || !Json.bool(schema,"all_matching")) throw Refused.resource();
    } else Json.token(request,"resource_ref");
    String source = Json.string(request,"source_ref");
    if (sourceTarget.isEmpty() != source.isEmpty()) throw Refused.resource();
    if (!source.isEmpty()) Json.token(request,"source_ref");
    NativeOutcomeV2.ref(request,"command_id"); Json.token(request,"activation_ref");
    for(String role:List.of("resource","source")) {
      Object value=request.get(role+"_acquisition");
      boolean required="required".equals(acquisitionPolicy.get(role+"_requirement"));
      if(required!=(value!=null))throw Refused.body();
      if(value!=null)NativeOutcomeV2.reference(value);
    }
    if(!Json.string(request,"resource_ref").isEmpty() && request.get("resource_ref").equals(request.get("source_ref"))
        && request.get("resource_acquisition")!=null && !request.get("resource_acquisition").equals(request.get("source_acquisition")))throw Refused.denied();
    var variables = Json.object(request.get("variables"));
    Map<String,Map<String,Object>> fields = new HashMap<>();
    for (Object item : Json.list(schema.get("fields"))) {
      var f = Json.object(item); String name = Json.token(f,"name"); fields.put(name,f);
      if (Json.bool(f,"required") && !variables.containsKey(name)) throw Refused.body();
    }
    if (!fields.keySet().containsAll(variables.keySet())) throw Refused.denied();
    variables.forEach((name,value) -> validateField(fields.get(name),value));
    var correlation = Json.object(request.get("correlation"));
    if (!correlation.keySet().equals(new HashSet<>(Json.list(schema.get("correlation_fields"))))) throw Refused.denied();
    for (var e : correlation.entrySet()) {
      if (!(e.getValue() instanceof String s) || s.isEmpty()) throw Refused.body();
      if (e.getKey().equals("tenant_id") && !identity.get("tenant").equals(e.getValue())) throw Refused.denied();
    }
    String error = Json.string(request,"error_code");
    if ("external_bpmn_error".equals(schema.get("operation"))) {
      if (!Json.list(schema.get("error_codes")).contains(error)) throw Refused.denied();
    } else if (!error.isEmpty()) throw Refused.denied();
    parameters(Json.object(request.get("parameters")));
    return variables;
  }

  private void validateField(Map<String,Object> f, Object value) {
    String name = Json.token(f,"name"), origin = Json.token(f,"origin"), kind = Json.token(f,"kind");
    if (f.get("fixed_json") != null) {
      Object fixed = Json.parse(("{\"v\":" + f.get("fixed_json") + "}").getBytes(StandardCharsets.UTF_8)).get("v");
      if (!Objects.equals(fixed,value)) throw Refused.denied();
    }
    boolean valid = "fixed_null".equals(origin) ? value == null : switch (kind) {
      case "String" -> value instanceof String;
      case "Boolean" -> value instanceof Boolean;
      case "Integer" -> value instanceof Long;
      case "Double" -> value instanceof Double d && Double.isFinite(d);
      case "Json" -> value instanceof Map<?,?> || value instanceof List<?>;
      default -> false;
    };
    if (!valid) throw Refused.body();
    if (value instanceof Map<?,?> m && "Json".equals(kind)) {
      if (m.containsKey("value")) throw Refused.denied();
      for (Object member : Json.list(f.get("null_members"))) if (m.get(member) != null) throw Refused.denied();
    }
    if (origin.equals("deployment_bound")) {
      String identityKey = switch (name) { case "tenant_id" -> "tenant"; case "source_agent_id" -> "workload"; case "source_agent_version" -> "workload_version"; default -> throw Refused.denied(); };
      if (!identity.get(identityKey).equals(value)) throw Refused.denied();
    }
  }
  private void parameters(Map<String,Object> p) {
    switch (Json.token(schema,"operation")) {
      case "fetch_lock":
        Json.keys(p,"maxTasks","lockDuration","asyncResponseTimeout","variables");
        positive(p,"maxTasks"); positive(p,"lockDuration"); positive(p,"asyncResponseTimeout");
        var projection = Json.list(p.get("variables"));
        if (projection.size() != new HashSet<>(projection).size() || !Json.list(schema.get("read_projection")).containsAll(projection)) throw Refused.denied();
        break;
      case "external_failure":
        Json.keys(p,"retries","retryTimeout","errorCategory");
        if (Json.number(p,"retries") < 0 || Json.number(p,"retries") > Integer.MAX_VALUE || Json.number(p,"retryTimeout") < 0
            || !"worker_failure".equals(p.get("errorCategory"))) throw Refused.body(); break;
      case "external_extend_lock": Json.keys(p,"newDuration"); positive(p,"newDuration"); break;
      default: Json.keys(p);
    }
  }
  static void positive(Map<String,Object> m, String key) { if (Json.number(m,key) < 1) throw Refused.body(); }
  /** Convert only declared JSON data. Never enable Java object deserialization or accept valueInfo. */
  static Map<String,Object> engineVariables(Map<String,Object> variables) {
    Map<String,Object> out = new HashMap<>();
    variables.forEach((name,value) -> {
      TypedValue typed;
      if (value == null) typed = Variables.stringValue(null);
      else if (value instanceof String s) typed = Variables.stringValue(s);
      else if (value instanceof Boolean b) typed = Variables.booleanValue(b);
      else if (value instanceof Double d) typed = Variables.doubleValue(d);
      else if (value instanceof Long n) {
        typed = name.equals("total_glosado_candidato_centavos") || n < Integer.MIN_VALUE || n > Integer.MAX_VALUE
            ? Variables.longValue(n) : Variables.integerValue(n.intValue());
      } else {
        // Spin JsonValue is loaded from the actual installed plugin; no ObjectValue fallback.
        try {
          Class<?> spin = Class.forName("org.cibseven.spin.plugin.variable.SpinValues");
          Object builder = spin.getMethod("jsonValue",String.class).invoke(null,new String(Json.bytes(value),StandardCharsets.UTF_8));
          typed = (TypedValue) Class.forName("org.cibseven.spin.plugin.variable.value.builder.JsonValueBuilder").getMethod("create").invoke(builder);
        } catch (ReflectiveOperationException e) { throw Refused.unavailable(); }
      }
      out.put(name,typed);
    });
    return out;
  }
}

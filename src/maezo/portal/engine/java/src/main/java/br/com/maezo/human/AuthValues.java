package br.com.maezo.human;

import java.util.*;
import org.cibseven.bpm.engine.variable.Variables;
import org.cibseven.bpm.engine.variable.value.TypedValue;

/** Closed projection with an explicit digest for native value types as well as logical bytes. */
final class AuthValues {
  private AuthValues() {}
  static Map<String,Object> descriptor(String tenant,String guide,Map<String,Object> facts) {
    var values=PortalReadModels.record(
      "tenant_id",typed("String",tenant),"guide_ref",typed("String",guide),
      "beneficiario_pseudo_id",typed("String",facts.get("beneficiary_pseudo_id")),
      "prestador_id",typed("String",facts.get("provider_ref")),
      "codigo_procedimento_tuss",typed("String",facts.get("procedure_code")),
      "carater_atendimento",typed("String",facts.get("character")),
      "categoria_procedimento",typed("String",facts.get("category")),
      "valor_estimado_brl",typed(AuthDecimalSerializer.NAME,AuthDecimalSerializer.cents(facts.get("claimed_amount_cents")).getValue().toPlainString()),
      "documentos_refs",typed("Json",AuthStore.text(facts.get("document_refs"))),
      "missing_docs",typed("Json",AuthStore.text(facts.get("missing_requirement_codes"))),
      "requer_autorizacao",typed("Boolean",facts.get("requer_autorizacao")),
      "beneficiario_ativo",typed("Boolean",facts.get("beneficiario_ativo")),
      "carencia_cumprida",typed("Boolean",facts.get("carencia_cumprida")),
      "documentacao_completa",typed("Boolean",facts.get("documentacao_completa")));
    return PortalReadModels.record("schema","human-auth-start-projection.v1","variables",values);
  }
  private static Map<String,Object> typed(String type,Object value){return PortalReadModels.record("type",type,"value",value);}
  static Map<String,Object> project(String tenant,String guide,Map<String,Object> facts) {
    var result=new HashMap<String,Object>();var variables=Jcs.object(descriptor(tenant,guide,facts).get("variables"));
    variables.forEach((name,input)->{
      var item=Jcs.object(input);Object value=item.get("value");
      TypedValue nativeValue=switch(Jcs.string(item,"type")) {
        case "String"->Variables.stringValue((String)value);
        case "Boolean"->Variables.booleanValue((Boolean)value);
        case "Json"->json((String)value);
        case AuthDecimalSerializer.NAME->AuthDecimalSerializer.cents(facts.get("claimed_amount_cents"));
        default->throw Rejected.invalid();
      };result.put(name,nativeValue);
    });return result;
  }
  static TypedValue json(String canonical) {
    // Same installed Spin mechanism as the existing workload Capability; never ObjectValue.
    try {
      if(!new String(Jcs.canonical(Jcs.parse(canonical.getBytes(java.nio.charset.StandardCharsets.UTF_8))),java.nio.charset.StandardCharsets.UTF_8).equals(canonical))throw Rejected.invalid();
      Class<?> spin=Class.forName("org.cibseven.spin.plugin.variable.SpinValues");
      Object builder=spin.getMethod("jsonValue",String.class).invoke(null,canonical);
      return (TypedValue)Class.forName("org.cibseven.spin.plugin.variable.value.builder.JsonValueBuilder").getMethod("create").invoke(builder);
    }catch(ReflectiveOperationException failure){throw Rejected.denied();}
  }
}

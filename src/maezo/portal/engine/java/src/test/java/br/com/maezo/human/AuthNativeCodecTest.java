package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.math.BigDecimal;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.cibseven.bpm.engine.impl.variable.serializer.ValueFields;
import org.cibseven.bpm.engine.variable.Variables;
import org.cibseven.bpm.engine.variable.value.NumberValue;
import org.cibseven.bpm.engine.variable.type.*;

/** Pure codecs only; no mocked engine and no claims of native persistence/DMN acceptance. */
class AuthNativeCodecTest {
  static final class Fields implements ValueFields {
    String name="valor_estimado_brl",text,text2;Long integer;Double floating;byte[] bytes;
    public String getName(){return name;}public void setName(String value){name=value;}
    public String getTextValue(){return text;}public void setTextValue(String value){text=value;}
    public String getTextValue2(){return text2;}public void setTextValue2(String value){text2=value;}
    public Long getLongValue(){return integer;}public void setLongValue(Long value){integer=value;}
    public Double getDoubleValue(){return floating;}public void setDoubleValue(Double value){floating=value;}
    public byte[] getByteArrayValue(){return bytes;}public void setByteArrayValue(byte[] value){bytes=value;}
  }
  @Test void exactMoneyBoundariesAndTextCodec() {
    var serializer=new AuthDecimalSerializer();
    String[][] cases={{"0","0.00"},{"1","0.01"},{"100","1.00"},{"9223372036854775807","92233720368547758.07"}};
    for(var pair:cases) {
      var fields=new Fields();fields.integer=1L;fields.floating=1.0;fields.bytes=new byte[]{1};
      serializer.writeValue(AuthDecimalSerializer.cents(pair[0]),fields);
      assertEquals(pair[1],fields.text);assertNull(fields.integer);assertNull(fields.floating);assertNull(fields.bytes);
      var decoded=serializer.readValue(fields,false,false);assertEquals(new BigDecimal(pair[1]),decoded.getValue());
      assertEquals(Map.of("type",AuthDecimalSerializer.NAME,"value",pair[1]),AuthDecimalSerializer.wire(decoded));
    }
    for(Object bad:List.of("-1","01","1.0","9223372036854775808",1L,true))assertThrows(Rejected.class,()->AuthDecimalSerializer.cents(bad));
  }
  @Test void genericNumbersAndResidueDoNotGainCustomType() {
    var serializer=new AuthDecimalSerializer();assertFalse(serializer.canHandle(Variables.doubleValue(1.0)));
    assertNull(AuthDecimalSerializer.wire(Variables.doubleValue(1.0)));
    NumberValue impostor=new NumberValue(){
      public Number getValue(){return new BigDecimal("1.00");}
      public PrimitiveValueType getType(){return ValueType.NUMBER;}
      public boolean isTransient(){return false;}
    };
    assertFalse(serializer.canHandle(impostor));assertNull(AuthDecimalSerializer.wire(impostor));
    for(String text:List.of("1","1.0","01.00","1e0","-0.01","92233720368547758.08")) {
      var f=new Fields();f.text=text;f.text2=AuthDecimalSerializer.NAME;assertThrows(Rejected.class,()->serializer.readValue(f,false,false));
    }
    var f=new Fields();serializer.writeValue(AuthDecimalSerializer.cents("100"),f);f.floating=1.0;
    assertThrows(Rejected.class,()->serializer.readValue(f,false,false));f.floating=null;f.name="other";
    assertThrows(Rejected.class,()->serializer.readValue(f,false,false));
  }
  @Test void projectionMatchesIndependentPythonVectorAndBindsTypes() {
    var facts=PortalReadModels.record("beneficiary_pseudo_id","beneficiary","provider_ref","provider","procedure_code","10101012",
      "character","eletivo","category","consulta","claimed_amount_cents","1","document_refs",List.of(),"missing_requirement_codes",List.of(),
      "requer_autorizacao",true,"beneficiario_ativo",true,"carencia_cumprida",true,"documentacao_completa",true);
    var descriptor=AuthValues.descriptor("tenant","guide",facts);
    assertEquals("d8c158b8ed60bf30ed39b24f2a92229fbc6bb7e4c6540dde0396471dde916db4",PortalReadModels.hash(descriptor));
    var altered=PortalReadModels.copy(descriptor);var variables=Jcs.object(altered.get("variables"));
    Jcs.object(variables.get("valor_estimado_brl")).put("type","String");assertNotEquals(PortalReadModels.hash(descriptor),PortalReadModels.hash(altered));
    assertEquals(14,variables.size());assertEquals("[]",Jcs.object(variables.get("documentos_refs")).get("value"));
  }
  static Map<String,Object> policy() {
    var source=PortalReadModels.record("publisher_ref","publisher","source_ref","source","source_revision","0","source_digest","a".repeat(64),
      "receipt_ref","receipt","observed_at","2026-09-10T00:00:00.000000Z","valid_until","2026-09-11T00:00:00.000000Z");
    return PortalReadModels.record("assessment_ref","assessment","resource_kind","case","resource_ref","case","request_ref","request","request_revision","0",
      "policy",Map.of("artifact_ref","policy","digest","b".repeat(64)),"policy_revision","0","recipient_principal_refs",List.of("principal"),
      "required_codes",List.of("requirement"),"missing_codes",List.of("requirement"),"submitted_response_digest",null,
      "effective_document_refs",List.of(),"document_set_digest",PortalReadModels.hash(List.of()),"complete",false,"source",source,"valid_until","2026-09-11T00:00:00.000000Z");
  }
  @Test void requestOnlyPolicyHasNoFabricatedResponseDigestAndClosedShape() {
    var request=policy();assertDoesNotThrow(()->AuthModels.validate("policy",request));
    request.put("submitted_response_digest","c".repeat(64));assertDoesNotThrow(()->AuthModels.validate("policy",request));
    request.put("request_ref",null);assertThrows(Rejected.class,()->AuthModels.validate("policy",request));
    var intake=policy();intake.put("resource_kind","intake");intake.put("request_ref",null);
    assertDoesNotThrow(()->AuthModels.validate("policy",intake));intake.put("submitted_response_digest","c".repeat(64));
    assertThrows(Rejected.class,()->AuthModels.validate("policy",intake));
  }
}

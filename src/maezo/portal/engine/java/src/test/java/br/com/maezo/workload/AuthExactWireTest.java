package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.math.BigDecimal;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.cibseven.bpm.engine.impl.persistence.entity.VariableInstanceEntity;
import org.cibseven.bpm.engine.variable.Variables;
import org.cibseven.bpm.engine.variable.type.*;
import org.cibseven.bpm.engine.variable.value.*;

class AuthExactWireTest {
  @Test void exactPrivateNativeValueHasLosslessTaggedWire() {
    var fields=new VariableInstanceEntity();fields.setName("valor_estimado_brl");
    fields.setTextValue("92233720368547758.07");fields.setTextValue2("maezo-auth-exact-decimal.v1");
    TypedValue value=new br.com.maezo.human.AuthDecimalSerializer().readValue(fields,false,false);
    assertEquals(Map.of("type","maezo-auth-exact-decimal.v1","value","92233720368547758.07"),WorkloadCommand.wire(value));
  }
  @Test void legacyWireRetainedAndUnknownNumberNotWidened() {
    assertEquals(Map.of("type","double","value",1.25),WorkloadCommand.wire(Variables.doubleValue(1.25)));
    assertEquals(Map.of("type","long","value",7L),WorkloadCommand.wire(Variables.longValue(7L)));
    assertEquals(Map.of("type","string","value","value"),WorkloadCommand.wire(Variables.stringValue("value")));
    NumberValue fake=new NumberValue(){public Number getValue(){return new BigDecimal("1.00");}
      public PrimitiveValueType getType(){return ValueType.NUMBER;}public boolean isTransient(){return false;}};
    assertThrows(Refused.class,()->WorkloadCommand.wire(fake));
  }
}

package br.com.maezo.human;

import java.math.BigDecimal;
import org.cibseven.bpm.engine.impl.variable.serializer.*;
import org.cibseven.bpm.engine.variable.impl.value.UntypedValueImpl;
import org.cibseven.bpm.engine.variable.type.*;
import org.cibseven.bpm.engine.variable.value.*;

/** Exact AUTH money in native TEXT_ columns; no Double or Java ObjectValue fallback. */
public final class AuthDecimalSerializer implements TypedValueSerializer<AuthDecimalSerializer.Value> {
  static final String NAME="maezo-auth-exact-decimal.v1";
  /**
   * Concrete value type. ValueType.NUMBER is ABSTRACT, and CIB's DefaultVariableSerializers refuses every value of an
   * abstract type ("Cannot serialize value of abstract type number") before consulting any serializer — measured in C1.
   */
  static final PrimitiveValueType TYPE=new org.cibseven.bpm.engine.variable.impl.type.PrimitiveValueTypeImpl(NAME,BigDecimal.class){
    private static final long serialVersionUID=1L;
    @Override public TypedValue createValue(Object value,java.util.Map<String,Object> info){throw Rejected.invalid();}
  };
  static final class Value implements NumberValue {
    private static final long serialVersionUID=1L;
    private final BigDecimal value;private final boolean transientValue;
    Value(BigDecimal value,boolean transientValue){this.value=checked(value);this.transientValue=transientValue;}
    @Override public BigDecimal getValue(){return value;}
    @Override public PrimitiveValueType getType(){return TYPE;}
    @Override public boolean isTransient(){return transientValue;}
  }
  /** Read-only export of this serializer's exact private value. Unknown number classes get no adaptation. */
  public static java.util.Map<String,Object> wire(TypedValue value) {
    if(value==null||value.getClass()!=Value.class)return null;
    var exact=(Value)value;if(exact.isTransient())throw Rejected.denied();
    return java.util.Map.of("type",NAME,"value",checked(exact.getValue()).toPlainString());
  }
  static Value cents(Object cents){return new Value(BigDecimal.valueOf(PortalReadModels.number(cents),2),false);}
  static BigDecimal checked(BigDecimal amount) {
    try {if(amount==null||amount.scale()!=2||amount.unscaledValue().longValueExact()<0)throw Rejected.invalid();}
    catch(ArithmeticException failure){throw Rejected.invalid();}return amount;
  }
  @Override public String getName(){return NAME;}
  @Override public ValueType getType(){return TYPE;}
  @Override public String getSerializationDataformat(){return null;}
  @Override public boolean isMutableValue(Value value){return false;}
  @Override public boolean canHandle(TypedValue value){return value!=null&&value.getClass()==Value.class;}
  @Override public Value convertToTypedValue(UntypedValueImpl value){throw Rejected.invalid();}
  @Override public void writeValue(Value value,ValueFields fields) {
    if(!"valor_estimado_brl".equals(fields.getName())||value.isTransient())throw Rejected.denied();
    String text=checked(value.getValue()).toPlainString();
    fields.setTextValue(text);fields.setTextValue2(NAME);fields.setLongValue(null);fields.setDoubleValue(null);fields.setByteArrayValue(null);
  }
  @Override public Value readValue(ValueFields fields,boolean deserialize,boolean transientValue) {
    if(!"valor_estimado_brl".equals(fields.getName())||!NAME.equals(fields.getTextValue2())
        ||fields.getLongValue()!=null||fields.getDoubleValue()!=null||fields.getByteArrayValue()!=null
        ||fields.getTextValue()==null||!fields.getTextValue().matches("(?:0|[1-9][0-9]*)\\.[0-9]{2}"))throw Rejected.denied();
    try{return new Value(new BigDecimal(fields.getTextValue()),transientValue);}
    catch(NumberFormatException failure){throw Rejected.invalid();}
  }
}

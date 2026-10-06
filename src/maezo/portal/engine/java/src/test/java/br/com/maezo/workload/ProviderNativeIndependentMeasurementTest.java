package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.io.Closeable;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Selected pure faults only; these are parser/interface checks, never runtime evidence. */
class ProviderNativeIndependentMeasurementTest {
  private static Object codec(String method, Class<?> argumentType, Object argument) throws Exception {
    Class<?> type = Class.forName(ProviderNativeIndependentMeasurement.class.getName() + "$Codec");
    Method operation = type.getDeclaredMethod(method, argumentType); operation.setAccessible(true);
    try { return operation.invoke(null, argument); }
    catch (InvocationTargetException failure) {
      if (failure.getCause() instanceof RuntimeException error) throw error;
      throw failure;
    }
  }
  private static Object canonical(String value) throws Exception {
    return codec("canonicalParse", byte[].class, value.getBytes(StandardCharsets.UTF_8));
  }
  @Test void concreteTypeHasOneExactTypedConstructorAndNoPublicMutableFields() {
    Class<?> type = ProviderNativeIndependentMeasurement.class;
    assertTrue(Modifier.isPublic(type.getModifiers())); assertTrue(Modifier.isFinal(type.getModifiers()));
    assertArrayEquals(new Class<?>[]{NativeMeasurementPort.class}, type.getInterfaces());
    assertEquals(1, type.getConstructors().length);
    assertArrayEquals(new Class<?>[]{NativeMeasurementConfiguration.class}, type.getConstructors()[0].getParameterTypes());
    assertEquals(0, type.getFields().length);
  }
  @Test void canonicalBytesPreserveNestedKeysNullAndActualJvmSpaces() throws Exception {
    byte[] encoded = (byte[]) codec("encode", Object.class,
        Map.of("z", List.of(17L, true), "a", "OpenJDK 64-Bit Server VM", "unicode", "a\u00e9\ud83d\ude00"));
    assertEquals("{\"a\":\"OpenJDK 64-Bit Server VM\",\"unicode\":\"a\\u00e9\\ud83d\\ude00\",\"z\":[17,true]}",
        new String(encoded, StandardCharsets.US_ASCII));
    assertEquals(Map.of("tuple", java.util.Arrays.asList(null, false, 0L)), canonical("{\"tuple\":[null,false,0]}"));
  }
  @Test void duplicateKeysAndNoncanonicalOrNonintegerFramesRefuseBeforeUse() {
    for (String raw : List.of("{\"x\":1,\"x\":2}", "{\"z\":1,\"a\":2}", "{ \"x\":1}",
        "{\"x\":01}", "{\"x\":1.0}", "{\"x\":1e0}", "{\"x\":-0}", "{\"x\":9223372036854775808}",
        "{\"x\":\"\\u0061\"}", "{\"x\":1}\n", "{\"x\":1}{}", "[true,]", "{\"x\":true,}",
        "{\"x\":\"\\ud800\"}", "{\"x\":\"\\udc00\"}")) {
      assertThrows(IllegalStateException.class, () -> canonical(raw), raw);
    }
  }
  @Test void malformedUtf8RefusesAndBooleanIsNotAnInteger() throws Exception {
    assertThrows(IllegalStateException.class, () -> codec("canonicalParse", byte[].class, new byte[]{(byte) 0xc3, 0x28}));
    Class<?> type = Class.forName(ProviderNativeIndependentMeasurement.class.getName() + "$Codec");
    Method integer = type.getDeclaredMethod("integer", Map.class, String.class); integer.setAccessible(true);
    InvocationTargetException failure = assertThrows(InvocationTargetException.class, () -> integer.invoke(null, Map.of("n", true), "n"));
    assertInstanceOf(IllegalStateException.class, failure.getCause());
  }
  private static void closeAll(List<Closeable> resources)throws Throwable {
    Method operation=ProviderNativeIndependentMeasurement.class.getDeclaredMethod("closeAll",List.class);
    operation.setAccessible(true);
    try {operation.invoke(null,resources);}catch(InvocationTargetException wrapped){throw wrapped.getCause();}
  }
  @Test void uncheckedCloseFailureCannotPreventRemainingOwnedCloses() {
    var calls=new ArrayList<String>();var primary=new AssertionError("unit-only-first-close");
    Throwable thrown=assertThrows(Throwable.class,()->closeAll(List.of(
        ()->{calls.add("primary");throw primary;},()->calls.add("outcome"),()->calls.add("selector"))));
    assertEquals(List.of("primary","outcome","selector"),calls);
    assertSame(primary,thrown.getCause());
  }
  @Test void repeatedSameCleanupThrowableCannotMaskFirstFailure() {
    var primary=new IOException("unit-only-reused-close");
    Throwable thrown=assertThrows(Throwable.class,()->closeAll(List.of(()->{throw primary;},()->{throw primary;})));
    assertInstanceOf(IllegalStateException.class,thrown);assertSame(primary,thrown.getCause());assertEquals(0,primary.getSuppressed().length);
  }
  @Test void successfulResourceCleanupClosesEveryHandle()throws Throwable {
    var calls=new ArrayList<String>();closeAll(List.of(()->calls.add("primary"),()->calls.add("outcome"),()->calls.add("selector")));
    assertEquals(List.of("primary","outcome","selector"),calls);
  }

  private static void closeUntransferred(AutoCloseable resource,Throwable primary)throws Throwable {
    Method operation=ProviderNativeIndependentMeasurement.class.getDeclaredMethod("closeUntransferred",AutoCloseable.class,Throwable.class);
    operation.setAccessible(true);
    try {operation.invoke(null,resource,primary);}catch(InvocationTargetException wrapped){throw wrapped.getCause();}
  }
  @Test void failedLocalAcquisitionClosesHandleWithoutMaskingPrimaryError()throws Throwable {
    var primary=new AssertionError("unit-only-acquisition");var cleanup=new AssertionError("unit-only-close");var calls=new ArrayList<String>();
    closeUntransferred(()->{calls.add("close");throw cleanup;},primary);
    assertEquals(List.of("close"),calls);assertArrayEquals(new Throwable[]{cleanup},primary.getSuppressed());
  }
  @Test void failedPreparedStatementCleanupRetainsOriginalSqlFailure()throws Throwable {
    var primary=new java.sql.SQLException("unit-only-query-timeout");var cleanup=new java.sql.SQLException("unit-only-statement-close");
    closeUntransferred(()->{throw cleanup;},primary);assertArrayEquals(new Throwable[]{cleanup},primary.getSuppressed());
  }
  @Test void localCleanupCannotSelfSuppressOrReplaceRuntimePrimary()throws Throwable {
    var primary=new IllegalStateException("unit-only-runtime");closeUntransferred(()->{throw primary;},primary);
    assertEquals(0,primary.getSuppressed().length);
  }
  @Test void everyCleanupFailureIsRetainedAfterEveryCloseAttempt() {
    var first=new IOException("unit-only-primary");var second=new IllegalStateException("unit-only-outcome");var third=new AssertionError("unit-only-selector");var calls=new ArrayList<String>();
    var thrown=assertThrows(IllegalStateException.class,()->closeAll(List.of(
        ()->{calls.add("primary");throw first;},()->{calls.add("outcome");throw second;},()->{calls.add("selector");throw third;})));
    assertEquals(List.of("primary","outcome","selector"),calls);assertSame(first,thrown.getCause());
    assertArrayEquals(new Throwable[]{second,third},first.getSuppressed());
  }

}

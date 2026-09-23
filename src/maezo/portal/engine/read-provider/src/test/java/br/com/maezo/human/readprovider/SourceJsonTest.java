package br.com.maezo.human.readprovider;

import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** The strict reader of the one foreign JSON (portal_memberships.payload). */
class SourceJsonTest {
  static Object parse(String text) {
    return SourceJson.parse(text.getBytes(StandardCharsets.UTF_8));
  }

  static void refused(String text) {
    Rejected r = assertThrows(Rejected.class, () -> parse(text), text);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  @Test
  void readsTheShapesPydanticWrites() {
    var value = parse(" {\"a\":1,\"b\":[true,false,null,\"x\"],\"c\":{\"d\":-7},\"e\":0}\n");
    assertEquals(Map.of("a", 1L, "b", java.util.Arrays.asList(true, false, null, "x"), "c",
        Map.of("d", -7L), "e", 0L), value);
    assertEquals(9007199254740993L, parse("9007199254740993"));
    assertEquals(Long.MAX_VALUE, parse(Long.toString(Long.MAX_VALUE)));
    assertEquals(List.of(), parse("[]"));
    assertEquals(Map.of(), parse("{}"));
  }

  @Test
  void decodesEveryJsonEscapeAndPairedSurrogates() {
    assertEquals("\"\\/\b\f\n\r\t\u00e7\uD83D\uDE00",
        parse("\"\\\"\\\\\\/\\b\\f\\n\\r\\t\\u00e7\\ud83d\\uDE00\""));
    assertEquals("a\u00e7\uD83D\uDE00", parse("\"a\u00e7\uD83D\uDE00\""));
  }

  @Test
  void refusesAnythingLooserThanStrictJsonIntegers() {
    for (String bad : List.of("1.0", "1e2", "1E2", "01", "-0", "-", "+1", "9223372036854775808",
             "-9223372036854775809", "99999999999999999999", ".5", "NaN", "Infinity"))
      refused(bad);
  }

  @Test
  void refusesMalformedStructure() {
    for (String bad : List.of("", " ", "{", "[", "{\"a\":1,}", "[1,]", "{\"a\" 1}", "{a:1}",
             "{\"a\":1}{", "{\"a\":1} x", "[1 2]", "tru", "nul", "'x'", "\"x", "{\"a\":1,\"a\":2}",
             "\"\\x\"", "\"\\u12\"", "\"\\u12G4\"", "\"a\u0001\"", "\"\\ud83d\"", "\"\\ude00\"",
             "\"\\ud83d\\u0041\""))
      refused(bad);
  }

  @Test
  void refusesInvalidUtf8TooDeepAndTooBig() {
    Rejected r = assertThrows(Rejected.class,
        () -> SourceJson.parse(new byte[] {'"', (byte) 0xC3, (byte) 0x28, '"'}));
    assertEquals(503, r.status);
    assertThrows(Rejected.class, () -> SourceJson.parse(null));
    refused("[".repeat(SourceJson.MAX_DEPTH + 2) + "]".repeat(SourceJson.MAX_DEPTH + 2));
    parse("[".repeat(SourceJson.MAX_DEPTH + 1) + "]".repeat(SourceJson.MAX_DEPTH + 1));
    byte[] big = ("\"" + "a".repeat(SourceJson.MAX_BYTES) + "\"").getBytes(StandardCharsets.UTF_8);
    assertThrows(Rejected.class, () -> SourceJson.parse(big));
  }
}

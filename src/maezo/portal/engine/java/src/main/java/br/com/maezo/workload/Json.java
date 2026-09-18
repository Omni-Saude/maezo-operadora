package br.com.maezo.workload;

import br.com.maezo.human.Jcs;
import com.fasterxml.jackson.core.*;
import java.io.*;
import java.nio.*;
import java.nio.charset.*;
import java.util.*;

/** D7-A bounded JSON profile, separate from the number-free signed human JCS profile. */
public final class Json {
  public static final int LIMIT = 1_048_576;
  private Json() {}
  public static Map<String, Object> parse(byte[] raw) {
    if (raw.length > LIMIT) throw Refused.body();
    try {
      String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
          .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(raw)).toString();
      try (JsonParser parser = new JsonFactory().createParser(text)) {
        parser.nextToken();
        Object value = read(parser, 0);
        if (parser.nextToken() != null) throw Refused.body();
        bytes(value); // Unicode, finite-number and output bound check.
        return object(value);
      }
    } catch (IOException | IllegalArgumentException e) { throw Refused.body(); }
  }
  private static Object read(JsonParser p, int depth) throws IOException {
    if (depth > 16 || p.currentToken() == null) throw Refused.body();
    switch (p.currentToken()) {
      case START_OBJECT:
        Map<String,Object> m = new TreeMap<>();
        while (p.nextToken() != JsonToken.END_OBJECT) {
          if (p.currentToken() != JsonToken.FIELD_NAME || m.containsKey(p.currentName())) throw Refused.body();
          String k = p.currentName(); p.nextToken(); m.put(k, read(p, depth + 1));
        }
        return Collections.unmodifiableMap(m);
      case START_ARRAY:
        List<Object> values = new ArrayList<>();
        while (p.nextToken() != JsonToken.END_ARRAY) values.add(read(p, depth + 1));
        return Collections.unmodifiableList(values);
      case VALUE_STRING: return p.getText();
      case VALUE_TRUE: return true;
      case VALUE_FALSE: return false;
      case VALUE_NULL: return null;
      case VALUE_NUMBER_INT: return p.getLongValue();
      case VALUE_NUMBER_FLOAT:
        double n = p.getDoubleValue(); if (!Double.isFinite(n)) throw Refused.body(); return n;
      default: throw Refused.body();
    }
  }
  public static byte[] bytes(Object value) {
    StringBuilder out = new StringBuilder(); write(value, out, 0);
    byte[] result = out.toString().getBytes(StandardCharsets.UTF_8);
    if (result.length > LIMIT) throw Refused.body();
    return result;
  }
  private static void write(Object value, StringBuilder out, int depth) {
    if (depth > 16) throw Refused.body();
    if (value instanceof Map<?,?> m) {
      out.append('{'); boolean first = true;
      for (var e : new TreeMap<>(object(m)).entrySet()) {
        if (!first) out.append(','); first = false;
        write(e.getKey(), out, depth + 1); out.append(':'); write(e.getValue(), out, depth + 1);
      }
      out.append('}');
    } else if (value instanceof List<?> list) {
      out.append('['); boolean first = true;
      for (Object v : list) { if (!first) out.append(','); first = false; write(v, out, depth + 1); }
      out.append(']');
    } else if (value instanceof Long || value instanceof Integer) out.append(value);
    else if (value instanceof Double d) { if (!Double.isFinite(d)) throw Refused.body(); out.append(d); }
    else if (value == null || value instanceof Boolean || value instanceof String) {
      try { out.append(new String(Jcs.canonical(value), StandardCharsets.UTF_8)); }
      catch (RuntimeException e) { throw Refused.body(); }
    } else throw Refused.body();
    if (out.length() > LIMIT) throw Refused.body();
  }
  @SuppressWarnings("unchecked")
  public static Map<String,Object> object(Object value) {
    if (!(value instanceof Map<?,?> m) || m.keySet().stream().anyMatch(k -> !(k instanceof String))) throw Refused.body();
    return (Map<String,Object>) m;
  }
  public static List<?> list(Object value) { if (!(value instanceof List<?> v)) throw Refused.body(); return v; }
  public static String string(Map<String,Object> m, String name) {
    if (!(m.get(name) instanceof String s)) throw Refused.body(); return s;
  }
  public static String token(Map<String,Object> m, String name) {
    String s = string(m,name); if (!s.matches("[!-~]{1,256}") || s.contains("*")) throw Refused.body(); return s;
  }
  public static long number(Map<String,Object> m, String name) {
    if (!(m.get(name) instanceof Long n)) throw Refused.body(); return n;
  }
  public static boolean bool(Map<String,Object> m, String name) {
    if (!(m.get(name) instanceof Boolean v)) throw Refused.body(); return v;
  }
  public static void keys(Map<String,Object> m, String... names) {
    if (!m.keySet().equals(Set.of(names))) throw Refused.body();
  }
  public static String digest(Object value) { return Jcs.digest(bytes(value)); }
}

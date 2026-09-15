package br.com.maezo.human;

import com.fasterxml.jackson.core.*;
import java.io.*;
import java.nio.*;
import java.nio.charset.*;
import java.security.*;
import java.util.*;

/**
 * RFC8785 restricted number-free human-envelope.v1 profile (ADR0049 D5). No Unicode normalization
 * or custom object coercion; natural Java order is UTF-16.
 */
public final class Jcs {
  private Jcs() {}

  public static Object parse(byte[] raw) {
    if (raw.length > 65536) throw Rejected.invalid();
    try {
      String text =
          StandardCharsets.UTF_8
              .newDecoder()
              .onMalformedInput(CodingErrorAction.REPORT)
              .onUnmappableCharacter(CodingErrorAction.REPORT)
              .decode(ByteBuffer.wrap(raw))
              .toString();
      try (JsonParser p = new JsonFactory().createParser(text)) {
        p.nextToken();
        Object value = read(p, 0);
        if (p.nextToken() != null) throw Rejected.invalid();
        canonical(value);
        return value;
      }
    } catch (IOException | IllegalArgumentException ex) {
      throw Rejected.invalid();
    }
  }

  private static Object read(JsonParser p, int depth) throws IOException {
    if (depth > 32 || p.currentToken() == null) throw Rejected.invalid();
    switch (p.currentToken()) {
      case START_OBJECT:
        Map<String, Object> map = new TreeMap<>();
        while (p.nextToken() != JsonToken.END_OBJECT) {
          if (p.currentToken() != JsonToken.FIELD_NAME) throw Rejected.invalid();
          String k = p.currentName();
          if (map.containsKey(k)) throw Rejected.invalid();
          p.nextToken();
          map.put(k, read(p, depth + 1));
        }
        return map;
      case START_ARRAY:
        List<Object> list = new ArrayList<>();
        while (p.nextToken() != JsonToken.END_ARRAY) list.add(read(p, depth + 1));
        return list;
      case VALUE_STRING:
        return p.getText();
      case VALUE_TRUE:
        return true;
      case VALUE_FALSE:
        return false;
      case VALUE_NULL:
        return null;
      default:
        throw Rejected.invalid();
    }
  }

  public static byte[] canonical(Object value) {
    StringBuilder out = new StringBuilder();
    write(value, out, 0);
    return out.toString().getBytes(StandardCharsets.UTF_8);
  }

  private static void write(Object value, StringBuilder out, int depth) {
    if (depth > 32) throw Rejected.invalid();
    if (value == null) {
      out.append("null");
      return;
    }
    if (value instanceof String s) {
      quote(s, out);
      return;
    }
    if (value instanceof Boolean b) {
      out.append(b);
      return;
    }
    if (value instanceof List<?> list) {
      out.append('[');
      boolean first = true;
      for (Object v : list) {
        if (!first) out.append(',');
        first = false;
        write(v, out, depth + 1);
      }
      out.append(']');
      return;
    }
    if (value instanceof Map<?, ?> map) {
      TreeMap<String, Object> sorted = new TreeMap<>();
      map.forEach(
          (k, v) -> {
            if (!(k instanceof String)) throw Rejected.invalid();
            sorted.put((String) k, v);
          });
      out.append('{');
      boolean first = true;
      for (var e : sorted.entrySet()) {
        if (!first) out.append(',');
        first = false;
        quote(e.getKey(), out);
        out.append(':');
        write(e.getValue(), out, depth + 1);
      }
      out.append('}');
      return;
    }
    throw Rejected.invalid();
  }

  private static void quote(String s, StringBuilder out) {
    out.append('"');
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      if (Character.isHighSurrogate(c)) {
        if (i + 1 >= s.length() || !Character.isLowSurrogate(s.charAt(i + 1)))
          throw Rejected.invalid();
        out.append(c).append(s.charAt(++i));
        continue;
      }
      if (Character.isLowSurrogate(c)) throw Rejected.invalid();
      switch (c) {
        case '"':
          out.append("\\\"");
          break;
        case '\\':
          out.append("\\\\");
          break;
        case '\b':
          out.append("\\b");
          break;
        case '\t':
          out.append("\\t");
          break;
        case '\n':
          out.append("\\n");
          break;
        case '\f':
          out.append("\\f");
          break;
        case '\r':
          out.append("\\r");
          break;
        default:
          if (c < 32) out.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
          else out.append(c);
      }
    }
    out.append('"');
  }

  public static String digest(byte[] bytes) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    } catch (NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  @SuppressWarnings("unchecked")
  public static Map<String, Object> object(Object value) {
    if (!(value instanceof Map<?, ?>)) throw Rejected.invalid();
    return (Map<String, Object>) value;
  }

  public static String string(Map<String, Object> m, String key) {
    Object v = m.get(key);
    if (!(v instanceof String s) || s.isEmpty() || s.chars().anyMatch(c -> c < 32 || c == 127))
      throw Rejected.invalid();
    canonical(s);
    return s;
  }

  static String ref(Map<String, Object> m, String key) {
    String s = string(m, key);
    if (!s.matches("[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}")) throw Rejected.invalid();
    return s;
  }

  static String decimal(Map<String, Object> m, String key) {
    String s = string(m, key);
    if (!s.matches("0|[1-9][0-9]*")) throw Rejected.invalid();
    return s;
  }

  static long seconds(Map<String, Object> m, String key) {
    try {
      return Long.parseLong(decimal(m, key));
    } catch (NumberFormatException ex) {
      throw Rejected.invalid();
    }
  }

  static String hash(Map<String, Object> m, String key) {
    String s = string(m, key);
    if (!s.matches("[0-9a-f]{64}")) throw Rejected.invalid();
    return s;
  }

  static void keys(Map<String, Object> map, String... keys) {
    if (!map.keySet().equals(Set.of(keys))) throw Rejected.invalid();
  }
}

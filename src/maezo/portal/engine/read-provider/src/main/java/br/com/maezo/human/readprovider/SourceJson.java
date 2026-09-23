package br.com.maezo.human.readprovider;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Strict reader for the ONE foreign JSON the provider reads: {@code amh.portal_memberships.payload},
 * written by the portal administration plane with pydantic {@code model_dump_json()}. Unlike the
 * engine's number-free profile ({@code Jcs.parse}), that document carries JSON integers
 * ({@code "revision":3}), so this reader accepts integers and nothing looser: no fractions, no
 * exponents, no leading zeros, no duplicate names, no invalid UTF-8, no lone surrogate. Integers
 * come back as {@link Long}; anything that does not fit is refused.
 */
final class SourceJson {
  static final int MAX_BYTES = 65536;
  static final int MAX_DEPTH = 16;

  private final String text;
  private int at;

  private SourceJson(String text) {
    this.text = text;
  }

  static Object parse(byte[] raw) {
    if (raw == null || raw.length > MAX_BYTES)
      throw Fields.unavailable();
    String text;
    try {
      text = StandardCharsets.UTF_8.newDecoder()
                 .onMalformedInput(CodingErrorAction.REPORT)
                 .onUnmappableCharacter(CodingErrorAction.REPORT)
                 .decode(ByteBuffer.wrap(raw))
                 .toString();
    } catch (CharacterCodingException malformed) {
      throw Fields.unavailable();
    }
    var reader = new SourceJson(text);
    reader.space();
    Object value = reader.value(0);
    reader.space();
    if (reader.at != text.length())
      throw Fields.unavailable();
    return value;
  }

  private void space() {
    while (at < text.length()) {
      char c = text.charAt(at);
      if (c != ' ' && c != '\t' && c != '\n' && c != '\r')
        return;
      at++;
    }
  }

  private char peek() {
    if (at >= text.length())
      throw Fields.unavailable();
    return text.charAt(at);
  }

  private void expect(String literal) {
    if (!text.startsWith(literal, at))
      throw Fields.unavailable();
    at += literal.length();
  }

  private Object value(int depth) {
    if (depth > MAX_DEPTH)
      throw Fields.unavailable();
    char c = peek();
    switch (c) {
      case '{':
        return object(depth);
      case '[':
        return array(depth);
      case '"':
        return string();
      case 't':
        expect("true");
        return Boolean.TRUE;
      case 'f':
        expect("false");
        return Boolean.FALSE;
      case 'n':
        expect("null");
        return null;
      default:
        if (c == '-' || (c >= '0' && c <= '9'))
          return integer();
        throw Fields.unavailable();
    }
  }

  private Map<String, Object> object(int depth) {
    at++;
    Map<String, Object> out = new TreeMap<>();
    space();
    if (peek() == '}') {
      at++;
      return out;
    }
    while (true) {
      space();
      if (peek() != '"')
        throw Fields.unavailable();
      String key = string();
      space();
      expect(":");
      space();
      Object v = value(depth + 1);
      if (out.containsKey(key))
        throw Fields.unavailable();
      out.put(key, v);
      space();
      char c = peek();
      at++;
      if (c == '}')
        return out;
      if (c != ',')
        throw Fields.unavailable();
    }
  }

  private List<Object> array(int depth) {
    at++;
    List<Object> out = new ArrayList<>();
    space();
    if (peek() == ']') {
      at++;
      return out;
    }
    while (true) {
      space();
      out.add(value(depth + 1));
      space();
      char c = peek();
      at++;
      if (c == ']')
        return out;
      if (c != ',')
        throw Fields.unavailable();
    }
  }

  private Long integer() {
    int start = at;
    if (peek() == '-')
      at++;
    if (peek() == '0') {
      at++;
    } else {
      if (peek() < '1' || peek() > '9')
        throw Fields.unavailable();
      while (at < text.length() && text.charAt(at) >= '0' && text.charAt(at) <= '9') at++;
    }
    if (at < text.length()) {
      char c = text.charAt(at);
      if (c == '.' || c == 'e' || c == 'E')
        throw Fields.unavailable();
    }
    String digits = text.substring(start, at);
    if (digits.equals("-0") || digits.length() > 19)
      throw Fields.unavailable();
    try {
      return Long.parseLong(digits);
    } catch (NumberFormatException overflow) {
      throw Fields.unavailable();
    }
  }

  private String string() {
    at++;
    StringBuilder out = new StringBuilder();
    while (true) {
      char c = peek();
      at++;
      if (c == '"')
        break;
      if (c < 0x20)
        throw Fields.unavailable();
      if (c != '\\') {
        out.append(c);
        continue;
      }
      char e = peek();
      at++;
      switch (e) {
        case '"' -> out.append('"');
        case '\\' -> out.append('\\');
        case '/' -> out.append('/');
        case 'b' -> out.append('\b');
        case 'f' -> out.append('\f');
        case 'n' -> out.append('\n');
        case 'r' -> out.append('\r');
        case 't' -> out.append('\t');
        case 'u' -> {
          if (at + 4 > text.length())
            throw Fields.unavailable();
          String hex = text.substring(at, at + 4);
          if (!hex.matches("[0-9A-Fa-f]{4}"))
            throw Fields.unavailable();
          out.append((char) Integer.parseInt(hex, 16));
          at += 4;
        }
        default -> throw Fields.unavailable();
      }
    }
    String s = out.toString();
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      if (Character.isHighSurrogate(c)) {
        if (i + 1 >= s.length() || !Character.isLowSurrogate(s.charAt(i + 1)))
          throw Fields.unavailable();
        i++;
      } else if (Character.isLowSurrogate(c)) {
        throw Fields.unavailable();
      }
    }
    return s;
  }
}

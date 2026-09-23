package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.Rejected;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.time.DateTimeException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Closed, number-free field readers for the provider's own records. Every refusal is the single
 * bounded code the read plugin already maps to 503 ({@code READ_DEPENDENCY_UNAVAILABLE}): the
 * provider never distinguishes "malformed" from "not admitted" to its caller.
 */
final class Fields {
  private Fields() {}

  static final int MAX_FILE = 65536;
  private static final DateTimeFormatter TIME =
      DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSS'Z'", Locale.ROOT)
          .withZone(ZoneOffset.UTC);

  static Rejected unavailable() {
    return new Rejected(503, "READ_DEPENDENCY_UNAVAILABLE");
  }

  static Map<String, Object> object(Object value) {
    try {
      return Jcs.object(value);
    } catch (Rejected malformed) {
      throw unavailable();
    }
  }

  static void keys(Map<String, Object> map, String... names) {
    if (!map.keySet().equals(Set.of(names)))
      throw unavailable();
  }

  static String string(Map<String, Object> map, String key) {
    Object v = map.get(key);
    if (!(v instanceof String s) || s.isEmpty() || s.length() > 4096
        || s.chars().anyMatch(c -> c < 32 || c == 127))
      throw unavailable();
    return s;
  }

  static String exact(Map<String, Object> map, String key, String expected) {
    String s = string(map, key);
    if (!s.equals(expected))
      throw unavailable();
    return s;
  }

  static String ref(Map<String, Object> map, String key) {
    String s = string(map, key);
    if (!s.matches("[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}"))
      throw unavailable();
    return s;
  }

  static String hash(Map<String, Object> map, String key) {
    String s = string(map, key);
    if (!s.matches("[0-9a-f]{64}"))
      throw unavailable();
    return s;
  }

  /** Lower-case PostgreSQL identifier, compared EXACTLY (never by prefix, ADR-0060). */
  static String identifier(Map<String, Object> map, String key) {
    String s = string(map, key);
    if (!s.matches("[a-z_][a-z0-9_]{0,62}"))
      throw unavailable();
    return s;
  }

  static long decimal(Map<String, Object> map, String key, long min, long max) {
    String s = string(map, key);
    if (!s.matches("0|[1-9][0-9]{0,17}"))
      throw unavailable();
    long n = Long.parseLong(s);
    if (n < min || n > max)
      throw unavailable();
    return n;
  }

  static Instant time(Map<String, Object> map, String key) {
    return time(map.get(key));
  }

  static Instant time(Object value) {
    if (!(value instanceof String s)
        || !s.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z"))
      throw unavailable();
    try {
      Instant t = Instant.parse(s);
      if (!format(t).equals(s))
        throw unavailable();
      return t;
    } catch (DateTimeException ex) {
      throw unavailable();
    }
  }

  static String format(Instant value) {
    return TIME.format(value.truncatedTo(ChronoUnit.MICROS));
  }

  static byte[] base64(Map<String, Object> map, String key, int size) {
    String s = string(map, key);
    try {
      byte[] raw = Base64.getDecoder().decode(s);
      if (!Base64.getEncoder().encodeToString(raw).equals(s) || (size >= 0 && raw.length != size)
          || raw.length > MAX_FILE)
        throw unavailable();
      return raw;
    } catch (IllegalArgumentException ex) {
      throw unavailable();
    }
  }

  static Path absolute(Map<String, Object> map, String key) {
    String s = string(map, key);
    try {
      Path p = Path.of(s);
      if (!p.isAbsolute() || !p.normalize().toString().equals(p.toString()))
        throw unavailable();
      return p;
    } catch (java.nio.file.InvalidPathException ex) {
      throw unavailable();
    }
  }

  @SuppressWarnings("unchecked")
  static List<Object> list(Object value) {
    if (!(value instanceof List<?>))
      throw unavailable();
    return (List<Object>) value;
  }

  static Set<String> distinctStrings(Object value, Set<String> allowed) {
    Set<String> out = new HashSet<>();
    for (Object o : list(value))
      if (!(o instanceof String s) || !allowed.contains(s) || !out.add(s))
        throw unavailable();
    if (out.isEmpty())
      throw unavailable();
    return Set.copyOf(out);
  }

  /**
   * Number-free JSON object (the engine's own {@link Jcs#parse} profile: duplicate keys refused)
   * read from a bounded, regular, non-symlink file. Mounted files are not signed, so whitespace is
   * tolerated; only the signed admission row is required to be byte-canonical.
   */
  static Map<String, Object> jsonFile(Path file) {
    try {
      if (file == null || Files.isSymbolicLink(file)
          || !Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)
          || Files.size(file) > MAX_FILE)
        throw unavailable();
      return Jcs.object(Jcs.parse(Files.readAllBytes(file)));
    } catch (IOException | Rejected ex) {
      throw unavailable();
    }
  }

  /** Parses and requires the bytes to be exactly their own JCS form (nothing unsigned rides along). */
  static Map<String, Object> canonical(byte[] raw) {
    try {
      if (raw == null || raw.length > MAX_FILE)
        throw unavailable();
      Map<String, Object> value = Jcs.object(Jcs.parse(raw));
      if (!java.util.Arrays.equals(Jcs.canonical(value), raw))
        throw unavailable();
      return value;
    } catch (Rejected malformed) {
      throw unavailable();
    }
  }

  static String digest(Object value) {
    return Jcs.digest(Jcs.canonical(value));
  }
}

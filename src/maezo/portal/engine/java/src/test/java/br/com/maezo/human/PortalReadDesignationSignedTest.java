package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.sql.Timestamp;
import java.util.*;
import org.junit.jupiter.api.Test;

/** C1 (item 4): a designation changed by direct UPDATE no longer matches its signed catalog row. */
class PortalReadDesignationSignedTest {
  static Map<String, Object> row() {
    var until = Timestamp.valueOf("2026-09-30 00:00:00");
    var r = new HashMap<String, Object>();
    r.put("digest_", "a".repeat(64)); r.put("source_", "{\"s\":1}"); r.put("publisher_", "pub");
    r.put("publication_", "p1"); r.put("valid_until_", until);
    for (String k : PortalReadCommand.SIGNED) r.put("signed_" + k, r.get(k) instanceof Timestamp t ? new Timestamp(t.getTime()) : r.get(k));
    return r;
  }
  @Test void untouchedDesignationPasses() { assertDoesNotThrow(() -> PortalReadCommand.requireSigned(row())); }
  @Test void everyDirectlyUpdatedFieldIsRefused() {
    Map<String, Object> changed = Map.of("digest_", "b".repeat(64), "source_", "{\"s\":2}", "publisher_", "other",
        "publication_", "p2", "valid_until_", Timestamp.valueOf("2099-01-01 00:00:00"));
    for (var e : changed.entrySet()) {
      var r = row(); r.put(e.getKey(), e.getValue());
      assertThrows(RuntimeException.class, () -> PortalReadCommand.requireSigned(r), e.getKey());
      var missing = row(); missing.remove("signed_" + e.getKey());
      assertThrows(RuntimeException.class, () -> PortalReadCommand.requireSigned(missing), e.getKey());
    }
  }
}

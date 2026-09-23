package br.com.maezo.human;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.util.*;

/**
 * D-J.4 (plan portal-autoridade-nativa-dev): the owner script installs the AUTH and consumer-lineage
 * tables before the engine's installer runs. The installer accepts pre-existing tables only when
 * their catalog (columns, types, defaults, constraints, indexes, owner, table and column grants,
 * triggers, rules, RLS) digests to the committed pin; any divergence is refused. Role names are
 * normalized to owner/runtime/PUBLIC so the pin is independent of the deployment's login names.
 */
final class NativeCatalogPin {
  private NativeCatalogPin() {}
  static final String AUTH = "auth";
  static final String CONSUMER = "consumer-lineage";

  static String resource(String name) {
    try (var in = NativeCatalogPin.class.getResourceAsStream(name)) {
      if (in == null) throw EngineStore.unavailable();
      return new String(in.readAllBytes(), StandardCharsets.UTF_8);
    } catch (IOException failure) {
      throw EngineStore.unavailable();
    }
  }

  /** The committed pin for one table family: 64 lowercase hex, one line. */
  static String pinned(String family) {
    String pin = resource("/native-catalog-pin-" + family + ".sha256").strip();
    if (!pin.matches("[0-9a-f]{64}")) throw EngineStore.unavailable();
    return pin;
  }

  static String digest(Connection c, String schema, List<String> tables, String owner, String runtime) {
    for (String t : tables) if (!t.matches("[a-z_][a-z0-9_]{0,62}")) throw Rejected.invalid();
    var row = ConsumerEdgeInstallation.one(c, resource("/native-catalog-digest-postgres.sql"),
        schema, String.join(",", tables), owner, runtime);
    if (((Number) row.get("relations")).longValue() != tables.size()) throw Rejected.conflict();
    return (String) row.get("digest");
  }

  /**
   * true: every table is absent (caller installs); false: every table exists and matches the pin
   * (caller skips DDL and grants). Partial presence or a divergent catalog is a conflict.
   */
  static boolean absent(Connection c, String family, String schema, List<String> tables, String owner, String runtime) {
    int missing = 0;
    for (String t : tables)
      if (Boolean.TRUE.equals(ConsumerEdgeInstallation.one(c, "SELECT to_regclass(?) IS NULL AS absent",
          "\"" + schema + "\"." + t).get("absent"))) missing++;
    if (missing == tables.size()) return true;
    if (missing != 0 || !pinned(family).equals(digest(c, schema, tables, owner, runtime))) throw Rejected.conflict();
    return false;
  }
}

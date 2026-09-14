package br.com.maezo.human;

import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import org.apache.ibatis.exceptions.PersistenceException;
import org.apache.ibatis.session.SqlSession;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** ADR0049 D5: exclusively the CIB MyBatis connection, no DataSource.getConnection(). */
final class EngineStore {
  final Connection connection;
  final String tenant;
  private final SqlSession session;

  EngineStore(CommandContext context, String tenant) {
    session = context.getDbSqlSession().getSqlSession();
    connection = session.getConnection();
    this.tenant = tenant;
    try {
      if (connection.getAutoCommit()
          || !"PostgreSQL".equals(connection.getMetaData().getDatabaseProductName()))
        throw new IllegalStateException("human commands require enlisted PostgreSQL transaction");
    } catch (SQLException ex) {
      throw unavailable();
    }
  }

  static IllegalStateException unavailable() {
    return new IllegalStateException("human engine store unavailable");
  }

  List<Map<String, Object>> rows(String sql, Object... args) {
    try (PreparedStatement ps = connection.prepareStatement(sql)) {
      for (int i = 0; i < args.length; i++) ps.setObject(i + 1, args[i]);
      try (ResultSet rs = ps.executeQuery()) {
        List<Map<String, Object>> result = new ArrayList<>();
        while (rs.next()) {
          Map<String, Object> row = new HashMap<>();
          for (int i = 1; i <= rs.getMetaData().getColumnCount(); i++)
            row.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT), rs.getObject(i));
          result.add(row);
        }
        return result;
      }
    } catch (SQLException ex) {
      throw unavailable();
    }
  }

  int update(String sql, Object... args) {
    try {
      // The dirty-select mapping makes MyBatis own commit/rollback even when this
      // transaction has no CIB task/entity mutation. RETURNING executes immediately
      // under BATCH too, preserving row counts and the final freshness boundary.
      return session.<Integer>selectList(EnlistedWrites.ID, new EnlistedWrites.Write(sql, args.clone())).size();
    } catch (PersistenceException ex) {
      throw unavailable();
    }
  }

  long lockTenant() {
    var rows = rows("SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_=? FOR UPDATE", tenant);
    if (rows.size() != 1) throw Rejected.denied();
    return ((Number) rows.get(0).get("rev_")).longValue();
  }

  boolean revoked(String fingerprint) {
    return !rows(
            "SELECT REV_ FROM MZO_HUMAN_REVOKED_KEY WHERE TENANT_=? AND FINGERPRINT_=?",
            tenant,
            fingerprint)
        .isEmpty();
  }

  Map<String, Object> principal(String ref, String issuer, String subject, long now) {
    var rs =
        rows("SELECT * FROM MZO_HUMAN_PRINCIPAL WHERE TENANT_=? AND PRINCIPAL_=?", tenant, ref);
    if (rs.size() != 1) throw Rejected.denied();
    var p = rs.get(0);
    requireCurrentPrincipal(p, now);
    if (!issuer.equals(p.get("issuer_"))
        || !subject.equals(p.get("subject_"))) throw Rejected.denied();
    return p;
  }

  static void requireCurrentPrincipal(Map<String, Object> principal, long now) {
    if (!Boolean.TRUE.equals(principal.get("active_"))
        || ((Number) principal.get("valid_until_")).longValue() <= now)
      throw Rejected.denied();
  }

  byte[] receipt(String task, String command, String digest, String principal, String workload) {
    var rs =
        rows(
            "SELECT * FROM MZO_HUMAN_RECEIPT WHERE TENANT_=? AND TASK_=? AND COMMAND_=?",
            tenant,
            task,
            command);
    if (rs.isEmpty()) return null;
    var r = rs.get(0);
    if (!digest.equals(r.get("digest_"))
        || !principal.equals(r.get("principal_"))
        || !workload.equals(r.get("workload_"))) throw Rejected.conflict();
    return ((String) r.get("receipt_")).getBytes(StandardCharsets.UTF_8);
  }

  Map<String, Object> decisionBinding(HumanCommand c, List<?> groups, long now) {
    var d = c.classified();
    // AtomicHumanCommand holds the enlisted tenant FOR UPDATE lock through commit.
    // Qualified installers append immutable binding rows and revoke by revision CAS
    // under that same lock (human-schema-postgres.sql). Runtime is SELECT-only.
    var rs = rows("SELECT * FROM MZO_HUMAN_DECISION_BINDING WHERE TENANT_=? AND ENVIRONMENT_=? AND PROCESS_=? AND TASK_KEY_=? AND AUTHORITY_REV_=?",
        tenant, d.environment(), c.processId(), c.taskKey(), Long.parseLong(c.authorityRevision()));
    if (rs.size() != 1) throw new Rejected(409, "FORM_NOT_ACTIVATED");
    var b = rs.get(0);
    ClassifiedDecision.requireBinding(c, b, groups, now);
    return b;
  }

  void insertReceipt(HumanCommand c, String digest, byte[] receipt) {
    update(
        "INSERT INTO"
            + " MZO_HUMAN_RECEIPT(TENANT_,TASK_,COMMAND_,DIGEST_,PRINCIPAL_,WORKLOAD_,RECEIPT_)"
            + " VALUES(?,?,?,?,?,?,?)",
        tenant,
        c.taskId(),
        c.commandId(),
        digest,
        c.principalRef(),
        c.workloadRef(),
        new String(receipt, StandardCharsets.UTF_8));
  }
}

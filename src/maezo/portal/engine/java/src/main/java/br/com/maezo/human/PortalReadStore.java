package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;

import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import org.apache.ibatis.mapping.*;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/**
 * Prepared bounded PostgreSQL reads on the actual CIB enlisted connection, bypassing entity
 * caches.
 */
final class PortalReadStore {
  final Connection connection;
  final SqlSession session;
  final PortalReadTrust trust;
  final int timeout;
  static final String SCOPE = "TENANT_=? AND ENVIRONMENT_=? AND ENGINE_=? AND INCARNATION_=?";
  PortalReadStore(CommandContext context, PortalReadTrust trust, int timeout) {
    this.session = context.getDbSqlSession().getSqlSession();
    this.connection = session.getConnection();
    this.trust = trust;
    this.timeout = timeout;
    try {
      if (connection.getAutoCommit()
          || !connection.getMetaData().getDatabaseProductName().equals("PostgreSQL"))
        throw unavailable();
    } catch (SQLException ex) {
      throw unavailable();
    }
  }
  Object[] args(Object... tail) {
    Object[] all = new Object[tail.length + 4];
    all[0] = trust.scope.get("tenant");
    all[1] = trust.scope.get("environment");
    all[2] = trust.engine;
    all[3] = trust.incarnation;
    System.arraycopy(tail, 0, all, 4, tail.length);
    return all;
  }
  List<Map<String, Object>> rows(String sql, int max, Object... args) {
    try (PreparedStatement ps = connection.prepareStatement(sql)) {
      ps.setQueryTimeout(timeout);
      ps.setMaxRows(max + 1);
      for (int i = 0; i < args.length; i++) ps.setObject(i + 1, args[i]);
      try (ResultSet rs = ps.executeQuery()) {
        List<Map<String, Object>> result = new ArrayList<>();
        long bytes = 0;
        while (rs.next()) {
          if (result.size() >= max)
            throw unavailable();
          Map<String, Object> row = new HashMap<>();
          for (int i = 1; i <= rs.getMetaData().getColumnCount(); i++) {
            Object v = rs.getObject(i);
            if (v != null)
              bytes += v.toString().getBytes(StandardCharsets.UTF_8).length;
            if (bytes > MAX)
              throw unavailable();
            row.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT), v);
          }
          result.add(row);
        }
        return result;
      }
    } catch (SQLException ex) {
      throw unavailable();
    }
  }
  Map<String, Object> one(String sql, Object... args) {
    var rows = rows(sql, 1, args);
    return rows.isEmpty() ? null : rows.get(0);
  }
  long lockTenant() {
    var row = one(
        "SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_=? FOR UPDATE", trust.scope.get("tenant"));
    if (row == null)
      throw unavailable();
    return ((Number) row.get("rev_")).longValue();
  }
  boolean revoked(String fingerprint) {
    return one("SELECT FINGERPRINT_ FROM MZO_PORTAL_READ_REVOCATION WHERE " + SCOPE
                   + " AND FINGERPRINT_=?",
               args(fingerprint))
        != null;
  }
  static Map<String, Object> json(Object value) {
    if (!(value instanceof String s))
      throw unavailable();
    return map(Jcs.parse(s.getBytes(StandardCharsets.UTF_8)));
  }
  Map<String, Object> catalog(String ref) {
    var r = one(
        "SELECT d.*,c.ARTIFACT_,c.DIGEST_ AS SIGNED_DIGEST_,c.SOURCE_ AS SIGNED_SOURCE_,c.PUBLISHER_ AS SIGNED_PUBLISHER_,c.PUBLICATION_ AS SIGNED_PUBLICATION_,c.VALID_UNTIL_ AS SIGNED_VALID_UNTIL_ FROM MZO_PORTAL_READ_DESIGNATION d JOIN MZO_PORTAL_READ_CATALOG c "
            + "USING(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,CATALOG_,REVISION_) WHERE "
            + "d.TENANT_=? "
            + "AND d.ENVIRONMENT_=? AND d.ENGINE_=? AND d.INCARNATION_=? AND d.CATALOG_=?",
        args(ref));
    if (r == null || Boolean.TRUE.equals(r.get("revoked_")))
      throw unavailable();
    return r;
  }
  void proof(String publication, String kind, Map<String, Object> source) {
    var receipt =
        one("SELECT KEY_FINGERPRINT_,RECEIPT_ FROM MZO_PORTAL_READ_PUBLICATION_RECEIPT WHERE "
                + SCOPE + " AND PUBLICATION_=?",
            args(publication));
    if (receipt == null || revoked((String) receipt.get("key_fingerprint_")))
      throw unavailable();
    var key = trust.keys.values()
                  .stream()
                  .filter(k -> k.fingerprint().equals(receipt.get("key_fingerprint_")))
                  .findFirst()
                  .orElseThrow(PortalReadModels::unavailable);
    if (!key.purpose().equals("portal-read-publication") || !key.kinds().contains(kind)
        || !key.workload().equals(source.get("publisher_ref"))
        || !kind.equals(json(receipt.get("receipt_")).get("kind")))
      throw unavailable();
  }
  Map<String, Object> membership(String principal) {
    return one("SELECT * FROM MZO_PORTAL_READ_MEMBERSHIP WHERE " + SCOPE + " AND PRINCIPAL_=?",
        args(principal));
  }
  Map<String, Object> human(String principal) {
    return one("SELECT * FROM MZO_HUMAN_PRINCIPAL WHERE TENANT_=? AND PRINCIPAL_=?",
        trust.scope.get("tenant"), principal);
  }
  /** One PostgreSQL statement snapshot includes ALL task/link/projection/native evidence facts. */
  Map<String, Object> tuple(String task, String catalog, String principal) {
    String sql = """
      SELECT t.ID_,t.REV_,t.PROC_DEF_ID_,t.TASK_DEF_KEY_,t.TENANT_ID_,t.ASSIGNEE_,t.DUE_DATE_,t.SUSPENSION_STATE_,
       h.REV_ AS AUTHORITY_REVISION_, r.PAYLOAD_ AS RESOURCE_,r.SOURCE_ AS RESOURCE_SOURCE_,r.PUBLICATION_ AS RESOURCE_PUBLICATION_,
       e.REF_ AS EVIDENCE_REF_,e.REV_ AS EVIDENCE_REVISION_,e.DIGEST_ AS EVIDENCE_DIGEST_,e.VALID_UNTIL_ AS EVIDENCE_UNTIL_,e.PROCESS_ AS EVIDENCE_DEFINITION_,
       d.REVISION_ AS CATALOG_REVISION_,d.DIGEST_ AS CATALOG_DIGEST_,d.SOURCE_ AS CATALOG_SOURCE_,d.PUBLICATION_ AS CATALOG_PUBLICATION_,d.PUBLISHER_ AS CATALOG_PUBLISHER_,d.VALID_UNTIL_ AS CATALOG_UNTIL_,d.REVOKED_ AS CATALOG_REVOKED_,c.ARTIFACT_ AS CATALOG_ARTIFACT_,c.DIGEST_ AS CATALOG_SIGNED_DIGEST_,c.SOURCE_ AS CATALOG_SIGNED_SOURCE_,c.PUBLISHER_ AS CATALOG_SIGNED_PUBLISHER_,c.PUBLICATION_ AS CATALOG_SIGNED_PUBLICATION_,c.VALID_UNTIL_ AS CATALOG_SIGNED_UNTIL_,
       m.PAYLOAD_ AS MEMBERSHIP_,m.SOURCE_ AS MEMBERSHIP_SOURCE_,m.PUBLICATION_ AS MEMBERSHIP_PUBLICATION_,
       (SELECT count(*) FROM MZO_PORTAL_READ_PUBLICATION_RECEIPT pr WHERE pr.TENANT_=r.TENANT_ AND pr.ENVIRONMENT_=r.ENVIRONMENT_ AND pr.ENGINE_=r.ENGINE_ AND pr.INCARNATION_=r.INCARNATION_ AND pr.PUBLICATION_=r.PUBLICATION_ AND NOT EXISTS (SELECT 1 FROM MZO_PORTAL_READ_REVOCATION rv WHERE rv.TENANT_=pr.TENANT_ AND rv.ENVIRONMENT_=pr.ENVIRONMENT_ AND rv.ENGINE_=pr.ENGINE_ AND rv.INCARNATION_=pr.INCARNATION_ AND rv.FINGERPRINT_=pr.KEY_FINGERPRINT_)) AS RESOURCE_PROOF_,
       (SELECT COALESCE(jsonb_agg(jsonb_build_object('link_id',l.ID_,'link_revision',l.REV_::text,'task_id',l.TASK_ID_,'tenant_id',l.TENANT_ID_,'type',l.TYPE_,'group_id',l.GROUP_ID_,'user_id',l.USER_ID_) ORDER BY l.ID_ COLLATE "C"),'[]'::jsonb)::text FROM ACT_RU_IDENTITYLINK l WHERE l.TASK_ID_=t.ID_ AND l.TYPE_='candidate') AS LINKS_
      FROM ACT_RU_TASK t JOIN MZO_HUMAN_TENANT h ON h.TENANT_=t.TENANT_ID_
      LEFT JOIN MZO_PORTAL_READ_RESOURCE r ON r.TENANT_=t.TENANT_ID_ AND r.ENVIRONMENT_=? AND r.ENGINE_=? AND r.INCARNATION_=? AND r.TASK_=t.ID_
      LEFT JOIN MZO_PORTAL_READ_PUBLICATION_RECEIPT pr ON pr.TENANT_=r.TENANT_ AND pr.ENVIRONMENT_=r.ENVIRONMENT_ AND pr.ENGINE_=r.ENGINE_ AND pr.INCARNATION_=r.INCARNATION_ AND pr.PUBLICATION_=r.PUBLICATION_
     LEFT JOIN MZO_PORTAL_READ_REVOCATION rv ON rv.TENANT_=pr.TENANT_ AND rv.ENVIRONMENT_=pr.ENVIRONMENT_ AND rv.ENGINE_=pr.ENGINE_ AND rv.INCARNATION_=pr.INCARNATION_ AND rv.FINGERPRINT_=pr.KEY_FINGERPRINT_
     LEFT JOIN MZO_HUMAN_EVIDENCE e ON e.TENANT_=t.TENANT_ID_ AND e.TASK_=t.ID_
      LEFT JOIN MZO_PORTAL_READ_DESIGNATION d ON d.TENANT_=t.TENANT_ID_ AND d.ENVIRONMENT_=r.ENVIRONMENT_ AND d.ENGINE_=r.ENGINE_ AND d.INCARNATION_=r.INCARNATION_ AND d.CATALOG_=?
      LEFT JOIN MZO_PORTAL_READ_CATALOG c ON c.TENANT_=d.TENANT_ AND c.ENVIRONMENT_=d.ENVIRONMENT_ AND c.ENGINE_=d.ENGINE_ AND c.INCARNATION_=d.INCARNATION_ AND c.CATALOG_=d.CATALOG_ AND c.REVISION_=d.REVISION_
      LEFT JOIN MZO_PORTAL_READ_MEMBERSHIP m ON m.TENANT_=t.TENANT_ID_ AND m.ENVIRONMENT_=d.ENVIRONMENT_ AND m.ENGINE_=d.ENGINE_ AND m.INCARNATION_=d.INCARNATION_ AND m.PRINCIPAL_=?
      WHERE t.TENANT_ID_=? AND t.ID_=?
      """;
    return one(sql, trust.scope.get("environment"), trust.engine, trust.incarnation, catalog,
        principal, trust.scope.get("tenant"), task);
  }
  // JSONB is used only on new closed projections. Every value is a bound parameter.
  // Per-item fail-closed: a candidate whose (definition, task key) the admitted catalog does not
  // carry is OMITTED from the queue (never disclosed), not a fault of the whole queue. Only an
  // ADMITTED candidate whose publication is stale/absent faults the read (PREFLIGHT).
  static final String DISCOVERY_BASE = """
    WITH p AS (SELECT ?::jsonb AS principal,?::jsonb AS catalog,?::timestamptz AS now), candidates AS (
     SELECT t.*, entry.value AS entry,r.PAYLOAD_::jsonb AS resource,r.SOURCE_::jsonb AS source,pr.PUBLICATION_ AS proof,rv.FINGERPRINT_ AS revoked_publisher,e.REV_ AS evidence_revision,e.DIGEST_ AS evidence_digest
     FROM ACT_RU_TASK t CROSS JOIN p
     LEFT JOIN LATERAL (SELECT value FROM jsonb_array_elements(p.catalog->'entries') WHERE value->>'process_definition_id'=t.PROC_DEF_ID_ AND value->>'task_definition_key'=t.TASK_DEF_KEY_) entry ON true
     LEFT JOIN MZO_PORTAL_READ_RESOURCE r ON r.TENANT_=t.TENANT_ID_ AND r.ENVIRONMENT_=? AND r.ENGINE_=? AND r.INCARNATION_=? AND r.TASK_=t.ID_
     LEFT JOIN MZO_PORTAL_READ_PUBLICATION_RECEIPT pr ON pr.TENANT_=r.TENANT_ AND pr.ENVIRONMENT_=r.ENVIRONMENT_ AND pr.ENGINE_=r.ENGINE_ AND pr.INCARNATION_=r.INCARNATION_ AND pr.PUBLICATION_=r.PUBLICATION_
     LEFT JOIN MZO_PORTAL_READ_REVOCATION rv ON rv.TENANT_=pr.TENANT_ AND rv.ENVIRONMENT_=pr.ENVIRONMENT_ AND rv.ENGINE_=pr.ENGINE_ AND rv.INCARNATION_=pr.INCARNATION_ AND rv.FINGERPRINT_=pr.KEY_FINGERPRINT_
     LEFT JOIN MZO_HUMAN_EVIDENCE e ON e.TENANT_=t.TENANT_ID_ AND e.TASK_=t.ID_
     WHERE t.TENANT_ID_=? AND t.SUSPENSION_STATE_=1 AND
      ((?='mine' AND t.ASSIGNEE_=p.principal->>'principal_ref') OR EXISTS
       (SELECT 1 FROM ACT_RU_IDENTITYLINK l,jsonb_array_elements(p.principal->'memberships') mb WHERE l.TASK_ID_=t.ID_ AND l.TYPE_='candidate' AND l.TENANT_ID_=t.TENANT_ID_ AND jsonb_exists(mb->'groups',l.GROUP_ID_)) OR EXISTS (SELECT 1 FROM ACT_RU_IDENTITYLINK bad WHERE bad.TASK_ID_=t.ID_ AND bad.TYPE_='candidate' AND (bad.GROUP_ID_ LIKE '${%' OR bad.GROUP_ID_ LIKE '#{%')))
    )
    """;
  static final String PREFLIGHT = """
    SELECT CASE WHEN entry IS NOT NULL AND resource IS NOT NULL AND source IS NOT NULL AND resource->>'state'='complete'
       AND (resource->>'valid_until')::timestamptz>p.now AND (source->>'valid_until')::timestamptz>p.now
       AND (resource->'classification'->>'valid_until')::timestamptz>p.now
       AND proof IS NOT NULL AND revoked_publisher IS NULL AND source->>'publisher_ref'=?
       AND evidence_revision IS NOT NULL AND evidence_digest IS NOT NULL
       AND resource->'resource_policy'=entry->'resource_policy'
       AND resource->'classification'->>'policy_ref'=entry->'disclosure_policy'->>'artifact_ref'
       AND resource->'classification'->>'policy_digest'=entry->'disclosure_policy'->>'digest'
       AND resource->>'process_definition_digest'=entry->>'process_definition_digest'
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(resource->'positive_grants') expired WHERE expired->>'issuer'=p.principal->>'issuer' AND expired->>'subject'=p.principal->>'subject' AND expired->>'principal_ref'=p.principal->>'principal_ref' AND expired->>'membership_revision'=p.principal->>'membership_revision' AND (expired->>'valid_until')::timestamptz<=p.now)
       THEN 'READ_REVISION_CONFLICT' ELSE 'READ_DEPENDENCY_UNAVAILABLE' END AS fault
    FROM candidates CROSS JOIN p WHERE entry IS NOT NULL AND (resource IS NULL OR source IS NULL OR resource->>'state'<>'complete'
      OR proof IS NULL OR revoked_publisher IS NOT NULL OR source->>'publisher_ref'<>?
      OR resource->'resource_policy' IS DISTINCT FROM entry->'resource_policy'
      OR resource->'classification'->>'policy_ref' IS DISTINCT FROM entry->'disclosure_policy'->>'artifact_ref'
      OR resource->'classification'->>'policy_digest' IS DISTINCT FROM entry->'disclosure_policy'->>'digest'
      OR resource->>'process_definition_digest' IS DISTINCT FROM entry->>'process_definition_digest'
      OR EXISTS (SELECT 1 FROM jsonb_array_elements(resource->'positive_grants') expired WHERE expired->>'issuer'=p.principal->>'issuer' AND expired->>'subject'=p.principal->>'subject' AND expired->>'principal_ref'=p.principal->>'principal_ref' AND expired->>'membership_revision'=p.principal->>'membership_revision' AND (expired->>'valid_until')::timestamptz<=p.now)
      OR (resource->>'valid_until')::timestamptz<=p.now OR (source->>'valid_until')::timestamptz<=p.now
      OR (resource->'classification'->>'valid_until')::timestamptz<=p.now
      OR resource->>'observed_task_revision'<>REV_::text OR resource->>'process_definition_id'<>PROC_DEF_ID_
      OR resource->>'evidence_revision' IS DISTINCT FROM evidence_revision::text OR resource->>'evidence_digest' IS DISTINCT FROM evidence_digest)
    LIMIT 1
    """;
  static final String ELIGIBLE = """
    SELECT c.ID_ FROM candidates c CROSS JOIN p WHERE c.entry IS NOT NULL AND (?='team' OR ASSIGNEE_=p.principal->>'principal_ref')
      AND (?::text IS NULL OR ID_ COLLATE "C">?::text COLLATE "C")
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(p.principal->'memberships') mb
         WHERE (mb->'roles') @> (entry->'required_roles') AND EXISTS
          (SELECT 1 FROM ACT_RU_IDENTITYLINK l WHERE l.TASK_ID_=c.ID_ AND l.TYPE_='candidate' AND l.TENANT_ID_=c.TENANT_ID_ AND jsonb_exists(mb->'groups',l.GROUP_ID_)))
      AND (p.principal->'subject_bindings') @> (resource->'required_subject_bindings')
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(resource->'positive_grants') g
         WHERE g->>'issuer'=p.principal->>'issuer' AND g->>'subject'=p.principal->>'subject' AND g->>'principal_ref'=p.principal->>'principal_ref'
          AND g->>'membership_revision'=p.principal->>'membership_revision' AND (g->'consent_scopes') @> (resource->'required_consent_scopes') AND (g->>'valid_until')::timestamptz>p.now)
      ORDER BY ID_ COLLATE "C" ASC LIMIT ?
    """;
  List<String> discover(Map<String, Object> principal, Map<String, Object> artifact, String queue,
      int limit, String after, Instant now) {
    Object[] base = {new String(Jcs.canonical(principal), StandardCharsets.UTF_8),
        new String(Jcs.canonical(artifact), StandardCharsets.UTF_8), Timestamp.from(now),
        trust.scope.get("environment"), trust.engine, trust.incarnation, trust.scope.get("tenant"),
        queue};
    String publisher = trust.publisher("resource", str(artifact, "catalog_ref"));
    Object[] preflight = Arrays.copyOf(base, base.length + 2);
    preflight[8] = publisher;
    preflight[9] = publisher;
    var fault = one(DISCOVERY_BASE + PREFLIGHT, preflight);
    if (fault != null) {
      if (fault.get("fault").equals("READ_REVISION_CONFLICT"))
        throw conflict();
      throw unavailable();
    }
    Object[] all = Arrays.copyOf(base, base.length + 4);
    all[8] = queue;
    all[9] = after;
    all[10] = after;
    all[11] = limit + 1;
    var rows = rows(DISCOVERY_BASE + ELIGIBLE, limit + 1, all);
    return rows.stream().map(row -> (String) row.get("id_")).toList();
  }
  int write(String sql, Object... args) {
    try {
      return session.<Integer>selectList(Writes.ID, new Writes.Write(sql, args.clone())).size();
    } catch (org.apache.ibatis.exceptions.PersistenceException failure) {
      throw unavailable();
    }
  }
  static final class Writes implements SqlSource {
    static final String ID = "br.com.maezo.human.portalReadPublicationWrite.v1";
    record Write(String sql, Object[] args) {}
    final Configuration config;
    Writes(Configuration c) {
      config = c;
    }
    static void install(Configuration c) {
      synchronized (c) {
        if (c.hasStatement(ID, false)) {
          if (!(c.getMappedStatement(ID).getSqlSource() instanceof Writes))
            throw unavailable();
          return;
        }
        c.addMappedStatement(
            new MappedStatement.Builder(c, ID, new Writes(c), SqlCommandType.SELECT)
                .dirtySelect(true)
                .flushCacheRequired(true)
                .useCache(false)
                .resultMaps(List.of(
                    new ResultMap.Builder(c, ID + ".count", Integer.class, List.of()).build()))
                .build());
      }
    }
    public BoundSql getBoundSql(Object parameter) {
      Write w = (Write) parameter;
      List<ParameterMapping> mappings = new ArrayList<>();
      for (int i = 0; i < w.args.length; i++)
        mappings.add(new ParameterMapping.Builder(config, "p" + i, Object.class).build());
      BoundSql b = new BoundSql(config, w.sql + " RETURNING 1", mappings, parameter);
      for (int i = 0; i < w.args.length; i++) b.setAdditionalParameter("p" + i, w.args[i]);
      return b;
    }
  }
}

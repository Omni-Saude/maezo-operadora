package br.com.maezo.human;
import static br.com.maezo.human.PortalReadModels.*;

import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;
/** Dedicated CAS publisher. Source qualification is acquired BEFORE tenant locking. */
final class PortalReadPublication implements Command<PortalReadPublication.Result> {
  static final class Result {
    byte[] bytes;
    boolean committed;
    Runnable guard;
    byte[] bytes() {
      if (!committed || bytes == null)
        throw unavailable();
      guard.run();
      return bytes.clone();
    }
  }
  final PortalReadTrust trust;
  final PortalReadEnvelope.Verified envelope;
  final PortalReadTrust.Admission admission;
  final PortalReadTrust.PublicationQualification qualification;
  PortalReadPublication(PortalReadTrust t, PortalReadEnvelope.Verified e,
      PortalReadTrust.Admission a, PortalReadTrust.PublicationQualification q) {
    trust = t;
    envelope = e;
    admission = a;
    qualification = q;
  }
  void local() {
    Instant now = Instant.now();
    envelope.current(now);
    admission.requireCurrent();
    if (!now.isBefore(admission.validUntil()))
      throw unavailable();
  }
  static String text(Object value) {
    return new String(bounded(value), StandardCharsets.UTF_8);
  }
  public Result execute(CommandContext context) {
    local();
    var r = envelope.request();
    String kind = str(r, "kind"), id = str(r, "publication_id");
    var key = envelope.key();
    var source = obj(r, "source");
    var payload = copy(obj(r, "payload"));
    if (!key.kinds().contains(kind) || !key.workload().equals(source.get("publisher_ref")))
      throw denied();
    if ((kind.equals("catalog-designate") || kind.equals("catalog-revoke"))
        && !key.catalog().equals(payload.get("catalog_ref")))
      throw denied();
    var db = new PortalReadStore(context, trust, admission.statementTimeoutSeconds());
    long revision = db.lockTenant();
    if (db.revoked(key.fingerprint()))
      throw denied();
    var old = db.one(
        "SELECT DIGEST_,KEY_FINGERPRINT_,RECEIPT_ FROM MZO_PORTAL_READ_PUBLICATION_RECEIPT WHERE "
            + PortalReadStore.SCOPE + " AND PUBLICATION_=?",
        db.args(id));
    Result out = new Result();
    out.guard = this::local;
    if (old != null) {
      if (!envelope.digest().equals(old.get("digest_"))
          || !key.fingerprint().equals(old.get("key_fingerprint_")))
        throw conflict();
      out.bytes = ((String) old.get("receipt_")).getBytes(StandardCharsets.UTF_8);
      context.getTransactionContext().addTransactionListener(
          TransactionState.COMMITTING, ignored -> {
            local();
            if (db.revoked(key.fingerprint()))
              throw denied();
          });
      context.getTransactionContext().addTransactionListener(
          TransactionState.COMMITTED, ignored -> out.committed = true);
      return out;
    }
    if (revision != number(r.get("expected_authority_revision")) || revision == Long.MAX_VALUE)
      throw conflict();
    if (qualification == null)
      throw unavailable();
    qualification.requireCurrent();
    qualification.verify(kind, source, payload);
    admission.verifySource(kind, source);
    current(Instant.now(), source.get("valid_until"));
    if (time(source.get("observed_at")).isAfter(Instant.now())
        || !Instant.now().isBefore(qualification.validUntil()))
      throw unavailable();
    List<Instant> retained = new ArrayList<>();
    retained.add(time(source.get("valid_until")));
    retained.add(qualification.validUntil());
    if (kind.equals("catalog-designate"))
      retained.add(time(payload.get("valid_until")));
    if (kind.equals("membership") && payload.get("state").equals("active"))
      retained.add(time(payload.get("reviewed_until")));
    if (kind.equals("resource") && payload.get("state").equals("complete")) {
      retained.add(time(payload.get("valid_until")));
      retained.add(time(obj(payload, "classification").get("valid_until")));
      for (Object grant : list(payload.get("positive_grants")))
        retained.add(time(map(grant).get("valid_until")));
    }
    Runnable positiveGuard = () -> {
      local();
      qualification.requireCurrent();
      Instant now = Instant.now();
      for (Instant end : retained)
        if (!now.isBefore(end))
          throw unavailable();
    };
    positiveGuard.run();
    out.guard = positiveGuard;
    long next = revision + 1;
    Runnable persist;
    switch (kind) {
      case "catalog-designate" -> {
        byte[] raw = b64(payload.get("catalog_artifact_base64"), false, -1);
        var artifact = validate("artifactcatalog", Jcs.parse(raw));
        if (!Arrays.equals(raw, Jcs.canonical(artifact))
            || !Jcs.digest(raw).equals(payload.get("catalog_digest"))
            || !artifact.get("catalog_ref").equals(payload.get("catalog_ref"))
            || !artifact.get("publisher_ref").equals(source.get("publisher_ref"))
            || !artifact.get("deployment_receipt_ref").equals(payload.get("deployment_receipt_ref"))
            || !artifact.get("deployment_receipt_digest")
                .equals(payload.get("deployment_receipt_digest")))
          throw unavailable();
        verifyCatalog(context, artifact);
        var prior = db.one("SELECT REVISION_,REVOKED_ FROM MZO_PORTAL_READ_DESIGNATION WHERE "
                + PortalReadStore.SCOPE + " AND CATALOG_=?",
            db.args(payload.get("catalog_ref")));
        if (prior != null
            && (Boolean.TRUE.equals(prior.get("revoked_"))
                || number(payload.get("catalog_revision"))
                    <= ((Number) prior.get("revision_")).longValue()))
          throw conflict();
        current(Instant.now(), payload.get("valid_until"));
        if (time(payload.get("valid_until")).isAfter(time(source.get("valid_until"))))
          throw unavailable();
        persist = () -> {
          db.write("INSERT INTO "
                  + "MZO_PORTAL_READ_CATALOG(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,CATALOG_,"
                  + "REVISION_,DIGEST_,ARTIFACT_,PUBLICATION_,SOURCE_,PUBLISHER_,VALID_UNTIL_) "
                  + "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              db.args(payload.get("catalog_ref"), number(payload.get("catalog_revision")),
                  payload.get("catalog_digest"), text(artifact), id, text(source),
                  source.get("publisher_ref"), Timestamp.from(time(payload.get("valid_until")))));
          db.write("INSERT INTO "
                  + "MZO_PORTAL_READ_DESIGNATION(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,"
                  + "CATALOG_,REVISION_,DIGEST_,PUBLICATION_,SOURCE_,PUBLISHER_,VALID_UNTIL_,"
                  + "REVOKED_) VALUES(?,?,?,?,?,?,?,?,?,?,?,false) ON "
                  + "CONFLICT(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,CATALOG_) DO UPDATE SET "
                  + "REVISION_=EXCLUDED.REVISION_,DIGEST_=EXCLUDED.DIGEST_,PUBLICATION_=EXCLUDED."
                  + "PUBLICATION_,SOURCE_=EXCLUDED.SOURCE_,PUBLISHER_=EXCLUDED.PUBLISHER_,VALID_"
                  + "UNTIL_=EXCLUDED.VALID_UNTIL_",
              db.args(payload.get("catalog_ref"), number(payload.get("catalog_revision")),
                  payload.get("catalog_digest"), id, text(source), source.get("publisher_ref"),
                  Timestamp.from(time(payload.get("valid_until")))));
        };
      }
      case "catalog-revoke" -> {
        var prior = db.one("SELECT REVISION_ FROM MZO_PORTAL_READ_DESIGNATION WHERE "
                + PortalReadStore.SCOPE + " AND CATALOG_=?",
            db.args(payload.get("catalog_ref")));
        if (prior == null
            || number(payload.get("expected_catalog_revision"))
                != ((Number) prior.get("revision_")).longValue())
          throw conflict();
        persist = ()
            -> db.write("UPDATE MZO_PORTAL_READ_DESIGNATION SET REVOKED_=true,PUBLICATION_=? WHERE "
                    + PortalReadStore.SCOPE + " AND CATALOG_=?",
                prepend(id, db.args(payload.get("catalog_ref"))));
      }
      case "membership" -> {
        var prior = db.membership(str(payload, "principal_ref"));
        if (prior != null
            && (!prior.get("issuer_").equals(payload.get("issuer"))
                || !prior.get("subject_").equals(payload.get("subject"))
                || number(payload.get("membership_revision"))
                    <= ((Number) prior.get("revision_")).longValue()))
          throw conflict();
        if (payload.get("state").equals("active")) {
          current(Instant.now(), payload.get("reviewed_until"));
          var human = db.human(str(payload, "principal_ref"));
          if (human == null || !human.get("issuer_").equals(payload.get("issuer"))
              || !human.get("subject_").equals(payload.get("subject")))
            throw unavailable();
        }
        persist = ()
            -> db.write("INSERT INTO "
                    + "MZO_PORTAL_READ_MEMBERSHIP(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,"
                      + "PRINCIPAL_,"
                    + "ISSUER_,SUBJECT_,REVISION_,PAYLOAD_,PUBLICATION_,SOURCE_) "
                    + "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON "
                    + "CONFLICT(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,PRINCIPAL_) DO UPDATE "
                      + "SET "
                    + "REVISION_=EXCLUDED.REVISION_,PAYLOAD_=EXCLUDED.PAYLOAD_,PUBLICATION_="
                      + "EXCLUDED."
                    + "PUBLICATION_,SOURCE_=EXCLUDED.SOURCE_",
                db.args(payload.get("principal_ref"), payload.get("issuer"), payload.get("subject"),
                    number(payload.get("membership_revision")), text(payload), id, text(source)));
      }
      case "resource" -> {
        String taskId = str(payload, "task_id");
        var task = context.getTaskManager().findTaskById(taskId);
        if (task == null || !trust.scope.get("tenant").equals(task.getTenantId()))
          throw absent();
        if (task.getRevision() != number(payload.get("observed_task_revision"))
            || !task.getProcessDefinitionId().equals(payload.get("process_definition_id")))
          throw conflict();
        var evidence = db.one("SELECT * FROM MZO_HUMAN_EVIDENCE WHERE TENANT_=? AND TASK_=?",
            trust.scope.get("tenant"), taskId);
        if (evidence == null)
          throw unavailable();
        retained.add(Instant.ofEpochSecond(((Number) evidence.get("valid_until_")).longValue()));
        positiveGuard.run();
        if (!evidence.get("ref_").equals(payload.get("evidence_ref"))
            || !evidence.get("rev_").toString().equals(payload.get("evidence_revision"))
            || !evidence.get("digest_").equals(payload.get("evidence_digest"))
            || !evidence.get("process_").equals(payload.get("process_definition_id")))
          throw conflict();
        var catalog = db.catalog(key.catalog());
        var artifact = validate("artifactcatalog", PortalReadStore.json(catalog.get("artifact_")));
        verifyCatalog(context, artifact);
        var entries =
            list(artifact.get("entries"))
                .stream()
                .map(PortalReadModels::map)
                .filter(e
                    -> e.get("process_definition_id").equals(payload.get("process_definition_id"))
                        && e.get("task_definition_key").equals(task.getTaskDefinitionKey()))
                .toList();
        if (entries.size() != 1
            || !entries.get(0).get("resource_policy").equals(payload.get("resource_policy"))
            || !entries.get(0)
                .get("process_definition_digest")
                .equals(payload.get("process_definition_digest")))
          throw unavailable();
        admission.verifyClassification(obj(payload, "classification"), entries.get(0));
        var previous = db.one("SELECT SOURCE_ FROM MZO_PORTAL_READ_RESOURCE WHERE "
                + PortalReadStore.SCOPE + " AND TASK_=?",
            db.args(taskId));
        if (previous != null
            && number(source.get("source_revision"))
                <= number(PortalReadStore.json(previous.get("source_")).get("source_revision")))
          throw conflict();
        context.getDbEntityManager().forceUpdate(task);
        persist = () -> {
          var actual =
              db.one("SELECT REV_,PROC_DEF_ID_ FROM ACT_RU_TASK WHERE TENANT_ID_=? AND ID_=?",
                  trust.scope.get("tenant"), taskId);
          if (actual == null
              || !actual.get("proc_def_id_").equals(payload.get("process_definition_id")))
            throw conflict();
          payload.put("observed_task_revision", actual.get("rev_").toString());
          db.write("INSERT INTO "
                  + "MZO_PORTAL_READ_RESOURCE(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,TASK_,"
                  + "PAYLOAD_,PUBLICATION_,SOURCE_) VALUES(?,?,?,?,?,?,?,?) ON "
                  + "CONFLICT(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,TASK_) DO UPDATE SET "
                  + "PAYLOAD_=EXCLUDED.PAYLOAD_,PUBLICATION_=EXCLUDED.PUBLICATION_,SOURCE_="
                  + "EXCLUDED.SOURCE_",
              db.args(taskId, text(payload), id, text(source)));
        };
      }
      case "revoke-key" ->
        persist = ()
            -> db.write("INSERT INTO "
                    + "MZO_PORTAL_READ_REVOCATION(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,"
                    + "FINGERPRINT_,REVISION_,PUBLICATION_) VALUES(?,?,?,?,?,?,?) ON CONFLICT "
                    + "DO NOTHING",
                db.args(payload.get("key_fingerprint"), next, id));
      default -> throw invalid();
    }
    if (db.write("UPDATE MZO_HUMAN_TENANT SET REV_=? WHERE TENANT_=? AND REV_=?", next,
            trust.scope.get("tenant"), revision)
        != 1)
      throw conflict();
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING, ignored -> {
      local();
      qualification.requireCurrent();
      current(Instant.now(), source.get("valid_until"));
      if (!Instant.now().isBefore(qualification.validUntil()) || db.revoked(key.fingerprint()))
        throw unavailable();
      positiveGuard.run();
      persist.run();
      var receipt = record("schema", "portal-read-publication-receipt.v1", "publication_id", id,
          "request_digest", envelope.digest(), "authority_revision", Long.toString(next), "kind",
          kind, "record_digest", hash(payload));
      out.bytes = bounded(receipt);
      db.write("INSERT INTO "
              + "MZO_PORTAL_READ_PUBLICATION_RECEIPT(TENANT_,ENVIRONMENT_,ENGINE_,INCARNATION_,"
              + "PUBLICATION_,DIGEST_,KEY_FINGERPRINT_,RECEIPT_) VALUES(?,?,?,?,?,?,?,?)",
          db.args(id, envelope.digest(), key.fingerprint(), text(receipt)));
      local();
      positiveGuard.run();
    });
    context.getTransactionContext().addTransactionListener(
        TransactionState.COMMITTED, ignored -> out.committed = true);
    return out;
  }
  void verifyCatalog(CommandContext context, Map<String, Object> artifact) {
    admission.verifyCatalog(artifact);
    var verifier = new PortalReadCommand(trust, envelope, admission, null);
    verifier.context = context;
    verifier.verifyCatalog(artifact);
  }
  static Object[] prepend(Object first, Object[] rest) {
    Object[] out = new Object[rest.length + 1];
    out[0] = first;
    System.arraycopy(rest, 0, out, 1, rest.length);
    return out;
  }
}

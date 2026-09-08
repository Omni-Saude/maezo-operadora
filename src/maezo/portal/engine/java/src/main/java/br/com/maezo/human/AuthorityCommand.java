package br.com.maezo.human;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/**
 * ADR0049 D4/D5: separately signed, monotonic publication/revocation in engine TX. This is an
 * authoritative projection protocol, not an IdP/FHIR replication claim.
 */
final class AuthorityCommand implements Command<byte[]> {
  private final Trust trust;
  private final byte[] raw;
  private final String peer;

  AuthorityCommand(Trust trust, byte[] raw, String peer) {
    this.trust = trust;
    this.raw = raw.clone();
    this.peer = peer;
  }

  @Override
  public byte[] execute(CommandContext context) {
    EngineStore db = new EngineStore(context, trust.tenant);
    long revision = db.lockTenant();
    long now = java.time.Instant.now().getEpochSecond();
    var v = Envelope.verify(raw, trust, "human-authority", peer, now, db::revoked);
    var c = v.command();
    String op = Jcs.string(c, "operation");
    if (!"human-authority.v1".equals(c.get("schema"))
        || !Long.toString(revision).equals(Jcs.decimal(c, "expected_revision")))
      throw Rejected.conflict();
    long next;
    try {
      next = Math.addExact(revision, 1);
    } catch (ArithmeticException ex) {
      throw Rejected.conflict();
    }
    switch (op) {
      case "principal":
        {
          Jcs.keys(
              c,
              "schema",
              "tenant",
              "workload_ref",
              "operation",
              "expected_revision",
              "principal_ref",
              "issuer",
              "subject",
              "active",
              "valid_until",
              "groups");
          String ref = Jcs.ref(c, "principal_ref"),
              issuer = Jcs.string(c, "issuer"),
              subject = Jcs.ref(c, "subject");
          if (!(c.get("active") instanceof Boolean) || !(c.get("groups") instanceof List<?> groups))
            throw Rejected.invalid();
          Set<String> distinct = new HashSet<>();
          for (Object group : groups) {
            if (!(group instanceof String s)
                || !s.matches("[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}")
                || !distinct.add(s)) throw Rejected.invalid();
          }
          long until = Jcs.seconds(c, "valid_until");
          if (until <= now) throw Rejected.invalid();
          var old =
              db.rows(
                  "SELECT ISSUER_,SUBJECT_ FROM MZO_HUMAN_PRINCIPAL WHERE TENANT_=? AND"
                      + " PRINCIPAL_=?",
                  trust.tenant,
                  ref);
          if (!old.isEmpty()
              && (!issuer.equals(old.get(0).get("issuer_"))
                  || !subject.equals(old.get(0).get("subject_")))) throw Rejected.conflict();
          db.update(
              "INSERT INTO"
                  + " MZO_HUMAN_PRINCIPAL(TENANT_,PRINCIPAL_,ISSUER_,SUBJECT_,REV_,ACTIVE_,VALID_UNTIL_,GROUPS_)"
                  + " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(TENANT_,PRINCIPAL_) DO UPDATE SET"
                  + " REV_=EXCLUDED.REV_,ACTIVE_=EXCLUDED.ACTIVE_,VALID_UNTIL_=EXCLUDED.VALID_UNTIL_,GROUPS_=EXCLUDED.GROUPS_",
              trust.tenant,
              ref,
              issuer,
              subject,
              next,
              c.get("active"),
              until,
              new String(Jcs.canonical(groups), StandardCharsets.UTF_8));
          break;
        }
      case "evidence":
        {
          Jcs.keys(
              c,
              "schema",
              "tenant",
              "workload_ref",
              "operation",
              "expected_revision",
              "task_id",
              "process_definition_id",
              "evidence_ref",
              "evidence_digest",
              "valid_until");
          String taskId = Jcs.ref(c, "task_id"), processId = Jcs.ref(c, "process_definition_id");
          var task = context.getTaskManager().findTaskById(taskId);
          if (task == null
              || !trust.tenant.equals(task.getTenantId())
              || !processId.equals(task.getProcessDefinitionId())) throw Rejected.conflict();
          context.getDbEntityManager().forceUpdate(task); // Also races with timers/other clients.
          long until = Jcs.seconds(c, "valid_until");
          if (until <= now) throw Rejected.invalid();
          db.update(
              "INSERT INTO"
                  + " MZO_HUMAN_EVIDENCE(TENANT_,TASK_,REV_,REF_,DIGEST_,VALID_UNTIL_,PROCESS_)"
                  + " VALUES(?,?,?,?,?,?,?) ON CONFLICT(TENANT_,TASK_) DO UPDATE SET"
                  + " REV_=EXCLUDED.REV_,REF_=EXCLUDED.REF_,DIGEST_=EXCLUDED.DIGEST_,VALID_UNTIL_=EXCLUDED.VALID_UNTIL_,PROCESS_=EXCLUDED.PROCESS_",
              trust.tenant,
              taskId,
              next,
              Jcs.ref(c, "evidence_ref"),
              Jcs.hash(c, "evidence_digest"),
              until,
              processId);
          break;
        }
      case "revoke-key":
        {
          Jcs.keys(
              c,
              "schema",
              "tenant",
              "workload_ref",
              "operation",
              "expected_revision",
              "key_fingerprint");
          String fp = Jcs.hash(c, "key_fingerprint");
          if (trust.keys.values().stream().noneMatch(k -> k.fingerprint().equals(fp)))
            throw Rejected.invalid();
          db.update(
              "INSERT INTO MZO_HUMAN_REVOKED_KEY(TENANT_,FINGERPRINT_,REV_) VALUES(?,?,?) ON"
                  + " CONFLICT(TENANT_,FINGERPRINT_) DO NOTHING",
              trust.tenant,
              fp,
              next);
          break;
        }
      default:
        throw Rejected.invalid();
    }
    if (db.update(
            "UPDATE MZO_HUMAN_TENANT SET REV_=? WHERE TENANT_=? AND REV_=?",
            next,
            trust.tenant,
            revision)
        != 1) throw Rejected.conflict();
    context
        .getTransactionContext()
        .addTransactionListener(
            TransactionState.COMMITTING,
            ignored -> {
              long current = java.time.Instant.now().getEpochSecond();
              v.requireCurrent(current);
              if (!op.equals("revoke-key") && Jcs.seconds(c, "valid_until") <= current)
                throw Rejected.invalid();
            });
    return Jcs.canonical(
        Map.of(
            "schema",
            "human-authority-receipt.v1",
            "tenant",
            trust.tenant,
            "revision",
            Long.toString(next),
            "digest",
            v.digest()));
  }
}

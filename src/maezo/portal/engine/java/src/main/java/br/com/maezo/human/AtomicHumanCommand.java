package br.com.maezo.human;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** ADR0049 D5. All state and receipt participate in the engine CommandContext TX. */
final class AtomicHumanCommand implements Command<AtomicHumanCommand.Result> {
  /** Populated only by a committed transaction; never exposes a pre-commit receipt. */
  static final class Result {
    private byte[] committed;

    byte[] bytes() {
      if (committed == null) throw EngineStore.unavailable();
      return committed.clone();
    }
  }

  static final String SYNTHETIC_FORM = "maezo.synthetic-ack.v1";
  static final String FORM_DIGEST = Jcs.digest(SYNTHETIC_FORM.getBytes(StandardCharsets.UTF_8));
  private final Trust trust;
  private final byte[] raw;
  private final String peer;

  AtomicHumanCommand(Trust trust, byte[] raw, String peer) {
    this.trust = trust;
    this.raw = raw.clone();
    this.peer = peer;
  }

  @Override
  public Result execute(CommandContext context) {
    EngineStore db = new EngineStore(context, trust.tenant);
    long authorityRevision = db.lockTenant();
    long now = java.time.Instant.now().getEpochSecond();
    var verified = Envelope.verify(raw, trust, "human-command", peer, now, db::revoked);
    HumanCommand c = HumanCommand.parse(verified.command());
    var principal = db.principal(c.principalRef(), c.principalIssuer(), c.principalSubject(), now);
    // Fresh current principal is mandatory even for exact retry; original membership need
    // not remain unchanged merely to retrieve an already committed result after a renewal.
    byte[] previous =
        db.receipt(c.taskId(), c.commandId(), verified.digest(), c.principalRef(), c.workloadRef());
    Result result = new Result();
    if (previous != null) {
      context
          .getTransactionContext()
          .addTransactionListener(
              TransactionState.COMMITTING, ignored -> requireCurrent(verified, principal, null));
      context
          .getTransactionContext()
          .addTransactionListener(
              TransactionState.COMMITTED, ignored -> result.committed = previous);
      return result;
    }
    if (!Long.toString(authorityRevision).equals(c.authorityRevision())
        || !principal.get("rev_").toString().equals(c.membershipRevision()))
      throw Rejected.conflict();
    var task = context.getTaskManager().findTaskById(c.taskId());
    if (task == null
        || task.isSuspended()
        || !trust.tenant.equals(task.getTenantId())
        || !Integer.toString(task.getRevision()).equals(c.taskRevision())
        || !Objects.equals(task.getAssignee(), c.assigneeRef())
        || !task.getProcessDefinitionId().equals(c.processId())
        || !task.getTaskDefinitionKey().equals(c.taskKey())) throw Rejected.conflict();
    var process = task.getProcessDefinition();
    if (!process.getKey().equals(c.processKey())
        || !Integer.toString(process.getVersion()).equals(c.processVersion()))
      throw Rejected.conflict();
    try (var resource =
        context
            .getProcessEngineConfiguration()
            .getRepositoryService()
            .getProcessModel(c.processId())) {
      if (!Jcs.digest(resource.readAllBytes()).equals(c.processDigest())) throw Rejected.conflict();
    } catch (java.io.IOException ex) {
      throw EngineStore.unavailable();
    }
    var ev =
        db.rows(
            "SELECT * FROM MZO_HUMAN_EVIDENCE WHERE TENANT_=? AND TASK_=?",
            trust.tenant,
            c.taskId());
    if (ev.size() != 1) throw Rejected.conflict();
    var evidence = ev.get(0);
    if (!c.evidenceRevision().equals(evidence.get("rev_").toString())
        || !c.evidenceRef().equals(evidence.get("ref_"))
        || !c.evidenceDigest().equals(evidence.get("digest_"))
        || !c.processId().equals(evidence.get("process_"))
        || ((Number) evidence.get("valid_until_")).longValue() <= now) throw Rejected.conflict();
    Object groupValue =
        Jcs.parse(((String) principal.get("groups_")).getBytes(StandardCharsets.UTF_8));
    if (!(groupValue instanceof List<?> groups)
        || task.getCandidates().stream()
            .noneMatch(link -> link.getGroupId() != null && groups.contains(link.getGroupId())))
      throw Rejected.denied();
    // Real SP-OP bindings remain inactive until PHI projections/source status reconcile.
    // No generic variable projector, administrator bypass, or invented insurance rule.
    if (!trust.syntheticEnabled
        || !c.processKey().equals("MZO-HUMAN-SYNTHETIC")
        || !c.taskKey().equals("UT_Acknowledge")
        || !c.formKey().equals(SYNTHETIC_FORM)
        || !c.formVersion().equals("1")
        || !c.formDigest().equals(FORM_DIGEST)) throw new Rejected(409, "FORM_NOT_ACTIVATED");
    requireCurrent(verified, principal, evidence);
    if (c.operation().equals("claim")) {
      if (task.getAssignee() != null) throw Rejected.conflict();
      task.setAssignee(c.principalRef());
    } else {
      if (!c.principalRef().equals(task.getAssignee())) throw Rejected.conflict();
      if (c.operation().equals("release")) task.setAssignee(null);
      else {
        task.setVariable("synthetic_acknowledged", true);
        task.setVariable("synthetic_human_ref", c.principalRef());
        task.setVariable("synthetic_workload_ref", c.workloadRef());
        task.setVariable("synthetic_command_ref", c.commandId());
        task.complete();
      }
    }
    // Keep CIB's optimistic revision fence and its single normal deferred flush.
    // A manual flush replays retained metric/history inserts at CommandContext.close.
    if (!c.operation().equals("decision")) context.getDbEntityManager().forceUpdate(task);
    context
        .getTransactionContext()
        .addTransactionListener(
            TransactionState.COMMITTING,
            committing -> {
              byte[] bytes =
                  persistReceipt(db, c, verified.digest(), java.time.Instant.now().getEpochSecond());
              // Last local blocking SQL has returned. The tenant lock still fences
              // state changes, but only a fresh clock can fence elapsed validity.
              requireCurrent(verified, principal, evidence);
              committing
                  .getTransactionContext()
                  .addTransactionListener(
                      TransactionState.COMMITTED, ignored -> result.committed = bytes);
            });
    return result;
  }

  private static void requireCurrent(
      Envelope.Verified verified, Map<String, Object> principal, Map<String, Object> evidence) {
    long now = java.time.Instant.now().getEpochSecond();
    verified.requireCurrent(now);
    EngineStore.requireCurrentPrincipal(principal, now);
    if (evidence != null && ((Number) evidence.get("valid_until_")).longValue() <= now)
      throw Rejected.conflict();
  }

  private byte[] persistReceipt(EngineStore db, HumanCommand c, String digest, long now) {
    // COMMITTING runs after normal flush and before JDBC commit in pinned CIB 2.1.
    // Read the actual resulting revision, not a predicted increment, on the same
    // enlisted connection. An insert or final commit failure still rolls back all state.
    String resulting = null;
    if (!c.operation().equals("decision")) {
      var rs =
          db.rows(
              "SELECT REV_ FROM ACT_RU_TASK WHERE ID_=? AND TENANT_ID_=?",
              c.taskId(),
              trust.tenant);
      if (rs.size() != 1) throw Rejected.conflict();
      resulting = rs.get(0).get("rev_").toString();
    }
    Map<String, Object> receipt = new TreeMap<>();
    receipt.put("schema", "human-engine-receipt.v1");
    receipt.put("status", "committed");
    receipt.put("tenant", trust.tenant);
    receipt.put("task_id", c.taskId());
    receipt.put("command_id", c.commandId());
    receipt.put("operation", c.operation());
    receipt.put("payload_digest", digest);
    receipt.put("principal_ref", c.principalRef());
    receipt.put("workload_ref", c.workloadRef());
    receipt.put("audit_intent_ref", c.auditIntentRef());
    receipt.put("consumed_task_revision", c.taskRevision());
    receipt.put("resulting_task_revision", resulting);
    receipt.put("engine_receipt_ref", UUID.randomUUID().toString());
    receipt.put("recorded_at", Long.toString(now));
    byte[] bytes = Jcs.canonical(receipt);
    // Mutant: omit durable receipt insert.
    return bytes;
  }
}

package br.com.maezo.human;

import java.nio.file.Path;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.impl.cfg.*;

/** Registered explicitly in Tomcat /camunda/conf/bpm-platform.xml, ADR0049 D5. */
public final class HumanCommandPlugin extends AbstractProcessEnginePlugin {
  private Trust trust;
  private ProcessEngineConfigurationImpl configuration;
  private static volatile HumanCommandPlugin running;

  public HumanCommandPlugin() {}

  HumanCommandPlugin(Trust trust) {
    this.trust = trust;
  }

  @Override
  public void preInit(ProcessEngineConfigurationImpl configuration) {
    if (trust == null) {
      String file = System.getenv("MAEZO_HUMAN_TRUST_FILE");
      if (file == null || file.isBlank())
        throw new IllegalStateException("MAEZO_HUMAN_TRUST_FILE must be explicitly configured");
      try {
        trust = Trust.load(Path.of(file));
      } catch (java.io.IOException ex) {
        throw new IllegalStateException("human trust configuration unavailable");
      }
    }
    if (!trust.engineName.equals(configuration.getProcessEngineName()))
      throw new IllegalStateException("human engine name mismatch");
    this.configuration = configuration;
  }

  @Override
  public void postProcessEngineBuild(ProcessEngine engine) {
    configuration
        .getCommandExecutorTxRequired()
        .execute(context -> new EngineStore(context, trust.tenant).lockTenant());
    running = this;
  }

  static HumanCommandPlugin running() {
    HumanCommandPlugin plugin = running;
    if (plugin == null) throw new Rejected(503, "HUMAN_ENGINE_UNAVAILABLE");
    return plugin;
  }

  byte[] execute(byte[] raw, String peer, String purpose) {
    return switch (purpose) {
      case "human-command" ->
          configuration
              .getCommandExecutorTxRequired()
              .execute(new AtomicHumanCommand(trust, raw, peer));
      case "human-authority" ->
          configuration
              .getCommandExecutorTxRequired()
              .execute(new AuthorityCommand(trust, raw, peer));
      default -> throw Rejected.invalid();
    };
  }

  byte[] receipt(byte[] raw, String peer, String task, String command) {
    return configuration
        .getCommandExecutorTxRequired()
        .execute(
            context -> {
              EngineStore db = new EngineStore(context, trust.tenant);
              db.lockTenant();
              long now = java.time.Instant.now().getEpochSecond();
              var v = Envelope.verify(raw, trust, "human-receipt", peer, now, db::revoked);
              var c = v.command();
              Jcs.keys(
                  c,
                  "schema",
                  "tenant",
                  "workload_ref",
                  "task_id",
                  "command_id",
                  "principal_ref",
                  "principal_issuer",
                  "principal_subject",
                  "payload_digest");
              if (!"human-receipt-query.v1".equals(c.get("schema"))
                  || !task.equals(c.get("task_id"))
                  || !command.equals(c.get("command_id"))) throw Rejected.invalid();
              String principal = Jcs.ref(c, "principal_ref");
              db.principal(
                  principal,
                  Jcs.string(c, "principal_issuer"),
                  Jcs.ref(c, "principal_subject"),
                  now);
              byte[] result =
                  db.receipt(
                      task, command, Jcs.hash(c, "payload_digest"), principal, v.key().workload());
              if (result == null) throw new Rejected(404, "RECEIPT_NOT_FOUND");
              return result;
            });
  }
}

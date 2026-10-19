package br.com.maezo.human;

import java.util.*;
import java.util.function.Supplier;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.regex.Pattern;

/**
 * DL-0134: the engine-side reason a portal read was refused, for the engine LOG only.
 *
 * <p>The answer to the caller does not change: same {@link Rejected} status and code, same body
 * (fail-closed, ADR0049 D5). Only the log line gains a token from this CLOSED vocabulary. A log
 * line never carries exception text, a task id, a principal, a business key, a process variable
 * or a row value: just the route, the status, the reason token and, for the catalog/definition
 * checks, the technical definition id taken from the signed catalog (charset-bounded).
 *
 * <p>Tokens shared with the Python reader vocabulary ({@code read_credentials.UnavailableReason})
 * keep the SAME spelling and meaning; {@code tests/unit/portal/test_portal_read_refusal_vocabulary.py}
 * keeps the two from drifting.
 */
final class PortalReadRefusal {
  private PortalReadRefusal() {}

  enum Reason {
    // Shared with the Python reader vocabulary (same spelling, same meaning).
    UNCLASSIFIED("unclassified"),
    CONFIGURATION_INVALID("configuration_invalid"),
    ADMISSION_INVALID("admission_invalid"),
    CATALOG_INVALID("catalog_invalid"),
    CATALOG_NOT_ADMITTED("catalog_not_admitted"),
    NATIVE_SOURCE_FAILED("native_source_failed"),
    MEMBERSHIP_ROW_ABSENT("membership_row_absent"),
    EVIDENCE_NOT_RECORDED("evidence_not_recorded"),
    // Engine-side only.
    ENGINE_EXCEPTION("engine_exception"),
    PLUGIN_NOT_RUNNING("plugin_not_running"),
    ADMISSION_ABSENT("admission_absent"),
    ADMISSION_WINDOW_CLOSED("admission_window_closed"),
    CLOCK_REGRESSED("clock_regressed"),
    REQUEST_ENVELOPE_EXPIRED("request_envelope_expired"),
    REQUESTER_KEY_REVOKED("requester_key_revoked"),
    CONTINUITY_KEY_INVALID("continuity_key_invalid"),
    CONTINUITY_KEY_EXPIRED("continuity_key_expired"),
    CONTINUITY_INVALID("continuity_invalid"),
    CONTINUITY_WINDOW_MISMATCH("continuity_window_mismatch"),
    CEILING_FROM_FUTURE("ceiling_from_future"),
    CEILING_MISMATCH("ceiling_mismatch"),
    CEILING_MISSING("ceiling_missing"),
    VALIDITY_WINDOW_EXPIRED("validity_window_expired"),
    TRANSACTION_NOT_COMMITTED("transaction_not_committed"),
    RESPONSE_TOO_LARGE("response_too_large"),
    ENGINE_CONNECTION_INVALID("engine_connection_invalid"),
    ROW_BOUND_EXCEEDED("row_bound_exceeded"),
    STORED_ROW_INVALID("stored_row_invalid"),
    NATIVE_WRITE_FAILED("native_write_failed"),
    TENANT_NOT_INSTALLED("tenant_not_installed"),
    CATALOG_NOT_DESIGNATED("catalog_not_designated"),
    CATALOG_REVOKED("catalog_revoked"),
    CATALOG_DESIGNATION_ALTERED("catalog_designation_altered"),
    CATALOG_PUBLISHER_MISMATCH("catalog_publisher_mismatch"),
    CATALOG_DIGEST_MISMATCH("catalog_digest_mismatch"),
    CATALOG_ENTRY_NOT_COMPILED("catalog_entry_not_compiled"),
    CATALOG_DEFINITION_ABSENT("catalog_definition_absent"),
    CATALOG_DEFINITION_OTHER_TENANT("catalog_definition_other_tenant"),
    CATALOG_DEFINITION_KEY_MISMATCH("catalog_definition_key_mismatch"),
    CATALOG_DEFINITION_VERSION_MISMATCH("catalog_definition_version_mismatch"),
    CATALOG_DECISION_ABSENT("catalog_decision_absent"),
    CATALOG_DECISION_MISMATCH("catalog_decision_mismatch"),
    DEPLOYED_MODEL_ABSENT("deployed_model_absent"),
    DEPLOYED_MODEL_MISMATCH("deployed_model_mismatch"),
    DEPLOYED_MODEL_UNREADABLE("deployed_model_unreadable"),
    CATALOG_ARTIFACT_MISMATCH("catalog_artifact_mismatch"),
    TASK_DEFINITION_NOT_IN_CATALOG("task_definition_not_in_catalog"),
    SOURCE_NOT_ADMITTED("source_not_admitted"),
    PUBLICATION_RECEIPT_ABSENT("publication_receipt_absent"),
    PUBLICATION_KEY_REVOKED("publication_key_revoked"),
    PUBLICATION_KEY_UNKNOWN("publication_key_unknown"),
    PUBLICATION_RECEIPT_MISMATCH("publication_receipt_mismatch"),
    RESOURCE_PUBLISHER_NOT_UNIQUE("resource_publisher_not_unique"),
    RESOURCE_NOT_PUBLISHED("resource_not_published"),
    RESOURCE_PROOF_ABSENT("resource_proof_absent"),
    RESOURCE_NOT_COMPLETE("resource_not_complete"),
    RESOURCE_POLICY_MISMATCH("resource_policy_mismatch"),
    RESOURCE_PUBLISHER_MISMATCH("resource_publisher_mismatch"),
    RESOURCE_EXPIRED("resource_expired"),
    CLASSIFICATION_POLICY_MISMATCH("classification_policy_mismatch"),
    CLASSIFICATION_NOT_ADMITTED("classification_not_admitted"),
    IDENTITY_POLICY_NOT_ADMITTED("identity_policy_not_admitted"),
    CANDIDATE_LINK_INVALID("candidate_link_invalid"),
    CANDIDATE_GROUP_OUTSIDE_CATALOG("candidate_group_outside_catalog"),
    NATIVE_IDENTITY_INVALID("native_identity_invalid"),
    NATIVE_IDENTITY_MISMATCH("native_identity_mismatch"),
    MEMBERSHIP_GROUPS_MISMATCH("membership_groups_mismatch"),
    GRANT_AMBIGUOUS("grant_ambiguous"),
    QUEUE_PREFLIGHT_FAILED("queue_preflight_failed");

    final String token;
    Reason(String token) {
      this.token = token;
    }
    /** SQL-computed tokens re-enter the closed set here; anything else is the fallback. */
    static Reason fromToken(Object token, Reason fallback) {
      for (Reason r : values())
        if (r.token.equals(token)) return r;
      return fallback;
    }
  }

  static final Logger LOG = Logger.getLogger(PortalReadRefusal.class.getName());
  static final String PREFIX = "portal_read_refused";
  /** Technical definition ids only: process/decision definition id or key/task key. */
  static final Pattern DEFINITION = Pattern.compile("[A-Za-z0-9_.:/-]{1,160}");
  /** Closed route set: the fixed POST routes of PortalReadServlet and the staff read routes. */
  static final Set<String> ROUTES = Set.of("catalog", "discover", "task", "authority",
      "disclosure", "publications", "staff-case-detail", "staff-case-list", "staff-case-finalize",
      "staff-case-publication", "assignment-context", "assignment-candidates",
      "assignment-authority", "assignment-receipt-authority");

  /** A refusal with its reason. Same status/code as {@link PortalReadModels#unavailable()}. */
  static Rejected unavailable(Reason reason, String definition) {
    var refusal = PortalReadModels.unavailable();
    refusal.reason = Objects.requireNonNull(reason);
    refusal.definition = definition;
    return refusal;
  }

  /**
   * Runs an installation-provider check and, if it refuses as unavailable WITHOUT a reason (the
   * provider JAR has its own refusals), records {@code reason}. The exception object, its status
   * and its code are rethrown unchanged.
   */
  static void tag(Reason reason, Runnable check) {
    try {
      check.run();
    } catch (Rejected refusal) {
      if (refusal.status == 503 && refusal.reason == null) refusal.reason = reason;
      throw refusal;
    }
  }
  static <T> T tagged(Reason reason, Supplier<T> check) {
    try {
      return check.get();
    } catch (Rejected refusal) {
      if (refusal.status == 503 && refusal.reason == null) refusal.reason = reason;
      throw refusal;
    }
  }

  /** The reason carried by the failure (or its causes); never reads a message. */
  static Reason reasonOf(Throwable failure) {
    Throwable t = failure;
    for (int depth = 0; t != null && depth < 8; depth++, t = t.getCause())
      if (t instanceof Rejected r)
        return r.reason == null ? Reason.UNCLASSIFIED : r.reason;
    return Reason.ENGINE_EXCEPTION;
  }
  static String definitionOf(Throwable failure) {
    Throwable t = failure;
    for (int depth = 0; t != null && depth < 8; depth++, t = t.getCause())
      if (t instanceof Rejected r)
        return r.definition != null && DEFINITION.matcher(r.definition).matches() ? r.definition
                                                                                 : null;
    return null;
  }

  /** The ONLY log line: route, status, closed reason and (when present) a definition id. */
  static String line(String route, int status, Throwable failure) {
    String safeRoute = route != null && ROUTES.contains(route) ? route : "other";
    String definition = definitionOf(failure);
    return PREFIX + " route=" + safeRoute + " status=" + status + " reason="
        + reasonOf(failure).token + (definition == null ? "" : " definition=" + definition);
  }

  /** Called by the servlets for every 503 they answer. Never passes the Throwable to the log. */
  static void log(String route, int status, Throwable failure) {
    if (status != 503) return;
    LOG.log(Level.WARNING, line(route, status, failure));
  }
}

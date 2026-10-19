package br.com.maezo.human;

/** ADR0049 D5: bounded technical errors never echo command, principal or evidence. */
public final class Rejected extends RuntimeException {
  public final int status;
  public final String code;
  /** DL-0134: engine-log-only refusal reason (closed vocabulary); never part of the answer. */
  transient PortalReadRefusal.Reason reason;
  /** DL-0134: optional technical definition id for the same log line; never part of the answer. */
  transient String definition;

  public Rejected(int status, String code) {
    super(code);
    this.status = status;
    this.code = code;
  }

  static Rejected invalid() {
    return new Rejected(400, "INVALID_COMMAND");
  }

  static Rejected denied() {
    return new Rejected(403, "AUTHORITY_DENIED");
  }

  static Rejected conflict() {
    return new Rejected(409, "REVISION_CONFLICT");
  }
}

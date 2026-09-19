package br.com.maezo.workload;

/** Fixed safe codes only; never include certificate, request, engine or provider text. */
public final class Refused extends RuntimeException {
  public final int status;
  public final String code;
  private Refused(int status, String code) { super(code); this.status = status; this.code = code; }
  static Refused body() { return new Refused(400, "engine_invalid_body"); }
  static Refused denied() { return new Refused(403, "engine_operation_denied"); }
  static Refused unavailable() { return new Refused(503, "engine_profile_unavailable"); }
  static Refused resource() { return new Refused(403, "engine_resource_mismatch"); }
}

package br.com.maezo.workload;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import jakarta.servlet.ServletException;
import org.apache.catalina.connector.Request;
import org.apache.catalina.connector.Response;
import org.apache.catalina.valves.ValveBase;

/** ADR-0049 D7: terminal refusal after Coyote parsing, before any webapp pipeline. */
public final class TraceRefusalValve extends ValveBase {
  private static final byte[] DENIED =
      "{\"error\":\"engine_operation_denied\"}".getBytes(StandardCharsets.UTF_8);

  public TraceRefusalValve() { super(true); }

  @Override public void invoke(Request request, Response response) throws IOException, ServletException {
    // Coyote leaves unmapped requests to the Engine/Host valves before assigning TRACE 405.
    // Preserve their genuine 404 handling as well as earlier malformed-request errors.
    if (!"TRACE".equals(request.getMethod()) || request.getHost() == null || request.getContext() == null
        || (response.isError() && response.getStatus() != 405)) {
      getNext().invoke(request, response);
      return;
    }
    if (response.isCommitted()) throw new IOException("engine_operation_denied");
    // sendError(405) in Coyote suspended output and set an error: neither may reach a servlet.
    response.reset();
    response.resetError();
    response.setSuspended(false);
    response.setStatus(403);
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setContentType("application/json");
    response.setContentLength(DENIED.length);
    response.getOutputStream().write(DENIED);
  }
}

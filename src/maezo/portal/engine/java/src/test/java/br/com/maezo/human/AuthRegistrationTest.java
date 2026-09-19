package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.lang.reflect.Proxy;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.List;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.ServletOutputStream;
import jakarta.servlet.WriteListener;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;

/** Actual servlet routing with synthetic HTTP collaborators; no engine or TLS qualification. */
class AuthRegistrationTest {
  private static final List<String> ROUTES = List.of(
      "/v1/auth-start", "/v1/auth-documents", "/v1/auth-receipt",
      "/v1/auth-document-context", "/v1/auth-input-publication",
      "/v1/auth-publication-receipt");

  @Test
  void everyAuthRouteReachesExplicitlyUnavailableRuntimeWithoutAnEngine() throws Exception {
    for (String path : ROUTES) {
      var reply = invoke(path, "POST", "application/json", null, 2, true);
      assertEquals(503, reply.status(), path);
      assertEquals("{\"error\":\"HUMAN_ENGINE_UNAVAILABLE\"}", reply.body());
    }
  }

  @Test
  void routesRetainExactMethodMediaQueryAndBodyBoundaries() throws Exception {
    for (String path : ROUTES) {
      assertEquals(404, invoke(path, "GET", "application/json", null, 2, true).status());
      assertEquals(400, invoke(path, "POST", "text/plain", null, 2, true).status());
      assertEquals(400, invoke(path, "POST", "application/json", "tenant=other", 2, true).status());
      assertEquals(400, invoke(path, "POST", "application/json", null, 65537, true).status());
      assertEquals(503, invoke(path, "POST", "application/json", null, 65536, true).status());
      assertEquals(404, invoke(path + "/extra", "POST", "application/json", null, 2, true).status());
    }
  }

  @Test
  void noAuthRouteAcceptsMissingContainerCertificate() throws Exception {
    for (String path : ROUTES) {
      assertEquals(403, invoke(path, "POST", "application/json", null, 2, false).status());
    }
  }

  private record Reply(int status, String body) {}

  private Reply invoke(String path, String method, String media, String query,
      int bytes, boolean certificatePresent) throws Exception {
    X509Certificate certificate;
    try (var input = getClass().getResourceAsStream("/jakarta-servlet-client.pem")) {
      certificate = (X509Certificate) CertificateFactory.getInstance("X.509")
          .generateCertificate(input);
    }
    certificate.checkValidity();
    var body = new ByteArrayInputStream(new byte[bytes]);
    var input = new ServletInputStream() {
      public int read() { return body.read(); }
      public boolean isFinished() { return body.available() == 0; }
      public boolean isReady() { return true; }
      public void setReadListener(ReadListener listener) {}
    };
    var request = (HttpServletRequest) Proxy.newProxyInstance(getClass().getClassLoader(),
        new Class<?>[] {HttpServletRequest.class}, (object, operation, args) -> switch (operation.getName()) {
          case "isSecure" -> true;
          case "getAttribute" -> certificatePresent
              && "jakarta.servlet.request.X509Certificate".equals(args[0])
                  ? new X509Certificate[] {certificate} : null;
          case "getPathInfo" -> path;
          case "getMethod" -> method;
          case "getContentType" -> media;
          case "getQueryString" -> query;
          case "getInputStream" -> input;
          default -> null;
        });
    var output = new ByteArrayOutputStream();
    var status = new int[1];
    var stream = new ServletOutputStream() {
      public void write(int value) { output.write(value); }
      public boolean isReady() { return true; }
      public void setWriteListener(WriteListener listener) {}
    };
    var response = (HttpServletResponse) Proxy.newProxyInstance(getClass().getClassLoader(),
        new Class<?>[] {HttpServletResponse.class}, (object, operation, args) -> {
          if (operation.getName().equals("setStatus")) status[0] = (Integer) args[0];
          if (operation.getName().equals("getOutputStream")) return stream;
          return null;
        });
    var running = HumanCommandPlugin.class.getDeclaredField("running");
    running.setAccessible(true);
    var previous = running.get(null);
    try {
      running.set(null, new HumanCommandPlugin());
      new HumanServlet().service(request, response);
      return new Reply(status[0], output.toString(java.nio.charset.StandardCharsets.UTF_8));
    } finally {
      running.set(null, previous);
    }
  }
}

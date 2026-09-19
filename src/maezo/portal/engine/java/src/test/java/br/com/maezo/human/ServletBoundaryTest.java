package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.io.*;
import java.lang.reflect.Proxy;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.*;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import org.junit.jupiter.api.Test;

/** Unit-level HTTP fence only; actual Tomcat TLS/load proof is a root-owned lane. */
class ServletBoundaryTest {
  @Test
  void onlySecureJakartaCertificateAttributeEstablishesPeerIdentity() throws Exception {
    X509Certificate certificate = certificate("/jakarta-servlet-client.pem");
    X509Certificate expired = certificate("/jakarta-servlet-expired-client.pem");
    certificate.checkValidity();
    assertThrows(java.security.cert.CertificateExpiredException.class, expired::checkValidity);
    var chain = new X509Certificate[] {certificate};

    assertEquals(
        Jcs.digest(certificate.getPublicKey().getEncoded()),
        HumanServlet.peer(request(true, Map.of("jakarta.servlet.request.X509Certificate", chain))));

    for (HttpServletRequest rejected :
        List.of(
            request(true, Map.of("javax.servlet.request.X509Certificate", chain)),
            request(true, Map.of()),
            request(
                true,
                Map.of(
                    "jakarta.servlet.request.X509Certificate", new X509Certificate[0])),
            request(
                true,
                Map.of(
                    "jakarta.servlet.request.X509Certificate",
                    new X509Certificate[] {expired})),
            request(
                false,
                Map.of("jakarta.servlet.request.X509Certificate", chain)))) {
      Rejected denial = assertThrows(Rejected.class, () -> HumanServlet.peer(rejected));
      assertEquals(403, denial.status);
      assertEquals("AUTHORITY_DENIED", denial.code);
    }
  }

  @Test
  void plaintextAndSpoofedForwardedCertificatesNeverReachEngine() throws Exception {
    for (boolean secure : List.of(false, true)) {
      HttpServletRequest request = request(secure, Map.of());
      var output = new ByteArrayOutputStream();
      var status = new int[1];
      HttpServletResponse response =
          (HttpServletResponse)
              Proxy.newProxyInstance(
                  getClass().getClassLoader(),
                  new Class[] {HttpServletResponse.class},
                  (obj, method, args) -> {
                    if (method.getName().equals("setStatus")) status[0] = (Integer) args[0];
                    if (method.getName().equals("getOutputStream"))
                      return new ServletOutputStream() {
                        public void write(int b) {
                          output.write(b);
                        }

                        public boolean isReady() {
                          return true;
                        }

                        public void setWriteListener(WriteListener listener) {}
                      };
                    return null;
                  });
      new HumanServlet().service(request, response);
      assertEquals(403, status[0]);
      assertEquals(
          "{\"error\":\"AUTHORITY_DENIED\"}",
          output.toString(java.nio.charset.StandardCharsets.UTF_8));
    }
  }

  private HttpServletRequest request(boolean secure, Map<String, Object> attributes) {
    return (HttpServletRequest)
        Proxy.newProxyInstance(
            getClass().getClassLoader(),
            new Class[] {HttpServletRequest.class},
            (obj, method, args) ->
                switch (method.getName()) {
                  case "isSecure" -> secure;
                  case "getHeader" -> "attacker-forwarded-certificate-and-private-narrative";
                  case "getAttribute" -> attributes.get((String) args[0]);
                  default -> null;
                });
  }

  private X509Certificate certificate(String resource) throws Exception {
    try (var input = getClass().getResourceAsStream(resource)) {
      return (X509Certificate)
          CertificateFactory.getInstance("X.509").generateCertificate(Objects.requireNonNull(input));
    }
  }
}

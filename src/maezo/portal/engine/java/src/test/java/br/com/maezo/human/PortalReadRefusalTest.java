package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.PortalReadRefusal.Reason;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.*;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.time.Instant;
import java.util.*;
import java.util.logging.*;
import java.util.regex.Pattern;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.*;

/**
 * DL-0134: a refused portal read logs ONE closed reason, and the answer to the caller is unchanged.
 * Every assertion here is about the log line or about the refusal object the servlet maps; none
 * relaxes the fail-closed answer.
 */
class PortalReadRefusalTest {
  static final String PHI = "Maria da Silva CPF 123.456.789-09 tarefa 4711 senha=hunter2";
  final List<LogRecord> logged = new ArrayList<>();
  final Handler capture = new Handler() {
    public void publish(LogRecord r) { logged.add(r); }
    public void flush() {}
    public void close() {}
  };
  @BeforeEach void setUp() { PortalReadRefusal.LOG.addHandler(capture); }
  @AfterEach void tearDown() { PortalReadRefusal.LOG.removeHandler(capture); }

  static Reason reasonOf(Executable call) {
    Rejected r = assertThrows(Rejected.class, call::run);
    // The answer is the one the servlet always gave: 503 READ_DEPENDENCY_UNAVAILABLE, and the
    // exception message is the code itself (no reason, no definition, no value).
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.getMessage());
    assertNull(r.getCause());
    return PortalReadRefusal.reasonOf(r);
  }
  interface Executable { void run() throws Throwable; }

  // ---- each refusal point carries its own reason --------------------------------------------

  @Test void alteredDesignationLogsItsReason() {
    var row = PortalReadDesignationSignedTest.row();
    row.put("digest_", "b".repeat(64));
    assertEquals(Reason.CATALOG_DESIGNATION_ALTERED, reasonOf(() -> PortalReadCommand.requireSigned(row)));
  }
  @Test void catalogArtifactMismatchLogsItsReason() {
    var a = PortalReadModels.record("artifact_ref", "policy-a", "digest",
        Jcs.digest("x".getBytes(StandardCharsets.UTF_8)), "bytes_base64",
        Base64.getEncoder().encodeToString("x".getBytes(StandardCharsets.UTF_8)));
    assertEquals(Reason.CATALOG_ARTIFACT_MISMATCH,
        reasonOf(() -> PortalReadCommand.verifyArtifacts(List.of(a), Set.of())));
    var forged = PortalReadModels.copy(a);
    forged.put("digest", "0".repeat(64));
    assertEquals(Reason.CATALOG_ARTIFACT_MISMATCH,
        reasonOf(() -> PortalReadCommand.verifyArtifacts(List.of(forged), Set.of())));
  }
  @Test void deployedModelChecksLogTheirReasonAndTheDefinitionId() {
    String id = "SP-OP-ESCALATION-001:3:0f6c1d2e-aaaa-bbbb-cccc-0123456789ab";
    Rejected absent = assertThrows(Rejected.class, () -> PortalReadCommand.resourceDigest(null, "a".repeat(64), id));
    assertEquals(Reason.DEPLOYED_MODEL_ABSENT, PortalReadRefusal.reasonOf(absent));
    assertEquals(id, PortalReadRefusal.definitionOf(absent));
    assertEquals(Reason.DEPLOYED_MODEL_MISMATCH, reasonOf(() -> PortalReadCommand.resourceDigest(
        new ByteArrayInputStream(new byte[] {1}), "a".repeat(64), id)));
    InputStream broken = new InputStream() {
      public int read() throws IOException { throw new IOException(PHI); }
    };
    assertEquals(Reason.DEPLOYED_MODEL_UNREADABLE,
        reasonOf(() -> PortalReadCommand.resourceDigest(broken, "a".repeat(64), id)));
    // The old two-argument signature keeps refusing, without a definition.
    Rejected legacy = assertThrows(Rejected.class, () -> PortalReadCommand.resourceDigest(null, "a".repeat(64)));
    assertEquals(Reason.DEPLOYED_MODEL_ABSENT, PortalReadRefusal.reasonOf(legacy));
    assertNull(PortalReadRefusal.definitionOf(legacy));
  }
  @Test void uncompiledCatalogEntryLogsItsReasonAndTheDefinitionKey() {
    var entry = PortalReadModels.record("process_definition_key", "SP-OP-NAO-EXISTE-001",
        "task_definition_key", "UT_Fantasma", "form_key", "x", "form_source_status", "y",
        "allowed_inputs", List.of());
    Rejected r = assertThrows(Rejected.class, () -> PortalReadCommand.compiled(entry));
    assertEquals(Reason.CATALOG_ENTRY_NOT_COMPILED, reasonOf(() -> { throw r; }));
    assertEquals("SP-OP-NAO-EXISTE-001/UT_Fantasma", PortalReadRefusal.definitionOf(r));
  }
  @Test void sharedModelChecksLogTheirReasons() {
    assertEquals(Reason.VALIDITY_WINDOW_EXPIRED, reasonOf(() -> PortalReadModels.current(
        Instant.parse("2026-10-19T00:00:00Z"), "2026-10-18T00:00:00.000000Z")));
    var huge = new ArrayList<Object>();
    for (int i = 0; i < 9000; i++) huge.add("0123456789");
    assertEquals(Reason.RESPONSE_TOO_LARGE, reasonOf(() -> PortalReadModels.bounded(huge)));
    assertEquals(Reason.STORED_ROW_INVALID, reasonOf(() -> PortalReadStore.json(42)));
  }
  @Test void continuityKeyChecksLogTheirReasons() {
    Instant now = Instant.now();
    assertEquals(Reason.CONTINUITY_KEY_INVALID, reasonOf(() -> new PortalReadTrust.NativeKey("k", "1",
        "e".repeat(64), now, now.plusSeconds(60), new SecretKeySpec(new byte[16], "HmacSHA256"), () -> {})));
    var expired = new PortalReadTrust.NativeKey("k", "1", "e".repeat(64), now.minusSeconds(120),
        now.minusSeconds(60), new SecretKeySpec(new byte[32], "HmacSHA256"), () -> {});
    assertEquals(Reason.CONTINUITY_KEY_EXPIRED, reasonOf(() -> expired.current(now)));
  }
  @Test void providerRefusalsAreTaggedOnlyWhenTheyCarryNoReason() {
    // The installation provider (separate JAR) refuses with a bare unavailable(): the call site
    // names it. A reason already set by the engine is never overwritten; other statuses untouched.
    Rejected bare = PortalReadModels.unavailable();
    Rejected tagged = assertThrows(Rejected.class,
        () -> PortalReadRefusal.tag(Reason.CATALOG_NOT_ADMITTED, () -> { throw bare; }));
    assertSame(bare, tagged);
    assertEquals(Reason.CATALOG_NOT_ADMITTED, PortalReadRefusal.reasonOf(tagged));
    Rejected inner = PortalReadModels.unavailable(Reason.CLOCK_REGRESSED);
    assertEquals(Reason.CLOCK_REGRESSED, PortalReadRefusal.reasonOf(assertThrows(Rejected.class,
        () -> PortalReadRefusal.tag(Reason.CATALOG_NOT_ADMITTED, () -> { throw inner; }))));
    Rejected conflict = PortalReadModels.conflict();
    assertNull(assertThrows(Rejected.class,
        () -> PortalReadRefusal.tag(Reason.CATALOG_NOT_ADMITTED, () -> { throw conflict; })).reason);
    Rejected viaSupplier = assertThrows(Rejected.class,
        () -> PortalReadRefusal.tagged(Reason.ADMISSION_INVALID, () -> { throw PortalReadModels.unavailable(); }));
    assertEquals(Reason.ADMISSION_INVALID, PortalReadRefusal.reasonOf(viaSupplier));
  }

  // ---- the answer never changes ----------------------------------------------------------------

  @Test void everyReasonYieldsTheSameAnswerAsTheBareRefusal() {
    Rejected bare = PortalReadModels.unavailable();
    for (Reason reason : Reason.values()) {
      Rejected r = PortalReadModels.unavailable(reason, "SP-OP-X-001:1:abc");
      assertSame(bare.getClass(), r.getClass());
      assertEquals(bare.status, r.status, reason.token);
      assertEquals(bare.code, r.code, reason.token);
      assertEquals(bare.getMessage(), r.getMessage(), reason.token);
      assertFalse(r.toString().contains(reason.token), reason.token);
    }
  }

  // ---- the log line ----------------------------------------------------------------------------

  @Test void logLineHasOnlyRouteStatusReasonAndSafeDefinition() {
    var r = PortalReadModels.unavailable(Reason.CATALOG_DEFINITION_VERSION_MISMATCH, "SP-OP-AUTH-001:7:abc");
    assertEquals("portal_read_refused route=discover status=503 reason=catalog_definition_version_mismatch"
        + " definition=SP-OP-AUTH-001:7:abc", PortalReadRefusal.line("discover", 503, r));
    // A definition outside the technical charset (spaces, PHI-looking text) is dropped, not echoed.
    var unsafe = PortalReadModels.unavailable(Reason.CATALOG_DEFINITION_ABSENT, PHI);
    assertEquals("portal_read_refused route=task status=503 reason=catalog_definition_absent",
        PortalReadRefusal.line("task", 503, unsafe));
    // An unknown route is never echoed.
    assertEquals("portal_read_refused route=other status=503 reason=unclassified",
        PortalReadRefusal.line("/v1/" + PHI, 503, PortalReadModels.unavailable()));
  }
  @Test void exceptionTextNeverReachesTheLog() {
    var wrapped = new IllegalStateException(PHI, new RuntimeException(PHI));
    PortalReadRefusal.log("discover", 503, wrapped);
    var nested = new RuntimeException(PHI, PortalReadModels.unavailable(Reason.RESOURCE_NOT_PUBLISHED));
    PortalReadRefusal.log("task", 503, nested);
    assertEquals(2, logged.size());
    assertEquals("portal_read_refused route=discover status=503 reason=engine_exception",
        logged.get(0).getMessage());
    assertEquals("portal_read_refused route=task status=503 reason=resource_not_published",
        logged.get(1).getMessage());
    for (LogRecord record : logged) {
      assertNull(record.getThrown(), "the Throwable (and its text) is never handed to the log");
      assertNull(record.getParameters());
      assertEquals(Level.WARNING, record.getLevel());
      assertFalse(record.getMessage().contains("CPF") || record.getMessage().contains("Maria")
          || record.getMessage().contains("4711") || record.getMessage().contains("hunter2"));
    }
  }
  @Test void onlyUnavailableAnswersAreLogged() {
    for (int status : List.of(400, 403, 404, 409))
      PortalReadRefusal.log("task", status, new Rejected(status, "X"));
    assertTrue(logged.isEmpty());
  }
  @Test void sqlTokensReEnterTheClosedSet() {
    assertEquals(Reason.RESOURCE_NOT_PUBLISHED,
        Reason.fromToken("resource_not_published", Reason.QUEUE_PREFLIGHT_FAILED));
    assertEquals(Reason.QUEUE_PREFLIGHT_FAILED, Reason.fromToken(PHI, Reason.QUEUE_PREFLIGHT_FAILED));
    assertEquals(Reason.QUEUE_PREFLIGHT_FAILED, Reason.fromToken(null, Reason.QUEUE_PREFLIGHT_FAILED));
  }

  // ---- through the real servlets ---------------------------------------------------------------

  @Test void portalReadServletLogsTheReasonAndAnswersExactlyAsBefore() throws Exception {
    // No engine plugin is running in this JVM: the read refuses with plugin_not_running.
    var answer = serve(new PortalReadServlet(), "/v1/discover", null);
    assertEquals(503, answer.status);
    assertEquals("{\"code\":\"READ_DEPENDENCY_UNAVAILABLE\",\"schema\":\"portal-engine-read-error.v1\"}",
        answer.body);
    assertEquals(List.of("portal_read_refused route=discover status=503 reason=plugin_not_running"),
        messages());
  }
  @Test void portalReadServletNeverLogsTheTextOfAnUnexpectedFailure() throws Exception {
    var answer = serve(new PortalReadServlet(), "/v1/task", new RuntimeException(PHI));
    assertEquals(503, answer.status);
    assertEquals("{\"code\":\"READ_DEPENDENCY_UNAVAILABLE\",\"schema\":\"portal-engine-read-error.v1\"}",
        answer.body);
    assertEquals(List.of("portal_read_refused route=other status=503 reason=engine_exception"), messages());
    assertNull(logged.get(0).getThrown());
  }
  @Test void portalReadServletDoesNotLogNonUnavailableAnswers() throws Exception {
    var answer = serve(new PortalReadServlet(), "/v1/" + PHI, null);
    assertEquals(400, answer.status);
    assertTrue(logged.isEmpty());
  }

  // ---- every refusal site names its reason (structural fence) ------------------------------------

  static final Path HUMAN = Path.of(System.getProperty("basedir", "."))
      .resolve("src/main/java/br/com/maezo/human");
  static final List<String> READ_FILES = List.of("PortalReadCommand.java", "PortalReadStore.java",
      "PortalReadTrust.java", "PortalReadEnvelope.java", "PortalReadPlugin.java");

  @Test void noReadRefusalSiteIsLeftWithoutAReason() throws Exception {
    var bare = new ArrayList<String>();
    for (String name : READ_FILES) {
      var lines = Files.readAllLines(HUMAN.resolve(name), StandardCharsets.UTF_8);
      for (int i = 0; i < lines.size(); i++)
        if (lines.get(i).contains("unavailable()") || lines.get(i).contains("PortalReadModels::unavailable"))
          bare.add(name + ":" + (i + 1));
    }
    assertEquals(List.of(), bare, "every read refusal names a PortalReadRefusal.Reason (DL-0134)");
  }
  @Test void everyReasonIsUsedAndEverySqlTokenIsInTheVocabulary() throws Exception {
    var main = new StringBuilder();
    try (var files = Files.list(HUMAN)) {
      for (Path p : files.filter(p -> p.toString().endsWith(".java")).toList())
        if (!p.getFileName().toString().equals("PortalReadRefusal.java"))
          main.append(Files.readString(p, StandardCharsets.UTF_8));
    }
    String store = Files.readString(HUMAN.resolve("PortalReadStore.java"), StandardCharsets.UTF_8);
    var sqlTokens = new TreeSet<String>();
    var m = Pattern.compile("THEN '([a-z_]+)'").matcher(store);
    while (m.find()) sqlTokens.add(m.group(1));
    assertFalse(sqlTokens.isEmpty());
    var known = new TreeSet<String>();
    for (Reason r : Reason.values()) known.add(r.token);
    sqlTokens.removeAll(known);
    assertEquals(Set.of(), sqlTokens, "a SQL reason token outside the enum would silently fall back");
    var unused = new ArrayList<String>();
    for (Reason r : Reason.values()) {
      boolean used = main.toString().contains("Reason." + r.name()) || store.contains("'" + r.token + "'");
      if (!used && r != Reason.UNCLASSIFIED && r != Reason.ENGINE_EXCEPTION) unused.add(r.name());
    }
    assertEquals(List.of(), unused, "a reason no refusal site can produce is dead vocabulary");
  }

  // ---- helpers -----------------------------------------------------------------------------------

  List<String> messages() {
    return logged.stream().map(LogRecord::getMessage).toList();
  }
  record Answer(int status, String body) {}
  Answer serve(PortalReadServlet servlet, String path, RuntimeException bodyFailure) throws Exception {
    X509Certificate certificate;
    try (var input = getClass().getResourceAsStream("/jakarta-servlet-client.pem")) {
      certificate = (X509Certificate) CertificateFactory.getInstance("X.509")
                        .generateCertificate(Objects.requireNonNull(input));
    }
    var chain = new X509Certificate[] {certificate};
    byte[] raw = "{}".getBytes(StandardCharsets.UTF_8);
    var in = new ByteArrayInputStream(raw);
    var input = new ServletInputStream() {
      public int read() {
        if (bodyFailure != null) throw bodyFailure;
        return in.read();
      }
      public boolean isFinished() { return in.available() == 0; }
      public boolean isReady() { return true; }
      public void setReadListener(ReadListener listener) {}
    };
    var request = (HttpServletRequest) Proxy.newProxyInstance(getClass().getClassLoader(),
        new Class[] {HttpServletRequest.class}, (obj, method, args) -> switch (method.getName()) {
          case "isSecure" -> true;
          case "getAttribute" -> "jakarta.servlet.request.X509Certificate".equals(args[0]) ? chain : null;
          case "getPathInfo" -> path;
          case "getMethod" -> "POST";
          case "getContentType" -> "application/json";
          case "getInputStream" -> input;
          default -> null;
        });
    var output = new ByteArrayOutputStream();
    var status = new int[1];
    var response = (HttpServletResponse) Proxy.newProxyInstance(getClass().getClassLoader(),
        new Class[] {HttpServletResponse.class}, (obj, method, args) -> {
          if (method.getName().equals("setStatus")) status[0] = (Integer) args[0];
          if (method.getName().equals("getOutputStream"))
            return new ServletOutputStream() {
              public void write(int b) { output.write(b); }
              public boolean isReady() { return true; }
              public void setWriteListener(WriteListener listener) {}
            };
          return null;
        });
    servlet.service(request, response);
    return new Answer(status[0], output.toString(StandardCharsets.UTF_8));
  }
}

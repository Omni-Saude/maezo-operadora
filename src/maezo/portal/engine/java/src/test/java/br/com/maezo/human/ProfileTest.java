package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.*;
import java.util.*;
import java.util.stream.Stream;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class ProfileTest {
  static Object resource(String name) throws Exception {
    try (var in = ProfileTest.class.getResourceAsStream("/" + name)) {
      return Jcs.parse(Objects.requireNonNull(in).readAllBytes());
    }
  }

  @Test
  void sharedRfc8785Vectors() throws Exception {
    for (Object item : (List<?>) resource("jcs-vectors.json")) {
      var v = Jcs.object(item);
      assertArrayEquals(
          ((String) v.get("canonical")).getBytes(StandardCharsets.UTF_8),
          Jcs.canonical(Jcs.parse(((String) v.get("input")).getBytes(StandardCharsets.UTF_8))));
    }
  }

  static Stream<String> badJson() {
    return Stream.of(
        "{\"x\":null,\"x\":true}",
        "{\"a\":{\"x\":true,\"x\":false}}",
        "0",
        "-0",
        "1.0",
        "1e2",
        "9007199254740993",
        "NaN",
        "Infinity",
        "-Infinity",
        "\"\\ud800\"",
        "\"\\udfff\"",
        "{\"\\ud800\":true}",
        "{} {}",
        "\ufeff{}");
  }

  @ParameterizedTest
  @MethodSource("badJson")
  void rejectsAmbiguousJson(String raw) {
    assertThrows(Rejected.class, () -> Jcs.parse(raw.getBytes(StandardCharsets.UTF_8)));
  }

  @Test
  void noUtf8ReplacementOrNumericCoercion() {
    assertThrows(Rejected.class, () -> Jcs.parse(new byte[] {34, (byte) 255, 34}));
    assertThrows(Rejected.class, () -> Jcs.canonical(1));
    assertThrows(Rejected.class, () -> Jcs.canonical(Double.NaN));
    String huge = "9".repeat(12000);
    assertEquals(huge, Jcs.parse(Jcs.canonical(huge)));
  }

  @Test
  void pythonSignatureAndCanonicalBytesVerifyInJava() throws Exception {
    var v = Jcs.object(resource("python-ed25519-vector.json"));
    TestKeys keys = new TestKeys();
    var config = keys.config();
    PublicKey publicKey =
        KeyFactory.getInstance("Ed25519")
            .generatePublic(
                new X509EncodedKeySpec(
                    Base64.getDecoder().decode((String) v.get("public_key_spki_base64"))));
    config.put(
        "keys",
        List.of(
            keys.key("command-key", "human-command", "gateway-test", keys.peer, publicKey),
            keys.key(
                "authority-key",
                "human-authority",
                "publisher-test",
                keys.adminPeer,
                keys.authority.getPublic())));
    var verified =
        Envelope.verify(
            ((String) v.get("envelope")).getBytes(StandardCharsets.UTF_8),
            new Trust(config),
            "human-command",
            keys.peer,
            1700000030L,
            x -> false);
    HumanCommand c = HumanCommand.parse(verified.command());
    assertEquals("command-test", c.commandId());
    assertEquals("ACK", c.outcome());
  }

  Map<String, Object> command() throws Exception {
    return Jcs.object(
        Jcs.object(
                Jcs.parse(
                    ((String) Jcs.object(resource("python-ed25519-vector.json")).get("envelope"))
                        .getBytes(StandardCharsets.UTF_8)))
            .get("command"));
  }

  @Test
  void envelopeHasFixedPurposeAudienceAlgorithmKeyWorkloadAndTenant() throws Exception {
    var c = command();
    TestKeys keys = new TestKeys();
    Trust trust = keys.trust();
    Map<String, String> mutations =
        Map.of(
            "algorithm",
            "HS256",
            "purpose",
            "a2a",
            "audience",
            "other",
            "tenant",
            "other",
            "issuer",
            "other",
            "key_id",
            "authority-key",
            "digest",
            "0".repeat(64),
            "schema",
            "v2");
    for (var change : mutations.entrySet()) {
      var e = keys.envelope(c, "human-command", 100, 160);
      e.put(change.getKey(), change.getValue());
      byte[] raw = TestKeys.signEnvelope(e, keys.command.getPrivate());
      assertThrows(
          Rejected.class,
          () -> Envelope.verify(raw, trust, "human-command", keys.peer, 120, x -> false),
          change.getKey());
    }
    byte[] raw = keys.sign(c, "human-command", 100, 160);
    assertThrows(
        Rejected.class,
        () -> Envelope.verify(raw, trust, "human-command", keys.adminPeer, 120, x -> false));
    assertThrows(
        Rejected.class,
        () -> Envelope.verify(raw, trust, "human-command", keys.peer, 120, x -> true));
    assertThrows(
        Rejected.class,
        () -> Envelope.verify(raw, trust, "human-command", keys.peer, 99, x -> false));
    assertThrows(
        Rejected.class,
        () -> Envelope.verify(raw, trust, "human-command", keys.peer, 160, x -> false));
    assertThrows(
        Rejected.class,
        () ->
            Envelope.verify(
                keys.sign(c, "human-command", 100, 161),
                trust,
                "human-command",
                keys.peer,
                120,
                x -> false));
    assertThrows(
        Rejected.class,
        () ->
            Envelope.verify(
                TestKeys.signEnvelope(
                    keys.envelope(c, "human-command", 100, 160), keys.authority.getPrivate()),
                trust,
                "human-command",
                keys.peer,
                120,
                x -> false));
  }

  @Test
  void untrustedCommandEditsCannotSurviveValidSignature() throws Exception {
    TestKeys keys = new TestKeys();
    var c = command();
    var raw = keys.sign(c, "human-command", 100, 160);
    for (String field :
        List.of(
            "principal_ref",
            "principal_subject",
            "workload_ref",
            "command_id",
            "task_id",
            "evidence_revision",
            "authority_revision",
            "membership_revision",
            "task_revision",
            "process_definition_digest",
            "form_digest")) {
      var e = Jcs.object(Jcs.parse(raw));
      var modified = new TreeMap<>(Jcs.object(e.get("command")));
      modified.put(field, "tampered");
      e.put("command", modified);
      assertThrows(
          Rejected.class,
          () ->
              Envelope.verify(
                  Jcs.canonical(e), keys.trust(), "human-command", keys.peer, 120, x -> false),
          field);
    }
  }

  @Test
  void strictTypedCommandRefusesUnknownVarsAndClinicalDecisions() throws Exception {
    var c = command();
    c.put("variables", Map.of("human_approved", true));
    assertThrows(Rejected.class, () -> HumanCommand.parse(c));
    c.remove("variables");
    c.put("outcome", "NEGAR");
    assertThrows(Rejected.class, () -> HumanCommand.parse(c));
  }

  @Test
  void trustMustBeExplicitAndNoCrossPurposeKeyReuse() {
    TestKeys keys = new TestKeys();
    var config = keys.config();
    config.remove("max_lifetime_seconds");
    var missing = config;
    assertThrows(Rejected.class, () -> new Trust(missing));
    config = keys.config();
    config.put(
        "keys",
        List.of(
            keys.key(
                "command-key",
                "human-command",
                "gateway-test",
                keys.peer,
                keys.command.getPublic()),
            keys.key(
                "authority-key",
                "human-authority",
                "publisher-test",
                keys.adminPeer,
                keys.command.getPublic())));
    var invalid = config;
    assertThrows(Rejected.class, () -> new Trust(invalid));
  }
}

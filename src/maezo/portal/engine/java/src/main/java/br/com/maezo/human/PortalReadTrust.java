package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;

import java.io.IOException;
import java.nio.file.*;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.SecretKey;

/** Independent read/publisher trust. No D5 purpose aliases, secret or static admission flag. */
public final class PortalReadTrust {
  final Map<String, Object> scope;
  final String engine, incarnation, audience, deployment, deploymentDigest, configurationDigest;
  final long maxEnvelopeSeconds;
  final Map<String, Key> keys;
  final Providers providers;
  record Key(String id, String purpose, String workload, String peer, String fingerprint,
      PublicKey publicKey, Instant notBefore, Instant notAfter, Set<String> kinds, String catalog) {
  }

  /**
   * Installation-owned SPI. Missing qualified implementation prevents activation.
   * Current D admission is verified by this provider, not manufactured by this plugin.
   * Acquisition (including any locks/I/O) is BEFORE the human tenant lock. All later
   * guards/qualified observations are synchronous, local and perform no reverse locks.
   * configurationDigest is SHA256 of the actual parsed canonical trust record. The
   * provider MUST compare it with installed D admission, never echo a caller release pin.
   */
  public interface Providers {
    Admission acquire(Map<String, Object> scope, String engine, String incarnation,
        String deployment, String digest, String configurationDigest, String purpose);
    NativeKeySet continuity(Admission admission);
    PublicationQualification qualifyPublication(
        Admission admission, Map<String, Object> publication);
  }
  public interface Admission {
    String generation();
    String providerRef();
    String providerRevision();
    String capabilityDigest();
    Instant observedAt();
    Instant validUntil();
    int statementTimeoutSeconds();
    void requireCurrent();
    /** Independently installed source identities/namespaces and committed policy provenance. */
    void verifySource(String kind, Map<String, Object> source);
    void verifyCatalog(Map<String, Object> artifact);
    /** Non-PHI engine IDs and assignee/candidate identity namespaces are explicitly admitted. */
    void verifyIdentityPolicy(
        Map<String, Object> policy, Map<String, Object> task, List<Object> links);
    /** Full projected field/types digest is independently classified, including PAGTO. */
    void verifyClassification(Map<String, Object> classification, Map<String, Object> entry);
  }
  public interface PublicationQualification {
    /** Must compare actual authenticated committed source, never accept sign-these-facts. */
    void verify(String kind, Map<String, Object> source, Map<String, Object> payload);
    void requireCurrent();
    Instant validUntil();
  }
  public interface NativeKeySet {
    NativeKey current();
    NativeKey verification(String configuredKeyId);
    void requireCurrent();
  }
  public static final class NativeKey {
    public final String id, generation, digest;
    public final Instant notBefore, notAfter;
    private final SecretKey secret;
    private final Runnable live;
    public NativeKey(String id, String generation, String digest, Instant before, Instant after,
        SecretKey secret, Runnable live) {
      type("r", id);
      type("n", generation);
      type("h", digest);
      byte[] encoded = secret.getEncoded();
      if (encoded == null || encoded.length != 32 || !"HmacSHA256".equals(secret.getAlgorithm()))
        throw unavailable();
      Arrays.fill(encoded, (byte) 0);
      this.id = id;
      this.generation = generation;
      this.digest = digest;
      this.notBefore = before;
      this.notAfter = after;
      this.secret = secret;
      this.live = live;
    }
    void current(Instant now) {
      live.run();
      if (now.isBefore(notBefore) || !now.isBefore(notAfter))
        throw unavailable();
    }
    byte[] mac(String stage, Map<String, Object> claims, Instant now) {
      current(now);
      try {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(secret);
        mac.update(("maezo/portal-native-read-continuity/v1/" + stage + "\0")
                .getBytes(java.nio.charset.StandardCharsets.US_ASCII));
        return mac.doFinal(Jcs.canonical(claims));
      } catch (GeneralSecurityException ex) {
        throw unavailable();
      }
    }
    @Override
    public String toString() {
      return "NativeReadContinuityKey[redacted]";
    }
  }

  private PortalReadTrust(Map<String, Object> root, Providers providers) {
    Jcs.keys(root, "schema", "scope", "engine_name", "database_incarnation", "audience",
        "read_deployment_ref", "read_deployment_digest", "validity_policy_ref",
        "validity_policy_digest", "max_envelope_seconds", "public_keys");
    if (!"portal-read-trust.v1".equals(root.get("schema")))
      throw invalid();
    configurationDigest = hash(root);
    scope = Collections.unmodifiableMap(copy(validate("scope", root.get("scope"))));
    engine = Jcs.ref(root, "engine_name");
    incarnation = Jcs.ref(root, "database_incarnation");
    audience = Jcs.ref(root, "audience");
    deployment = Jcs.ref(root, "read_deployment_ref");
    deploymentDigest = Jcs.hash(root, "read_deployment_digest");
    Jcs.ref(root, "validity_policy_ref");
    Jcs.hash(root, "validity_policy_digest");
    maxEnvelopeSeconds = number(root.get("max_envelope_seconds"));
    if (maxEnvelopeSeconds < 1)
      throw invalid();
    Map<String, Key> configured = new HashMap<>();
    Set<String> fingerprints = new HashSet<>(), peers = new HashSet<>();
    for (Object o : list(root.get("public_keys"))) {
      var k = map(o);
      String purpose = str(k, "purpose");
      boolean publication = purpose.equals("portal-read-publication");
      if (!publication && !purpose.equals("portal-task-read"))
        throw invalid();
      Set<String> names = new HashSet<>(Set.of("key_id", "purpose", "workload_ref",
          "peer_spki_sha256", "public_key_spki_base64", "not_before", "not_after"));
      if (publication)
        names.addAll(Set.of("publication_kinds", "catalog_ref"));
      if (!k.keySet().equals(names))
        throw invalid();
      try {
        PublicKey pub = KeyFactory.getInstance("Ed25519").generatePublic(
            new X509EncodedKeySpec(b64(k.get("public_key_spki_base64"), false, -1)));
        String fingerprint = Jcs.digest(pub.getEncoded()), peer = Jcs.hash(k, "peer_spki_sha256"),
               id = Jcs.ref(k, "key_id"), workload = Jcs.ref(k, "workload_ref");
        Set<String> kinds = new HashSet<>();
        if (publication)
          for (Object kind : list(k.get("publication_kinds"))) {
            if (!(kind instanceof String s) || !KINDS.contains(s) || !kinds.add(s))
              throw invalid();
          }
        Key key = new Key(id, purpose, workload, peer, fingerprint, pub, time(k.get("not_before")),
            time(k.get("not_after")), Set.copyOf(kinds),
            publication ? Jcs.ref(k, "catalog_ref") : null);
        if (!key.notBefore.isBefore(key.notAfter) || configured.put(id, key) != null
            || !fingerprints.add(fingerprint) || !peers.add(peer))
          throw invalid();
      } catch (GeneralSecurityException ex) {
        throw invalid();
      }
    }
    if (configured.isEmpty() || providers == null)
      throw unavailable();
    keys = Map.copyOf(configured);
    this.providers = providers;
  }
  public static PortalReadTrust load(Path file, Providers providers) throws IOException {
    return new PortalReadTrust(map(Jcs.parse(Files.readAllBytes(file))), providers);
  }
  static PortalReadTrust configured(Map<String, Object> root, Providers providers) {
    return new PortalReadTrust(root, providers);
  }
  Admission acquire(String purpose) {
    return acquire(purpose, scope);
  }
  Admission acquire(String purpose, Map<String, Object> requestScope) {
    Admission a = providers.acquire(requestScope, engine, incarnation, deployment, deploymentDigest,
        configurationDigest, purpose);
    if (a == null)
      throw unavailable();
    a.requireCurrent();
    type("n", a.generation());
    type("r", a.providerRef());
    type("n", a.providerRevision());
    type("h", a.capabilityDigest());
    if (a.statementTimeoutSeconds() < 1 || a.observedAt().isAfter(Instant.now())
        || !Instant.now().isBefore(a.validUntil()))
      throw unavailable();
    return a;
  }
  String publisher(String kind, String catalog) {
    var publishers = keys.values()
                         .stream()
                         .filter(k
                             -> k.purpose.equals("portal-read-publication")
                                 && k.kinds.contains(kind) && k.catalog.equals(catalog))
                         .map(Key::workload)
                         .distinct()
                         .toList();
    if (publishers.size() != 1)
      throw unavailable();
    return publishers.get(0);
  }
  void binding(Map<String, Object> request, Key key) {
    var requested = obj(request, "scope");
    if (!scope.get("tenant").equals(requested.get("tenant"))
        || !scope.get("environment").equals(requested.get("environment"))
        || (key.purpose.equals("portal-task-read") && !scope.equals(requested))
        || !engine.equals(request.get("engine_name"))
        || !incarnation.equals(request.get("database_incarnation"))
        || !deployment.equals(request.get("read_deployment_ref"))
        || !deploymentDigest.equals(request.get("read_deployment_digest"))
        || !key.workload.equals(requested.get("workload_ref")))
      throw denied();
  }
}

package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;

import java.security.*;
import java.time.Instant;
import java.util.*;

/** Exact read-only/publisher envelope; Ed25519 primitives reused, D5 parser unchanged. */
final class PortalReadEnvelope {
  record Verified(Map<String, Object> request, String digest, PortalReadTrust.Key key,
      Instant issued, Instant expires) {
    void current(Instant now) {
      if (now.isBefore(issued) || !now.isBefore(expires) || now.isBefore(key.notBefore())
          || !now.isBefore(key.notAfter()))
        throw unavailable();
    }
    Map<String, Object> requester() {
      return record("issuer", key.workload(), "key_id", key.id(), "public_key_sha256",
          key.fingerprint(), "peer_spki_sha256", key.peer());
    }
  }
  static Verified verify(
      byte[] raw, PortalReadTrust trust, String purpose, String peer, Instant now) {
    var m = map(Jcs.parse(raw));
    Jcs.keys(m, "schema", "purpose", "algorithm", "audience", "issuer", "tenant", "key_id",
        "issued_at", "expires_at", "digest", "request", "signature");
    if (!m.get("schema").equals("portal-read-envelope.v1") || !m.get("purpose").equals(purpose)
        || !m.get("algorithm").equals("Ed25519"))
      throw denied();
    var key = trust.keys.get(str(m, "key_id"));
    if (key == null || !key.purpose().equals(purpose) || !key.peer().equals(peer)
        || !m.get("audience").equals(trust.audience) || !m.get("issuer").equals(key.workload())
        || !m.get("tenant").equals(trust.scope.get("tenant")))
      throw denied();
    Instant issued = Instant.ofEpochSecond(number(m.get("issued_at"))),
            expires = Instant.ofEpochSecond(number(m.get("expires_at")));
    if (!issued.isBefore(expires)
        || expires.getEpochSecond() - issued.getEpochSecond() > trust.maxEnvelopeSeconds
        || issued.isBefore(key.notBefore()) || expires.isAfter(key.notAfter()))
      throw denied();
    var request = purpose.equals("portal-task-read") ? readRequest(m.get("request"))
                                                     : publication(m.get("request"));
    String digest = Jcs.hash(m, "digest");
    if (!hash(request).equals(digest))
      throw denied();
    byte[] sig = b64(m.get("signature"), true, 64);
    var signed = copy(m);
    signed.remove("signature");
    try {
      Signature verifier = Signature.getInstance("Ed25519");
      verifier.initVerify(key.publicKey());
      verifier.update(Jcs.canonical(signed));
      if (!verifier.verify(sig))
        throw denied();
    } catch (GeneralSecurityException ex) {
      throw denied();
    }
    trust.binding(request, key);
    var result = new Verified(request, digest, key, issued, expires);
    result.current(now);
    return result;
  }
  static Map<String, Object> publication(Object value) {
    var m = map(value);
    Jcs.keys(m, "schema", "scope", "engine_name", "database_incarnation", "read_deployment_ref",
        "read_deployment_digest", "publication_id", "expected_authority_revision", "source", "kind",
        "payload");
    if (!m.get("schema").equals("portal-read-publication.v1") || !KINDS.contains(m.get("kind")))
      throw invalid();
    validate("scope", m.get("scope"));
    for (String k :
        List.of("engine_name", "database_incarnation", "read_deployment_ref", "publication_id"))
      type("r", m.get(k));
    type("h", m.get("read_deployment_digest"));
    type("n", m.get("expected_authority_revision"));
    validate("source", m.get("source"));
    validate(str(m, "kind"), m.get("payload"));
    return m;
  }
}

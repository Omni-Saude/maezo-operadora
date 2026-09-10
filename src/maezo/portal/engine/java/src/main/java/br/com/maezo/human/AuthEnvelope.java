package br.com.maezo.human;

import java.security.*;
import java.time.Instant;
import java.util.*;

/** Dedicated E04 domain. No fallback to agent/workload or legacy human purpose mappings. */
final class AuthEnvelope {
  private AuthEnvelope() {}
  record Verified(Map<String,Object> command,String digest,AuthTrust.Key key,Instant issuedAt,Instant expiresAt) {
    void current(Instant now) {
      key.current(now);if(now.isBefore(issuedAt)||!now.isBefore(expiresAt))throw Rejected.denied();
    }
  }
  static Verified verify(byte[] raw,AuthTrust trust,String purpose,String peer,Instant now) {
    var e=Jcs.object(Jcs.parse(raw));
    Jcs.keys(e,"schema","purpose","algorithm","audience","issuer","tenant","key_id","issued_at","expires_at","digest","command","signature");
    if(!"human-auth-envelope.v1".equals(e.get("schema"))||!"Ed25519".equals(e.get("algorithm"))
        ||!purpose.equals(e.get("purpose"))||!trust.audience.equals(e.get("audience"))
        ||!trust.store.tenant.equals(e.get("tenant")))throw Rejected.denied();
    var key=trust.key(Jcs.ref(e,"key_id"),purpose,Jcs.ref(e,"issuer"),peer,now);
    long issued=PortalReadModels.number(e.get("issued_at")),expires=PortalReadModels.number(e.get("expires_at"));
    if(expires<=issued||expires-issued>trust.maxLifetime)throw Rejected.denied();
    Instant from,to;
    try {from=Instant.ofEpochSecond(issued);to=Instant.ofEpochSecond(expires);}catch(java.time.DateTimeException failure){throw Rejected.denied();}
    if(from.isBefore(key.notBefore())||to.isAfter(key.notAfter()))throw Rejected.denied();
    String shape=switch(purpose) {
      case "human-auth-start"->"start";case "human-auth-documents"->"documents";
      case "human-auth-input-publication"->"publication";case "human-auth-publication-read"->"publication-query";
      case "human-auth-read"->switch(Jcs.string(Jcs.object(e.get("command")),"schema")) {
        case "human-auth-receipt-query.v1"->"receipt-query";case "human-auth-document-context-query.v1"->"context-query";
        default->throw Rejected.invalid();
      };
      default->throw Rejected.denied();
    };
    var command=AuthModels.validate(shape,e.get("command"));
    if(!trust.store.scope.equals(command.get("scope"))||!key.issuer().equals(command.get("workload_ref")))throw Rejected.denied();
    String digest=Jcs.hash(e,"digest");if(!PortalReadModels.hash(command).equals(digest))throw Rejected.denied();
    String encoded=Jcs.string(e,"signature");if(!encoded.matches("[A-Za-z0-9_-]{86}"))throw Rejected.denied();
    try {
      byte[] signature=Base64.getUrlDecoder().decode(encoded);
      if(!Base64.getUrlEncoder().withoutPadding().encodeToString(signature).equals(encoded))throw Rejected.denied();
      var signed=new TreeMap<>(e);signed.remove("signature");
      Signature verifier=Signature.getInstance("Ed25519");verifier.initVerify(key.publicKey());verifier.update(Jcs.canonical(signed));
      if(!verifier.verify(signature))throw Rejected.denied();
    } catch(GeneralSecurityException|IllegalArgumentException failure){throw Rejected.denied();}
    var verified=new Verified(PortalReadModels.copy(command),digest,key,from,to);verified.current(now);return verified;
  }
}

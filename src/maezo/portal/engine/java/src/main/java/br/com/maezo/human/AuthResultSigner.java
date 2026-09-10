package br.com.maezo.human;

import java.io.InputStream;
import java.nio.file.*;
import java.security.*;
import java.security.cert.X509Certificate;
import java.time.Instant;
import java.util.*;

/** Private native receipt key, explicitly supplied from protected deployment custody. */
final class AuthResultSigner {
  private final PrivateKey privateKey;
  private final PublicKey publicKey;
  final String keyId,issuer,peerSpki;
  private final Instant certificateFrom,certificateUntil;
  private final java.util.function.Supplier<Instant> clock;
  private AuthResultSigner(PrivateKey privateKey,PublicKey publicKey,String keyId,String issuer,String peerSpki,Instant certificateFrom,Instant certificateUntil) {
    this(privateKey,publicKey,keyId,issuer,peerSpki,certificateFrom,certificateUntil,Instant::now);
  }
  AuthResultSigner(PrivateKey privateKey,PublicKey publicKey,String keyId,String issuer,String peerSpki,
      Instant certificateFrom,Instant certificateUntil,java.util.function.Supplier<Instant> clock) {
    this.privateKey=privateKey;this.publicKey=publicKey;this.keyId=keyId;this.issuer=issuer;this.peerSpki=peerSpki;
    this.certificateFrom=certificateFrom;this.certificateUntil=certificateUntil;this.clock=clock;
  }
  static AuthResultSigner load(Path pkcs12,char[] password,String alias,String keyId,String issuer,String expectedSpki) {
    char[] local=password.clone();
    try {
      if(Files.isSymbolicLink(pkcs12)||!Files.isRegularFile(pkcs12,LinkOption.NOFOLLOW_LINKS)||Files.size(pkcs12)>65536)throw Rejected.denied();
      var store=KeyStore.getInstance("PKCS12");
      try(InputStream stream=Files.newInputStream(pkcs12,LinkOption.NOFOLLOW_LINKS)){store.load(stream,local);}
      if(!(store.getKey(alias,local) instanceof PrivateKey privateKey)||!(store.getCertificate(alias) instanceof X509Certificate certificate))throw Rejected.denied();
      certificate.checkValidity();
      PublicKey publicKey=certificate.getPublicKey();
      String fingerprint=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(publicKey.getEncoded()));
      if(!fingerprint.equals(expectedSpki))throw Rejected.denied();
      byte[] challenge=new byte[32];new SecureRandom().nextBytes(challenge);
      var signer=Signature.getInstance("Ed25519");signer.initSign(privateKey);signer.update(challenge);byte[] proof=signer.sign();
      var verifier=Signature.getInstance("Ed25519");verifier.initVerify(publicKey);verifier.update(challenge);
      if(!verifier.verify(proof))throw Rejected.denied();
      return new AuthResultSigner(privateKey,publicKey,keyId,issuer,fingerprint,certificate.getNotBefore().toInstant(),certificate.getNotAfter().toInstant());
    } catch(Exception failure){throw Rejected.denied();}finally{Arrays.fill(local,'\0');}
  }
  void current(Instant now){if(now.isBefore(certificateFrom)||!now.isBefore(certificateUntil))throw Rejected.denied();}
  static final class Signed {
    private final byte[] bytes;
    private final AuthTrust.Key designation;
    private final Instant expiresAt;
    private final AuthResultSigner owner;
    Signed(byte[] bytes,AuthTrust.Key designation,Instant expiresAt,AuthResultSigner owner) {
      this.bytes=bytes.clone();this.designation=designation;this.expiresAt=expiresAt;this.owner=owner;
    }
    byte[] bytes(){return bytes.clone();}
    void current(Instant now) {
      designation.current(now);owner.current(now);
      if(!now.isBefore(expiresAt))throw Rejected.denied();
    }
  }
  Signed sign(AuthTrust trust,Map<String,Object> result,Instant maximum) {
    return sign(result,maximum,trust.audience,trust.store.tenant,trust.maxLifetime,
      ()->trust.key(keyId,"human-auth-result",issuer,peerSpki,clock.get()));
  }
  Signed sign(Map<String,Object> result,Instant maximum,String audience,String tenant,long maxLifetime,
      java.util.function.Supplier<AuthTrust.Key> lookup) {
    current(clock.get());
    var designation=lookup.get(); // current designation/revocation SQL completes before selecting issue time
    Instant now=clock.get();current(now);designation.current(now);
    if(!keyId.equals(designation.id())||!issuer.equals(designation.issuer())
        ||!"human-auth-result".equals(designation.purpose())||!peerSpki.equals(designation.peerSpki())
        ||!MessageDigest.isEqual(designation.publicKey().getEncoded(),publicKey.getEncoded()))throw Rejected.denied();
    long expires=Math.min(Math.min(now.getEpochSecond()+maxLifetime,designation.notAfter().getEpochSecond()),Math.min(maximum.getEpochSecond(),certificateUntil.getEpochSecond()));
    if(expires<=now.getEpochSecond())throw Rejected.denied();
    var envelope=PortalReadModels.record("schema","human-auth-envelope.v1","purpose","human-auth-result","algorithm","Ed25519",
      "audience",audience,"issuer",issuer,"tenant",tenant,"key_id",keyId,"issued_at",Long.toString(now.getEpochSecond()),
      "expires_at",Long.toString(expires),"digest",PortalReadModels.hash(result),"command",result);
    try {
      Signature signer=Signature.getInstance("Ed25519");signer.initSign(privateKey);signer.update(Jcs.canonical(envelope));
      envelope.put("signature",Base64.getUrlEncoder().withoutPadding().encodeToString(signer.sign()));
      var signed=new Signed(Jcs.canonical(envelope),designation,Instant.ofEpochSecond(expires),this);
      signed.current(clock.get());return signed;
    } catch(GeneralSecurityException failure){throw Rejected.denied();}
  }
}

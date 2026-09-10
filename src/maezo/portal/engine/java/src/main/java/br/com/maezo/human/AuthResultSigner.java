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
  private AuthResultSigner(PrivateKey privateKey,PublicKey publicKey,String keyId,String issuer,String peerSpki,Instant certificateFrom,Instant certificateUntil) {
    this.privateKey=privateKey;this.publicKey=publicKey;this.keyId=keyId;this.issuer=issuer;this.peerSpki=peerSpki;this.certificateFrom=certificateFrom;this.certificateUntil=certificateUntil;
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
  byte[] sign(AuthTrust trust,Map<String,Object> result,Instant now,Instant maximum) {
    current(now);
    var designation=trust.key(keyId,"human-auth-result",issuer,peerSpki,now);
    if(!MessageDigest.isEqual(designation.publicKey().getEncoded(),publicKey.getEncoded()))throw Rejected.denied();
    long expires=Math.min(Math.min(now.getEpochSecond()+trust.maxLifetime,designation.notAfter().getEpochSecond()),Math.min(maximum.getEpochSecond(),certificateUntil.getEpochSecond()));
    if(expires<=now.getEpochSecond())throw Rejected.denied();
    var envelope=PortalReadModels.record("schema","human-auth-envelope.v1","purpose","human-auth-result","algorithm","Ed25519",
      "audience",trust.audience,"issuer",issuer,"tenant",trust.store.tenant,"key_id",keyId,"issued_at",Long.toString(now.getEpochSecond()),
      "expires_at",Long.toString(expires),"digest",PortalReadModels.hash(result),"command",result);
    try {
      Signature signer=Signature.getInstance("Ed25519");signer.initSign(privateKey);signer.update(Jcs.canonical(envelope));
      envelope.put("signature",Base64.getUrlEncoder().withoutPadding().encodeToString(signer.sign()));return Jcs.canonical(envelope);
    } catch(GeneralSecurityException failure){throw Rejected.denied();}
  }
}

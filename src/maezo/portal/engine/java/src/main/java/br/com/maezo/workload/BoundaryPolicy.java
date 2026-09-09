package br.com.maezo.workload;

import br.com.maezo.human.Jcs;
import jakarta.servlet.http.HttpServletRequest;
import java.io.*;
import java.nio.file.*;
import java.security.cert.*;
import java.time.Instant;
import java.util.*;

/** Pinned mounted configuration; reread on every request and again at transaction COMMITTING. */
public final class BoundaryPolicy {
  final Path path;
  final String digest, tenant, environment, engine;
  final long notBefore, expires;
  final Map<String,Object> document;
  final List<Peer> peers;
  final Set<TrustAnchor> roots;
  final List<Map<String,Object>> files;
  final long maxTasks, maxLockMillis, maxPollMillis;
  final int port;

  public record Peer(String certificate, String spki, String issuer, String subject, String san,
                     String purpose, String engineUser, Map<String,Object> identity,
                     long notBefore, long expires, List<Capability> capabilities) {}

  static BoundaryPolicy environment() {
    String file = System.getenv("MAEZO_ENGINE_BOUNDARY_FILE"), hash = System.getenv("MAEZO_ENGINE_BOUNDARY_SHA256");
    if (file == null || hash == null) throw Refused.unavailable();
    return load(Path.of(file),hash);
  }
  public static BoundaryPolicy load(Path path, String digest) {
    try { return new BoundaryPolicy(path,digest,Json.parse(read(path,digest))); }
    catch (RuntimeException e) { throw Refused.unavailable(); }
  }
  private BoundaryPolicy(Path path,String digest,Map<String,Object> m) {
    this.path=path; this.digest=digest; this.document=m;
    Json.keys(m,"protocol","tenant","environment","engine_name","not_before","expires_at","roots","peers","files","listener_port","max_tasks","max_lock_millis","max_poll_millis");
    if (!"maezo.engine-boundary.v1".equals(m.get("protocol"))) throw Refused.unavailable();
    tenant=Json.token(m,"tenant"); environment=Json.token(m,"environment"); engine=Json.token(m,"engine_name");
    notBefore=Json.number(m,"not_before"); expires=Json.number(m,"expires_at");
    maxTasks=Json.number(m,"max_tasks"); maxLockMillis=Json.number(m,"max_lock_millis"); maxPollMillis=Json.number(m,"max_poll_millis");
    long listenerPort=Json.number(m,"listener_port");
    if (maxTasks < 1 || maxTasks > Integer.MAX_VALUE || maxLockMillis < 1 || maxPollMillis < 1 || maxPollMillis > Integer.MAX_VALUE || listenerPort < 1 || listenerPort > 65535) throw Refused.unavailable();
    port=(int)listenerPort;
    roots=new HashSet<>(); files=new ArrayList<>();
    for (Object item : Json.list(m.get("roots"))) {
      var file=Json.object(item); Json.keys(file,"path","sha256"); files.add(file);
      try {
        var cert=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(readFile(file)));
        cert.checkValidity(); if (cert.getBasicConstraints() < 0) throw Refused.unavailable();
        roots.add(new TrustAnchor(cert,null));
      } catch (CertificateException e) { throw Refused.unavailable(); }
    }
    if (roots.isEmpty()) throw Refused.unavailable();
    for (Object item : Json.list(m.get("files"))) {
      var file=Json.object(item); Json.keys(file,"path","sha256"); readFile(file); files.add(file);
    }
    List<Peer> loaded=new ArrayList<>(); Set<String> certs=new HashSet<>(),users=new HashSet<>();
    for (Object item : Json.list(m.get("peers"))) {
      var p=Json.object(item);
      Json.keys(p,"certificate_sha256","spki_sha256","issuer_dn","subject_dn","uri_san","purpose","engine_user","identity","not_before","expires_at","capabilities");
      String purpose=Json.token(p,"purpose");
      if (!Set.of("nonhuman","human-relay","bootstrap","deployment","observer").contains(purpose)) throw Refused.unavailable();
      var identity=Capability.identity(p.get("identity"));
      if (!tenant.equals(identity.get("tenant")) || !environment.equals(identity.get("environment"))) throw Refused.unavailable();
      var capabilities=new ArrayList<Capability>(); Set<String> digests=new HashSet<>();
      for (Object grant : Json.list(p.get("capabilities"))) {
        var capability=new Capability(Json.object(grant));
        if (!purpose.equals("nonhuman") || !identity.equals(capability.identity) || !digests.add(capability.digest)) throw Refused.unavailable();
        capabilities.add(capability);
      }
      String cert=Json.token(p,"certificate_sha256"),spki=Json.token(p,"spki_sha256"),user=Json.token(p,"engine_user");
      if (!cert.matches("[0-9a-f]{64}") || !spki.matches("[0-9a-f]{64}") || !certs.add(cert)) throw Refused.unavailable();
      // Rotation can bind two leaf certificates to one immutable engine user only when all authority is identical.
      Peer peer=new Peer(cert,spki,Json.string(p,"issuer_dn"),Json.string(p,"subject_dn"),Json.token(p,"uri_san"),purpose,user,identity,
          Json.number(p,"not_before"),Json.number(p,"expires_at"),List.copyOf(capabilities));
      if (!identity.get("issuer").equals(peer.issuer()) || !identity.get("subject").equals(peer.san()) || !peer.san().startsWith("spiffe://")) throw Refused.unavailable();
      for (Peer old : loaded) if (old.engineUser().equals(user) && (!old.identity().equals(identity) || !old.purpose().equals(purpose)
          || !old.capabilities().stream().map(c->c.digest).toList().equals(capabilities.stream().map(c->c.digest).toList()))) throw Refused.unavailable();
      users.add(user); loaded.add(peer);
    }
    if (loaded.isEmpty()) throw Refused.unavailable(); peers=List.copyOf(loaded);
    current(null);
  }
  static byte[] readFile(Map<String,Object> file) { return read(Path.of(Json.string(file,"path")),Json.token(file,"sha256")); }
  static byte[] read(Path path,String hash) {
    try {
      if (!path.isAbsolute() || !path.equals(path.toRealPath()) || !Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)
          || !hash.matches("[0-9a-f]{64}")) throw Refused.unavailable();
      byte[] raw;
      try (InputStream in=Files.newInputStream(path)) { raw=in.readNBytes(Json.LIMIT+1); }
      if (raw.length > Json.LIMIT || !Jcs.digest(raw).equals(hash)) throw Refused.unavailable();
      return raw;
    } catch (IOException e) { throw Refused.unavailable(); }
  }
  void current(Peer peer) {
    long now=Instant.now().getEpochSecond();
    if (now < notBefore || now >= expires || expires <= notBefore) throw Refused.unavailable();
    read(path,digest); for (var file:files) readFile(file);
    for(var root:roots)try {root.getTrustedCert().checkValidity();}catch(CertificateException e){throw Refused.unavailable();}
    if (peer != null && (now < peer.notBefore() || now >= peer.expires() || peer.expires() <= peer.notBefore())) throw Refused.denied();
  }
  Peer authenticate(HttpServletRequest request) {
    current(null);
    if (!request.isSecure() || request.getLocalPort()!=port || !"https".equals(request.getScheme())) throw Refused.denied();
    Object attribute=request.getAttribute("jakarta.servlet.request.X509Certificate");
    if (!(attribute instanceof X509Certificate[] chain) || chain.length < 1 || chain.length > 8) throw Refused.denied();
    try {
      for (X509Certificate cert:chain) cert.checkValidity();
      X509Certificate leaf=chain[0];
      if (leaf.getBasicConstraints() >= 0 || leaf.getExtendedKeyUsage()==null || !leaf.getExtendedKeyUsage().contains("1.3.6.1.5.5.7.3.2")
          || leaf.getKeyUsage()==null || !leaf.getKeyUsage()[0]) throw Refused.denied();
      Peer peer=peers.stream().filter(p -> p.certificate().equals(hashCertificate(leaf))).findFirst().orElseThrow(Refused::denied);
      for(X509Certificate cert:chain)if(peer.expires()>cert.getNotAfter().toInstant().getEpochSecond() || peer.notBefore()<cert.getNotBefore().toInstant().getEpochSecond())throw Refused.denied();
      if (!peer.spki().equals(Jcs.digest(leaf.getPublicKey().getEncoded())) || !peer.issuer().equals(leaf.getIssuerX500Principal().getName())
          || !peer.subject().equals(leaf.getSubjectX500Principal().getName())) throw Refused.denied();
      var sans=leaf.getSubjectAlternativeNames();
      if (sans==null || sans.stream().filter(v->Integer.valueOf(6).equals(v.get(0))).count()!=1
          || sans.stream().noneMatch(v->Integer.valueOf(6).equals(v.get(0)) && peer.san().equals(v.get(1)))) throw Refused.denied();
      var certificates=new ArrayList<X509Certificate>(Arrays.asList(chain));
      if (roots.stream().anyMatch(a->a.getTrustedCert().equals(certificates.get(certificates.size()-1)))) certificates.remove(certificates.size()-1);
      var parameters=new PKIXParameters(roots);
      // Revocation is the exact mounted leaf allowlist, pinned in the deployment; absent leaves never authenticate.
      parameters.setRevocationEnabled(false);
      var validated=(PKIXCertPathValidatorResult)CertPathValidator.getInstance("PKIX").validate(CertificateFactory.getInstance("X.509").generateCertPath(certificates),parameters);
      X509Certificate anchor=validated.getTrustAnchor().getTrustedCert();anchor.checkValidity();
      if(peer.expires()>anchor.getNotAfter().toInstant().getEpochSecond())throw Refused.denied();
      current(peer); return peer;
    } catch (java.security.GeneralSecurityException | IllegalArgumentException e) { throw Refused.denied(); }
  }
  static String hashCertificate(X509Certificate cert) {
    try { return Jcs.digest(cert.getEncoded()); } catch (CertificateEncodingException e) { throw Refused.denied(); }
  }
}

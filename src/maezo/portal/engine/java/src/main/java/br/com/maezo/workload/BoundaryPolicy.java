package br.com.maezo.workload;

import br.com.maezo.human.Jcs;
import jakarta.servlet.http.HttpServletRequest;
import java.io.*;
import java.io.IOException;
import java.nio.file.*;
import java.security.cert.*;
import java.time.Instant;
import java.util.*;

/** Pinned mounted configuration; reread on every request and again at transaction COMMITTING. */
public final class BoundaryPolicy {
  final Path path;
  private final Path installedRoot;
  private final long binaryCeiling;
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
  /** V2 reuses only the strict mTLS/file validator; its grants are validated separately. */
  static BoundaryPolicy transportV2(Path path,String digest,Map<String,Object> transport) {
    return new BoundaryPolicy(path,digest,transport);
  }
  private BoundaryPolicy(Path path,String digest,Map<String,Object> m) {
    this.path=path; this.digest=digest; this.document=m;installedRoot=actualInstalledRoot();
    binaryCeiling=binaryDeadline(m);
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
        var cert=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(readFile(file,installedRoot,binaryCeiling)));
        cert.checkValidity(); if (cert.getBasicConstraints() < 0) throw Refused.unavailable();
        roots.add(new TrustAnchor(cert,null));
      } catch (CertificateException e) { throw Refused.unavailable(); }
    }
    if (roots.isEmpty()) throw Refused.unavailable();
    for (Object item : Json.list(m.get("files"))) {
      var file=Json.object(item); Json.keys(file,"path","sha256"); readFile(file,installedRoot,binaryCeiling); files.add(file);
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
  static Path actualInstalledRoot() {
    String value=System.getProperty("catalina.base");if(value==null)return null;
    try {Path root=Path.of(value);if(!root.isAbsolute() || !root.equals(root.toRealPath()))throw Refused.unavailable();return root;}
    catch(IOException e){throw Refused.unavailable();}
  }
  static long binaryDeadline(Map<String,Object> document) {
    try {
      long ceiling=Math.multiplyExact(Json.number(document,"expires_at"),1000L);
      var refs=Json.list(document.get("files")).stream().map(Json::object)
          .filter(f->Path.of(Json.string(f,"path")).getFileName().toString().equals(RuntimeObservationAdmission.ADMISSION_FILE)).toList();
      if(refs.size()>1)throw Refused.unavailable();
      if(!refs.isEmpty()) {
        var ref=refs.get(0);var admission=Json.parse(RuntimeObservationAdmission.readProtected(Path.of(Json.string(ref,"path")),Json.string(ref,"sha256"),65536));
        RuntimeObservationAdmission.validate("ObservationAdmission",admission);
        ceiling=Math.min(ceiling,Instant.parse(Json.string(admission,"original_deadline")).toEpochMilli());
      }
      return ceiling;
    }catch(ArithmeticException e){throw Refused.unavailable();}
  }
  /** Pinned files at or under the 1MiB JSON budget use read(); larger pinned binaries use the deadline-guarded binary reader. */
  static byte[] readFile(Map<String,Object> file,Path root,long originalDeadline) {
    Path path=Path.of(Json.string(file,"path"));String hash=Json.token(file,"sha256");
    try { if(Files.size(path)<=Json.LIMIT)return read(path,hash); }
    catch(IOException sizeUnavailable){throw Refused.unavailable();}
    if(root==null || !root.equals(actualInstalledRoot()))throw Refused.unavailable();
    return readOwnBinary(path,hash,originalDeadline);
  }
  private static final String[] FD_ATTRIBUTES={"dev","ino","mode","uid","nlink","size","lastModifiedTime"};
  private static Map<String,Object> fdAttributes(Path path)throws IOException {
    return java.nio.file.Files.readAttributes(path,"unix:"+String.join(",",FD_ATTRIBUTES));
  }
  private static Set<Path> matchingDescriptors(Map<String,Object> attributes)throws IOException {
    Path directory=Path.of("/proc/self/fd");if(!Files.isDirectory(directory))throw Refused.unavailable();
    var matches=new HashSet<Path>();
    try(var listing=Files.list(directory)) {
      for(Path descriptor:listing.toList())try {
        if(!descriptor.getFileName().toString().matches("[0-9]+"))continue;
        var current=fdAttributes(descriptor);
        if(attributes.get("dev").equals(current.get("dev")) && attributes.get("ino").equals(current.get("ino")))matches.add(descriptor);
      }catch(java.nio.file.NoSuchFileException closedDuringListing) {/* No fact is inferred about a disappeared descriptor. */}
    }
    return matches;
  }
  private static byte[] readOwnBinary(Path path,String hash,long deadline) {
    long started=System.currentTimeMillis(),nano=System.nanoTime();long remaining=deadline-started;
    if(remaining<=0 || remaining>Long.MAX_VALUE/1000000L)throw Refused.unavailable();
    long budget=remaining*1000000L;var clock=new BinaryClock(started,nano,budget,deadline);
    try {
      clock.current();
      if(!path.isAbsolute() || !path.equals(path.toRealPath()) || !hash.matches("[a-f0-9]{64}"))throw Refused.unavailable();
      for(Path ancestor=path;ancestor!=null;ancestor=ancestor.getParent())if(Files.isSymbolicLink(ancestor))throw Refused.unavailable();
      var before=Files.readAttributes(path,"unix:"+String.join(",",FD_ATTRIBUTES),LinkOption.NOFOLLOW_LINKS);
      long owner=((Number)before.get("uid")).longValue();long own=((Number)Files.getAttribute(Path.of(System.getProperty("user.home")),"unix:uid")).longValue();
      long size=((Number)before.get("size")).longValue();int mode=((Number)before.get("mode")).intValue();
      if(!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS) || (mode&0022)!=0 || (owner!=0 && owner!=own)
          || ((Number)before.get("nlink")).longValue()!=1 || size<=0 || size>33_554_432L)throw Refused.unavailable();
      var previous=matchingDescriptors(before);clock.current();
      byte[] bytes;
      try(var channel=java.nio.channels.FileChannel.open(path,StandardOpenOption.READ,LinkOption.NOFOLLOW_LINKS)) {
        var matching=matchingDescriptors(before);matching.removeAll(previous);
        if(matching.size()!=1)throw Refused.unavailable();Path descriptor=matching.iterator().next();
        var opened=fdAttributes(descriptor);
        if(!before.equals(opened) || channel.size()!=size || !channel.isOpen())throw Refused.unavailable();
        clock.current();var output=new ByteArrayOutputStream((int)size);
        var digest=java.security.MessageDigest.getInstance("SHA-256");var buffer=java.nio.ByteBuffer.allocate(65536);long count=0;
        while(true) {
          clock.current();int read=channel.read(buffer);clock.current();
          if(read==-1)break;count+=read;if(count>size)throw Refused.unavailable();
          digest.update(buffer.array(),0,read);output.write(buffer.array(),0,read);buffer.clear();
        }
        if(count!=size || channel.size()!=size || !channel.isOpen() || !opened.equals(fdAttributes(descriptor))
            || !before.equals(Files.readAttributes(path,"unix:"+String.join(",",FD_ATTRIBUTES),LinkOption.NOFOLLOW_LINKS))
            || !hash.equals(java.util.HexFormat.of().formatHex(digest.digest())))throw Refused.unavailable();
        bytes=output.toByteArray();clock.current();
      }
      clock.current();
      if(!before.equals(Files.readAttributes(path,"unix:"+String.join(",",FD_ATTRIBUTES),LinkOption.NOFOLLOW_LINKS)))throw Refused.unavailable();
      return bytes;
    }catch(Exception e){throw Refused.unavailable();}
    finally{clock.current();}
  }
  private static final class BinaryClock {
    private final long wall,nano,budget,deadline;private long lastWall,lastNano;
    private BinaryClock(long wall,long nano,long budget,long deadline) {
      this.wall=wall;this.nano=nano;this.budget=budget;this.deadline=deadline;lastWall=wall;lastNano=nano;
    }
    private void current() {
      long now=System.currentTimeMillis(),currentNano=System.nanoTime(),elapsed=currentNano-nano;
      if(Thread.currentThread().isInterrupted() || now<lastWall || currentNano<lastNano || elapsed<0 || elapsed>=budget || now>=deadline
          || Math.abs((now-wall)-elapsed/1000000L)>5000)throw Refused.unavailable();
      lastWall=now;lastNano=currentNano;
    }
  }
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
    read(path,digest); for (var file:files) readFile(file,installedRoot,binaryCeiling);
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

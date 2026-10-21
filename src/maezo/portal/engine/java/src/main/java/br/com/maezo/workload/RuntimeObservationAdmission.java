package br.com.maezo.workload;

import java.io.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.file.*;
import java.nio.file.attribute.*;
import java.time.*;
import java.time.format.*;
import java.util.*;
import java.util.jar.*;
import br.com.maezo.human.Jcs;

/** Closed TestOnly technical admission. Neither this configuration nor its pins grant an effect. */
final class RuntimeObservationAdmission {
  static final String ADMISSION_FILE="provider-native-observation-admission.json";
  static final String PROFILE_FILE="provider-native-observation-profile.json";
  static final DateTimeFormatter UTC=new DateTimeFormatterBuilder().appendInstant(6).toFormatter();
  static final Map<String,Object> CONTRACT=contract();
  static final String BOOT=UUID.randomUUID().toString();
  private static final Map<String,Instant> RESERVED=new HashMap<>();
  final Map<String,Object> document;
  final Map<String,Object> file;
  final String digest;
  final BoundaryPolicy policy;
  final BoundaryPolicy.Peer peer;
  final Capability capability;
  final Instant deadline;
  final long initialWall,initialNano,budget;
  final NativeMeasurementPort measurement;
  private long lastWall,lastNano;

  private RuntimeObservationAdmission(BoundaryPolicy policy,BoundaryPolicy.Peer peer,Capability cap,
      WorkloadServlet.VerifiedObservationRequest request) {
    this.policy=policy;this.peer=peer;capability=cap;
    file=named(policy.files,ADMISSION_FILE);digest=Json.token(file,"sha256");
    deadline=Instant.parse(Json.string(Json.object(request.fields().get("parameters")),"original_deadline"));
    initialWall=System.currentTimeMillis();initialNano=System.nanoTime();lastWall=initialWall;lastNano=initialNano;
    long remaining=deadline.toEpochMilli()-initialWall;
    require(remaining>0 && remaining<=Long.MAX_VALUE/1000000L);budget=Math.multiplyExact(remaining,1000000L);
    require(!deadline.isAfter(Instant.ofEpochSecond(policy.expires)) && !deadline.isAfter(Instant.ofEpochSecond(peer.expires())));
    document=Json.parse(readProtectedOwned(Path.of(Json.string(file,"path")),digest,65536));validate("ObservationAdmission",document);
    validate("Request",request.fields());
    var params=Json.object(request.fields().get("parameters"));
    require(digest.equals(params.get("admission_sha256")) && document.get("nonce").equals(params.get("nonce"))
        && document.get("nonce").equals(request.fields().get("resource_ref"))
        && document.get("original_deadline").equals(params.get("original_deadline")));
    require("nonhuman".equals(peer.purpose()) && peer.capabilities().size()==1 && peer.capabilities().get(0)==cap
        && document.get("identity").equals(peer.identity()) && document.get("identity").equals(cap.identity)
        && policy.tenant.equals(peer.identity().get("tenant")) && policy.environment.equals(peer.identity().get("environment"))
        && policy.environment.matches("TestOnly-[A-Za-z0-9_.:-]{1,240}")
        && document.get("engine_name").equals(policy.engine)
        && document.get("peer_certificate_sha256").equals(peer.certificate())
        && document.get("peer_spki_sha256").equals(peer.spki())
        && document.get("definition_id").equals(cap.target.get("definition_id"))
        && document.get("process_key").equals(cap.target.get("process_key"))
        && document.get("process_version").equals(cap.target.get("process_version")));
    for(var other:policy.peers)if(other!=peer)require(!peer.spki().equals(other.spki()) && !peer.engineUser().equals(other.engineUser()));
    Instant issued=Instant.parse(Json.string(document,"issued_at"));
    require(!issued.isAfter(Instant.ofEpochMilli(initialWall)) && issued.isBefore(deadline)
        && Duration.between(issued,deadline).compareTo(Duration.ofMinutes(15))<=0
        && !deadline.isAfter(Instant.ofEpochSecond(policy.expires)) && !deadline.isAfter(Instant.ofEpochSecond(peer.expires())));
    for(var anchor:policy.roots)require(!deadline.isAfter(anchor.getTrustedCert().getNotAfter().toInstant()));
    current();
    measurement=NativeMeasurementBinding.forObservation(this,request);
  }
  static RuntimeObservationAdmission admit(BoundaryPolicy policy,BoundaryPolicy.Peer peer,Capability cap,
      WorkloadServlet.VerifiedObservationRequest request) {
    return new RuntimeObservationAdmission(policy,peer,cap,request);
  }
  void reserve() {
    current();String key=peer.certificate()+":"+peer.spki()+":"+digest+":"+BOOT+":"+document.get("nonce");
    synchronized(RESERVED) {
      Instant now=Instant.ofEpochMilli(System.currentTimeMillis());RESERVED.entrySet().removeIf(e->!now.isBefore(e.getValue()));
      require(RESERVED.size()<1024 && !RESERVED.containsKey(key));RESERVED.put(key,deadline);
    }
    current();
  }
  /** Pure wall/monotonic guard. No FD read, SQL, callback or implicit stage re-entry. */
  synchronized void timeOnly() {
    long wall=System.currentTimeMillis(),nano=System.nanoTime();
    require(!Thread.currentThread().isInterrupted() && wall>=lastWall && nano>=lastNano && nano-initialNano<budget && wall<deadline.toEpochMilli()
        && Math.abs((wall-initialWall)-(nano-initialNano)/1000000L)<=5000);
    lastWall=wall;lastNano=nano;
  }
  synchronized void current() {
    timeOnly();policy.current(peer);timeOnly();
    byte[] admissionBytes=readProtectedOwned(Path.of(Json.string(file,"path")),digest,65536);timeOnly();
    require(Arrays.equals(Json.bytes(document),Json.bytes(Json.parse(admissionBytes))));timeOnly();
    readProtectedOwned(Path.of(Json.string(named(policy.files,PROFILE_FILE),"path")),Json.string(document,"profile_sha256"),65536);timeOnly();
  }
  static Map<String,Object> named(List<Map<String,Object>> files,String name) {
    var found=files.stream().filter(f->Path.of(Json.string(f,"path")).getFileName().toString().equals(name)).toList();
    require(found.size()==1);return found.get(0);
  }
  byte[] profile() {
    current();var ref=named(policy.files,PROFILE_FILE);
    require(ref.get("sha256").equals(document.get("profile_sha256")));
    timeOnly();byte[] raw=readProtectedOwned(Path.of(Json.string(ref,"path")),Json.string(document,"profile_sha256"),65536);timeOnly();
    require(Arrays.equals(raw,Json.bytes(Json.parse(raw))));current();return raw;
  }
  private byte[] readProtectedOwned(Path path,String expected,int bound) {return readProtected(path,expected,bound,this);}
  static byte[] readProtected(Path path,String expected,int bound) {return readProtected(path,expected,bound,null);}
  private static final Path FD_ROOT=Path.of("/proc/self/fd");
  private static final String FD_ATTRIBUTES="unix:mode,uid,nlink,ino,dev,size,lastModifiedTime";
  private static byte[] readProtected(Path path,String expected,int bound,RuntimeObservationAdmission owned) {
    if(owned!=null)owned.timeOnly();
    try {
      require(Files.isDirectory(FD_ROOT) && bound>0 && bound<Integer.MAX_VALUE
          && expected!=null && expected.matches("[0-9a-f]{64}"));
      require(path.isAbsolute() && path.equals(path.normalize()) && path.equals(path.toRealPath()));
      for(Path parent=path.getParent();parent!=null;parent=parent.getParent())require(!Files.isSymbolicLink(parent));
      var parentBefore=Files.readAttributes(path.getParent(),FD_ATTRIBUTES,LinkOption.NOFOLLOW_LINKS);
      var before=Files.readAttributes(path,FD_ATTRIBUTES,LinkOption.NOFOLLOW_LINKS);
      long uid=((Number)before.get("uid")).longValue();
      long ownUid=((Number)Files.getAttribute(Path.of("/proc/self"),"unix:uid")).longValue();
      long parentUid=((Number)parentBefore.get("uid")).longValue();
      require(parentUid==0 || parentUid==ownUid);
      require(((Number)parentBefore.get("mode")).intValue()==0040700
          && ((Number)before.get("mode")).intValue()==0100600
          && (uid==0 || uid==ownUid) && ((Number)before.get("nlink")).longValue()==1
          && ((Number)before.get("size")).longValue()>0 && ((Number)before.get("size")).longValue()<=bound);
      var censusBefore=descriptorCensus();if(owned!=null)owned.timeOnly();
      byte[] raw;Path descriptor;
      try(var channel=FileChannel.open(path,StandardOpenOption.READ,LinkOption.NOFOLLOW_LINKS)) {
        if(owned!=null)owned.timeOnly();
        descriptor=openedDescriptor(censusBefore,descriptorCensus());
        verifyOpenedDescriptor(channel,descriptor,before);
        require(parentBefore.equals(Files.readAttributes(path.getParent(),FD_ATTRIBUTES,LinkOption.NOFOLLOW_LINKS)));
        ByteBuffer buf=ByteBuffer.allocate(bound+1);
        while(buf.hasRemaining()) {
          if(owned!=null)owned.timeOnly();int n=channel.read(buf);if(owned!=null)owned.timeOnly();if(n==-1)break;
        }
        raw=Arrays.copyOf(buf.array(),buf.position());
        require(channel.size()==raw.length && raw.length==((Number)before.get("size")).longValue()
            && raw.length>0 && raw.length<=bound && before.equals(fdAttributes(descriptor))
            && descriptorPosition(descriptor)==channel.position()
            && before.equals(Files.readAttributes(path,FD_ATTRIBUTES,LinkOption.NOFOLLOW_LINKS))
            && Jcs.digest(raw).equals(expected));
        if(owned!=null)owned.timeOnly();
      }
      if(owned!=null)owned.timeOnly();
      // An immediately recycled descriptor is ambiguous and cannot earn a custody success.
      require(!Files.exists(descriptor) && before.equals(Files.readAttributes(path,FD_ATTRIBUTES,LinkOption.NOFOLLOW_LINKS))
          && parentBefore.equals(Files.readAttributes(path.getParent(),FD_ATTRIBUTES,LinkOption.NOFOLLOW_LINKS)));
      return raw;
    } catch(IOException|IllegalArgumentException|UnsupportedOperationException e) {throw Refused.unavailable();}
    finally {if(owned!=null)owned.timeOnly();}
  }
  static Map<String,Object> fdAttributes(Path descriptor)throws IOException {
    require(descriptor.getParent().equals(FD_ROOT) && descriptor.getFileName().toString().matches("[0-9]+"));
    return Files.readAttributes(descriptor,FD_ATTRIBUTES);
  }
  /** Full kernel census, including foreign descriptors: exactly the FDs extant at their read. */
  static Map<Path,Map<String,Object>> descriptorCensus()throws IOException {
    require(Files.isDirectory(FD_ROOT));
    var attributes=new HashMap<Path,Map<String,Object>>();
    try(var entries=Files.newDirectoryStream(FD_ROOT)) {
      for(Path descriptor:entries) {
        // Concurrent JDK activity (GC, logging) closes descriptors mid-census; a
        // descriptor absent at its read instant is indistinguishable from one
        // enumerated only after it closed. Vanish per entry; the custody diff
        // stays exact for every descriptor actually read. Only REGULAR files
        // carry protected-file custody: sockets/pipes/epoll of the connection
        // pool, JobExecutor and TLS churn constantly and are not attributable
        // to a protected read (R1 follow-up: the served lane refused on
        // acquired-set drift caused by concurrent socket opens).
        try {
          var read=fdAttributes(descriptor);
          if((((Number)read.get("mode")).intValue()&0xF000)==0x8000)attributes.put(descriptor,read);
        }catch(NoSuchFileException vanished){}
      }
    }
    // The enumeration stream's own descriptors closed with it; any other descriptor
    // may also vanish between its read and this check under concurrent JDK activity.
    for(Path descriptor:new ArrayList<>(attributes.keySet()))if(!Files.exists(descriptor))attributes.remove(descriptor);
    return attributes;
  }
  static Path openedDescriptor(Map<Path,Map<String,Object>> before,Map<Path,Map<String,Object>> after) {
    // Descriptors that only closed between the two censuses are legitimate concurrent
    // JDK activity and are not attributable; survivors keep exact identity so a reused
    // number with a different inode still refuses.
    var survivors=new HashMap<>(before);survivors.keySet().retainAll(after.keySet());
    // Existing FDs can legitimately receive log/file writes while a protected file is opened.
    // Identity and custody changes still make attribution ambiguous; size/mtime belong to the new FD checks.
    for(var old:survivors.entrySet())for(String key:List.of("dev","ino","mode","uid","nlink")) {
      require(old.getValue().get(key)!=null
          && Objects.equals(old.getValue().get(key),after.get(old.getKey()).get(key)));
    }
    var acquired=new HashSet<>(after.keySet());acquired.removeAll(before.keySet());
    require(acquired.size()==1);return acquired.iterator().next();
  }
  static void verifyOpenedDescriptor(FileChannel channel,Path descriptor,Map<String,Object> expected)throws IOException {
    require(channel.isOpen() && expected.equals(fdAttributes(descriptor))
        && channel.size()==((Number)expected.get("size")).longValue());
    // Couple this actual channel's independent file offset to the kernel FD. No inode-only attribution.
    require(channel.position()==0 && descriptorPosition(descriptor)==0);
    long size=channel.size();channel.position(size);require(descriptorPosition(descriptor)==size);
    channel.position(0);require(descriptorPosition(descriptor)==0 && expected.equals(fdAttributes(descriptor)));
  }
  private static long descriptorPosition(Path descriptor)throws IOException {
    Path info=Path.of("/proc/self/fdinfo",descriptor.getFileName().toString());
    try(var reader=Files.newBufferedReader(info,java.nio.charset.StandardCharsets.US_ASCII)) {
      String line;Long position=null;int count=0;
      while((line=reader.readLine())!=null) {
        require(++count<=32 && line.length()<=256);
        if(line.startsWith("pos:")) {require(position==null);position=Long.parseLong(line.substring(4).trim());}
      }
      require(position!=null && position>=0);return position;
    }
  }
  static Map<String,Object> schemaRow() {return Json.object(definition("TestOnlySchema").get("const"));}
  static Map<String,Object> contract() {
    try(var stream=RuntimeObservationAdmission.class.getResourceAsStream("/provider-native-observation-schema-v1.json")) {
      require(stream!=null);return Json.parse(stream.readAllBytes());
    }catch(IOException e){throw Refused.unavailable();}
  }
  static Map<String,Object> definition(String name){return Json.object(Json.object(CONTRACT.get("$defs")).get(name));}
  static void validate(String name,Object value){validateNode(definition(name),value);}
  private static void validateNode(Map<String,Object> rule,Object value) {
    if(rule.containsKey("$ref")){validate(Json.string(rule,"$ref").substring("#/$defs/".length()),value);return;}
    if(rule.containsKey("oneOf")) {
      int matches=0;for(Object alternative:Json.list(rule.get("oneOf")))try {validateNode(Json.object(alternative),value);matches++;}catch(Refused mismatch){}
      require(matches==1);return;
    }
    if(rule.containsKey("const"))require(Objects.equals(rule.get("const"),value));
    if(rule.containsKey("enum"))require(Json.list(rule.get("enum")).contains(value));
    if(rule.containsKey("type"))switch(Json.string(rule,"type")) {
      case "object": {
        var m=Json.object(value);var properties=Json.object(rule.get("properties"));
        require(m.keySet().equals(new HashSet<>(Json.list(rule.get("required")))) && properties.keySet().containsAll(m.keySet()));
        m.forEach((k,v)->validateNode(Json.object(properties.get(k)),v));break;
      }
      case "array": {
        var list=Json.list(value);if(rule.containsKey("minItems"))require(list.size()>=Json.number(rule,"minItems"));
        if(rule.containsKey("maxItems"))require(list.size()<=Json.number(rule,"maxItems"));
        if(Boolean.TRUE.equals(rule.get("uniqueItems")))require(new HashSet<>(list).size()==list.size());
        for(Object item:list)validateNode(Json.object(rule.get("items")),item);break;
      }
      case "null":require(value==null);break;
      case "string": {
        require(value instanceof String);String s=(String)value;
        if(rule.containsKey("pattern"))require(s.matches(Json.string(rule,"pattern")));
        if(rule.containsKey("maxLength"))require(s.length()<=Json.number(rule,"maxLength"));break;
      }
      case "integer":require(value instanceof Long || value instanceof Integer);
        long n=((Number)value).longValue();if(rule.containsKey("minimum"))require(n>=Json.number(rule,"minimum"));
        if(rule.containsKey("maximum"))require(n<=Json.number(rule,"maximum"));break;
      default:throw Refused.unavailable();
    }
  }
  static void require(boolean v){if(!v)throw Refused.unavailable();}
  static String time(Instant t){return UTC.format(t);}
  String time(){timeOnly();return time(Instant.ofEpochMilli(System.currentTimeMillis()));}
}

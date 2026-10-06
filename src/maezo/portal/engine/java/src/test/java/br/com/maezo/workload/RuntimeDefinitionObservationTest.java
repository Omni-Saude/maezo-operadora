package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.jar.*;
import br.com.maezo.human.Jcs;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Pure parsing/custody/comparison tests; these do not claim an engine transaction occurred. */
class RuntimeDefinitionObservationTest {
  @TempDir Path temp;
  static Map<String,Object> request() {
    return RuntimeDefinitionObservation.map("protocol","maezo.engine-operation.v1","capability_digest","a".repeat(64),"operation","read_runtime_definition",
        "process_key","SP-OP-AUTH-001","resource_ref","b".repeat(32),"variables",Map.of(),"correlation",Map.of(),"all_matching",false,
        "error_code","","topic","","message","","worker_id","","source_ref","",
        "parameters",Map.of("admission_sha256","c".repeat(64),"nonce","b".repeat(32),"original_deadline","2026-10-06T07:00:00.000000Z"));
  }
  static WorkloadServlet.VerifiedObservationRequest ingress(byte[] bytes)throws Exception {
    var ctor=WorkloadServlet.VerifiedObservationRequest.class.getDeclaredConstructor(byte[].class);
    assertTrue(Modifier.isPrivate(ctor.getModifiers()));ctor.setAccessible(true);
    try{return ctor.newInstance((Object)bytes);}catch(InvocationTargetException e){throw (RuntimeException)e.getCause();}
  }
  @Test void noCommittingScopeLeaksIntoAbsentOrForeignContext() {
    assertFalse(RuntimeDefinitionObservation.isCommitting(null));assertFalse(RuntimeDefinitionObservation.committingNativeRead(null,new Object()));
  }
  @Test void exactRawHashPreservesWhitespaceAndOrder()throws Exception {
    byte[] compact=Json.bytes(request());byte[] pretty=(" \n"+new String(compact,StandardCharsets.UTF_8)+"\n ").getBytes(StandardCharsets.UTF_8);
    var one=ingress(compact);var two=ingress(pretty);
    assertEquals(one.fields(),two.fields());assertNotEquals(one.digest(),two.digest());assertEquals(Jcs.digest(compact),one.digest());assertEquals(Jcs.digest(pretty),two.digest());
  }
  @Test void carrierDetachesReceivedArrayAndParsedObjects()throws Exception {
    byte[] raw=Json.bytes(request());var carrier=ingress(raw);String hash=carrier.digest();Arrays.fill(raw,(byte)'x');
    assertEquals(hash,carrier.digest());assertEquals(request(),carrier.fields());
    assertThrows(UnsupportedOperationException.class,()->carrier.fields().put("nonce","x"));
    assertThrows(UnsupportedOperationException.class,()->Json.object(carrier.fields().get("parameters")).put("nonce","x"));
  }
  @ParameterizedTest @ValueSource(strings={"wire_sha256","request_sha256","callback","stage","witness","trusted_hash"})
  void noCallerTrustFields(String field){var r=request();r.put(field,"a".repeat(64));assertThrows(Refused.class,()->ingress(Json.bytes(r)));}
  @ParameterizedTest @ValueSource(strings={"start","get_definition","read_active","external_complete","read_runtime_definition "})
  void carrierRejectsOtherCommands(String command){var r=request();r.put("operation",command);assertThrows(Refused.class,()->ingress(Json.bytes(r)));}
  @Test void duplicateKeysUtf8OverflowUnknownNestedRejected() {
    String s=new String(Json.bytes(request()),StandardCharsets.UTF_8);
    assertThrows(Refused.class,()->ingress(s.replace("{\"all_matching\":false","{\"all_matching\":false,\"all_matching\":false").getBytes(StandardCharsets.UTF_8)));
    assertThrows(Refused.class,()->ingress(new byte[]{(byte)0xff}));
    var r=request();var p=new TreeMap<>(Json.object(r.get("parameters")));p.put("caller_deadline",1L);r.put("parameters",p);assertThrows(Refused.class,()->ingress(Json.bytes(r)));
    assertThrows(Refused.class,()->ingress(s.replace("false","9223372036854775808").getBytes(StandardCharsets.UTF_8)));
  }
  @Test void schemaRowDoesNotChangeProduction47Rows() {
    assertEquals(47,Capability.schemas().size());assertFalse(Capability.schemas().containsKey("provider.native.definition.observe.v1"));
    assertEquals("read_runtime_definition",RuntimeObservationAdmission.schemaRow().get("operation"));
  }
  static Map<String,Object> session(String pid,String xid,String observed) {
    return RuntimeDefinitionObservation.map("backend_pid",pid,"backend_start","2026-10-06T04:00:00.000000Z","transaction_started_at","2026-10-06T04:01:00.000000Z",
        "transaction_id_if_assigned",xid,"backend_ssl",true,"backend_tls_version","TLSv1.3","observed_at",observed);
  }
  static Map<String,Object> sample(String pid,String xid,String observed) {
    return RuntimeDefinitionObservation.map("stable_database_projection",Map.of("oid","42"),"binding_projection",Map.of("xml","hash"),
        "runtime_installation_projection",Map.of("boot","same"),"session_transaction",session(pid,xid,observed));
  }
  @Test void stableComparisonAllowsDifferentBackendAcrossStages() {
    var a=sample("40","101","2026-10-06T04:01:01.000000Z");var b=sample("41","102","2026-10-06T04:01:02.000000Z");
    assertDoesNotThrow(()->RuntimeDefinitionObservation.sameStable(a,b));assertThrows(Refused.class,()->RuntimeDefinitionObservation.sameSession(a,b));
  }
  @Test void nullableXidMayBecomeAssignedWithinSameStage() {
    var a=sample("40",null,"2026-10-06T04:01:01.000000Z");var b=sample("40","101","2026-10-06T04:01:02.000000Z");
    assertDoesNotThrow(()->RuntimeDefinitionObservation.sameSession(a,b));
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.sameSession(b,a));
  }
  @ParameterizedTest @ValueSource(strings={"stable_database_projection","binding_projection","runtime_installation_projection"})
  void eachStableProjectionIsCompared(String key) {var a=sample("40",null,"2026-10-06T04:01:01.000000Z");var b=new TreeMap<>(a);b.put(key,Map.of("changed",true));assertThrows(Refused.class,()->RuntimeDefinitionObservation.sameStable(a,b));}
  @Test void assignedXidAndClockRollbackRefuse() {
    var a=sample("40","101","2026-10-06T04:01:02.000000Z");
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.sameSession(a,sample("40","102","2026-10-06T04:01:03.000000Z")));
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.sameSession(a,sample("40","101","2026-10-06T04:01:01.000000Z")));
  }
  byte[] xml(String processes){return ("<?xml version=\"1.0\"?><definitions xmlns=\"http://www.omg.org/spec/BPMN/20100524/MODEL\">"+processes+"</definitions>").getBytes(StandardCharsets.UTF_8);}
  @Test void legitimateXmlSingleProcessAccepted() {assertDoesNotThrow(()->RuntimeDefinitionObservation.validateXml(xml("<process id=\"SP-OP-AUTH-001\"/>")));}
  @Test void foreignMultipleEmptyOversizeAndDtdRejected() {
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.validateXml(xml("<process id=\"wrong\"/>")));
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.validateXml(xml("<process id=\"SP-OP-AUTH-001\"/><process id=\"extra\"/>")));
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.validateXml(new byte[0]));assertThrows(Refused.class,()->RuntimeDefinitionObservation.validateXml(new byte[524289]));
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.validateXml("<!DOCTYPE x [<!ENTITY x SYSTEM 'file:///never-read'>]><x>&x;</x>".getBytes(StandardCharsets.UTF_8)));
  }
  Path protectedFile()throws Exception {
    Files.setPosixFilePermissions(temp,PosixFilePermissions.fromString("rwx------"));Path p=temp.toRealPath().resolve("admission.json");Files.writeString(p,"{}");Files.setPosixFilePermissions(p,PosixFilePermissions.fromString("rw-------"));return p;
  }
  @Test void genuineProtectedFilePositive()throws Exception {Path p=protectedFile();assertArrayEquals("{}".getBytes(StandardCharsets.UTF_8),RuntimeObservationAdmission.readProtected(p,Jcs.digest(Files.readAllBytes(p)),100));}
  @Test void metadataHashSymlinkHardlinkModeAndBoundRefuse()throws Exception {
    Path p=protectedFile();String hash=Jcs.digest(Files.readAllBytes(p));
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(p,"a".repeat(64),100));
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(p,hash,1));
    Path link=temp.resolve("link");Files.createSymbolicLink(link,p);assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(link,hash,100));
    Files.setPosixFilePermissions(p,PosixFilePermissions.fromString("rw-r-----"));assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(p,hash,100));
    Files.setPosixFilePermissions(p,PosixFilePermissions.fromString("rw-------"));Files.createLink(temp.resolve("hard"),p);assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(p,hash,100));
  }
  @Test void jarResourcePinAndMissingEntryRefuse()throws Exception {
    Path jar=temp.toRealPath().resolve("inventory.jar");byte[] raw="a".repeat(64).concat(" lib/a.jar\n").getBytes(StandardCharsets.UTF_8);
    try(var out=new JarOutputStream(Files.newOutputStream(jar))){out.putNextEntry(new JarEntry("provider-native/phase-a/secured-startup-vendor.sha256"));out.write(raw);out.closeEntry();}
    assertEquals(Jcs.digest(raw),RuntimeDefinitionObservation.jarResource(jar,"provider-native/phase-a/secured-startup-vendor.sha256"));
    assertThrows(Refused.class,()->RuntimeDefinitionObservation.jarResource(jar,"missing"));
  }
  @Test void timestampSourceUsesActualInstantSixDecimals() {assertEquals("2026-10-06T04:00:00.123000Z",RuntimeObservationAdmission.time(java.time.Instant.parse("2026-10-06T04:00:00.123Z")));}
  record FixtureAdmission(BoundaryPolicy policy,BoundaryPolicy.Peer peer,Capability cap,byte[] raw,Path path) {
    RuntimeObservationAdmission admit()throws Exception{return RuntimeObservationAdmission.admit(policy,peer,cap,ingress(raw));}
  }
  FixtureAdmission admissionFixture(java.time.Instant deadline)throws Exception {
    return admissionFixture(java.time.Instant.now().minusSeconds(1),deadline);
  }
  FixtureAdmission admissionFixture(java.time.Instant issued,java.time.Instant deadline)throws Exception {
    temp=temp.toRealPath();Files.setPosixFilePermissions(temp,PosixFilePermissions.fromString("rwx------"));
    var manifest=Fixtures.manifest(temp);manifest.put("environment","TestOnly-unit-observation");
    // Longer TestOnly policy/peer validity isolates the admission fifteen-minute ceiling.
    long expires=java.time.Instant.now().getEpochSecond()+1800;manifest.put("expires_at",expires);
    var peerDoc=new TreeMap<>(Json.object(Json.list(manifest.get("peers")).get(0)));
    var identity=new TreeMap<>(Json.object(peerDoc.get("identity")));identity.put("environment","TestOnly-unit-observation");identity.put("workload","provider-native-observer");
    peerDoc.put("identity",identity);peerDoc.put("expires_at",expires);
    var target=Map.of("process_key","SP-OP-AUTH-001","process_version",1L,"definition_id","unit-definition","topic","","message","");
    var document=RuntimeDefinitionObservation.map("protocol","maezo.engine-capability.v1","identity",identity,"target",target,"schema",RuntimeObservationAdmission.schemaRow(),"worker_id","","source_target",null);
    var binding=Map.of("document",document,"digest",Json.digest(document),"source_kind","","source_worker_id","","attestations",List.of());
    peerDoc.put("capabilities",List.of(binding));manifest.put("peers",List.of(peerDoc));
    Path profile=temp.resolve(RuntimeObservationAdmission.PROFILE_FILE);Files.writeString(profile,"{}");Files.setPosixFilePermissions(profile,PosixFilePermissions.fromString("rw-------"));
    var db=RuntimeDefinitionObservation.map("database_name","unit-expectation","database_oid","1","system_identifier","2","engine_schema_name","cibseven","engine_schema_oid","3", "native_schema_name","maezo_native","native_schema_oid","4","current_user","cibseven_app","session_user","cibseven_app","catalogue_projection_sha256","a".repeat(64),"witness_projection","provider-native-physical-db-join.v2");
    var admission=RuntimeDefinitionObservation.map("schema","provider-native-observation-admission.v4","identity",identity,
        "peer_certificate_sha256",peerDoc.get("certificate_sha256"),"peer_spki_sha256",peerDoc.get("spki_sha256"),"engine_name","default", "definition_id","unit-definition","deployment_id","unit-deployment", "process_key","SP-OP-AUTH-001","process_version",1L,
        "xml_sha256","a".repeat(64),"profile_sha256",Jcs.digest(Files.readAllBytes(profile)),"candidate_sha","c".repeat(40),"expected_image_id","sha256:"+"d".repeat(64),
        "native_jar_sha256","e".repeat(64),"support_jar_sha256","f".repeat(64),"descriptor_sha256","a".repeat(64),"observation_schema_sha256","b".repeat(64),
        "startup_variant","phase-a","startup_descriptor_inventory_sha256","c".repeat(64),"startup_vendor_inventory_sha256","d".repeat(64),
        "nonce",UUID.randomUUID().toString().replace("-",""),"issued_at",RuntimeObservationAdmission.time(issued),"original_deadline",RuntimeObservationAdmission.time(deadline),
        "expected_database",Map.of("stable_projection",db,"root_pg_preboot_witness_sha256","a".repeat(64)),"rest_spi_jar_sha256","e".repeat(64));
    Path path=temp.resolve(RuntimeObservationAdmission.ADMISSION_FILE);Files.write(path,Json.bytes(admission));Files.setPosixFilePermissions(path,PosixFilePermissions.fromString("rw-------"));
    String digest=Jcs.digest(Files.readAllBytes(path));
    manifest.put("files",List.of(Map.of("path",path.toString(),"sha256",digest),Map.of("path",profile.toString(),"sha256",Jcs.digest(Files.readAllBytes(profile)))));
    var policy=Fixtures.load(temp,manifest);var peer=policy.peers.get(0);var cap=peer.capabilities().get(0);
    var request=request();request.put("capability_digest",cap.digest);request.put("resource_ref",admission.get("nonce"));
    request.put("parameters",Map.of("admission_sha256",digest,"nonce",admission.get("nonce"),"original_deadline",admission.get("original_deadline")));
    return new FixtureAdmission(policy,peer,cap,Json.bytes(request),path);
  }
  // These use an explicit TestOnly admission and capture object, not an engine/context witness.
  RuntimeDefinitionObservation.CapturedObservation clockCapture(RuntimeObservationAdmission admitted) {
    var token=new RuntimeDefinitionObservation.StageToken(null,null,null,null,admitted);
    return new RuntimeDefinitionObservation.CapturedObservation(null,null,token,WorkloadCommand.ObservationStage.CAPTURE,0);
  }
  @Test void captureClockExcludesAdmissionPreflightAndKeepsOriginalBudget()throws Exception {
    var admitted=admissionFixture(java.time.Instant.now().plusSeconds(30)).admit();
    long initialWall=admitted.initialWall,initialNano=admitted.initialNano,budget=admitted.budget;
    Thread.sleep(100);var captured=clockCapture(admitted);Thread.sleep(20);captured.finishClock();
    long elapsed=Long.parseLong(Json.string(captured.clock,"monotonic_elapsed_ns"));
    long wallSpan=java.time.Duration.between(java.time.Instant.parse(captured.started),java.time.Instant.parse(captured.cutoff)).toNanos();
    assertTrue(elapsed>=0);assertTrue(Math.abs(wallSpan-elapsed)<=1_000_000L,"same sample interval at nominal1ms");
    assertTrue(System.nanoTime()-initialNano-elapsed>=80_000_000L,"preflight is outside measurement interval");
    assertEquals(initialWall,admitted.initialWall);assertEquals(initialNano,admitted.initialNano);assertEquals(budget,admitted.budget);
    assertEquals(captured.cutoff,captured.clock.get("engine_clock"));
  }
  @Test void captureClockIgnoresTestOnlyCibClockOverride()throws Exception {
    var admitted=admissionFixture(java.time.Instant.now().plusSeconds(30)).admit();
    org.cibseven.bpm.engine.impl.util.ClockUtil.reset();
    try {
      var artificial=java.util.Date.from(java.time.Instant.parse("2000-01-01T00:00:00Z"));
      org.cibseven.bpm.engine.impl.util.ClockUtil.setCurrentTime(artificial);
      long before=System.currentTimeMillis();var captured=clockCapture(admitted);captured.finishClock();long after=System.currentTimeMillis();
      long measured=java.time.Instant.parse(Json.string(captured.clock,"engine_clock")).toEpochMilli();
      assertTrue(measured>=before && measured<=after);assertEquals("jvm-system-currentTimeMillis",captured.clock.get("source"));
      assertNotEquals(artificial.toInstant(),java.time.Instant.parse(Json.string(captured.clock,"engine_clock")));
    }finally {org.cibseven.bpm.engine.impl.util.ClockUtil.reset();}
  }
  @Test void clockCaptureRejectsAbsentAdmission() {
    assertThrows(Refused.class,()->new RuntimeDefinitionObservation.CapturedObservation(null,null,null,WorkloadCommand.ObservationStage.CAPTURE,0));
    var token=new RuntimeDefinitionObservation.StageToken(null,null,null,null,null);
    assertThrows(Refused.class,()->new RuntimeDefinitionObservation.CapturedObservation(null,null,token,WorkloadCommand.ObservationStage.CAPTURE,0));
  }
  @Test void delayedCaptureCannotRenewAdmissionBudget()throws Exception {
    var admitted=admissionFixture(java.time.Instant.now().plusSeconds(2)).admit();
    var captured=clockCapture(admitted);long originalBudget=admitted.budget;
    Thread.sleep(Math.max(1,java.time.Duration.between(java.time.Instant.now(),admitted.deadline).toMillis()+10));
    assertThrows(Refused.class,captured::finishClock);assertThrows(Refused.class,()->clockCapture(admitted));
    assertNull(captured.clock);assertNull(captured.cutoff);assertEquals(originalBudget,admitted.budget);
  }
  @ParameterizedTest @ValueSource(longs={-1,0})
  void admissionAcceptsAtMostFifteenMinutes(long offsetMicros)throws Exception {
    var issued=java.time.Instant.now().minusSeconds(1).truncatedTo(java.time.temporal.ChronoUnit.MILLIS);
    var deadline=issued.plusSeconds(900).plusNanos(offsetMicros*1000);
    assertDoesNotThrow(()->admissionFixture(issued,deadline).admit());
  }
  @ParameterizedTest @ValueSource(longs={1,1000,60000000})
  void admissionRefusesBeyondFifteenMinutes(long offsetMicros)throws Exception {
    var issued=java.time.Instant.now().minusSeconds(1).truncatedTo(java.time.temporal.ChronoUnit.MILLIS);
    var deadline=issued.plusSeconds(900).plusNanos(offsetMicros*1000);
    var f=admissionFixture(issued,deadline);assertThrows(Refused.class,f::admit);
  }
  @Test void admissionFifteenMinuteRuleDoesNotRelaxIssuedOrdering()throws Exception {
    var issued=java.time.Instant.now().plusSeconds(2).truncatedTo(java.time.temporal.ChronoUnit.MILLIS);
    var f=admissionFixture(issued,issued.plusSeconds(30));assertThrows(Refused.class,f::admit);
  }
  @Test void admittedTechnicalFixturePositiveAndReplayRefusal()throws Exception {
    var f=admissionFixture(java.time.Instant.now().plusSeconds(30));var first=f.admit();first.reserve();
    assertThrows(Refused.class,()->f.admit().reserve());
  }
  @Test void expiryAfterWaitUsesOriginalCeiling()throws Exception {
    var f=admissionFixture(java.time.Instant.now().plusSeconds(2));var admitted=f.admit();admitted.reserve();
    Thread.sleep(Math.max(1,java.time.Duration.between(java.time.Instant.now(),admitted.deadline).toMillis()+10));
    assertThrows(Refused.class,admitted::current);assertEquals(Json.object(Json.parse(f.raw()).get("parameters")).get("original_deadline"),admitted.document.get("original_deadline"));
  }
  @Test void admissionOrProfileLossAfterReservationRefuses()throws Exception {
    var f=admissionFixture(java.time.Instant.now().plusSeconds(30));var admitted=f.admit();admitted.reserve();
    Files.writeString(f.path(),"{}");assertThrows(Refused.class,admitted::current);
  }
  @Test void callerCannotChangeNonceOrDeadline()throws Exception {
    var f=admissionFixture(java.time.Instant.now().plusSeconds(30));var req=new TreeMap<>(Json.parse(f.raw()));var params=new TreeMap<>(Json.object(req.get("parameters")));
    params.put("original_deadline","2026-10-07T00:00:00.000000Z");req.put("parameters",params);
    assertThrows(Refused.class,()->RuntimeObservationAdmission.admit(f.policy(),f.peer(),f.cap(),ingress(Json.bytes(req))));
  }
  @Test void oneOfNullableAssignedXidRejectsCallerTypes() {
    var s=session("40",null,"2026-10-06T04:01:01.000000Z");s.putAll(RuntimeDefinitionObservation.map("transaction_id_source","pg_current_xact_id_if_assigned","connection_source","actual-commandcontext-dbsqlsession-connection","context_connection_identity","enlisted-object-identity-checked","auto_commit",false,"transaction_isolation","READ_COMMITTED","transaction_started_at_source","postgresql-transaction_timestamp-on-enlisted-connection","observed_at_source","postgresql-clock_timestamp-on-enlisted-connection"));
    assertDoesNotThrow(()->RuntimeObservationAdmission.validate("SessionTransactionWitness",s));s.put("transaction_id_if_assigned",42L);
    assertThrows(Refused.class,()->RuntimeObservationAdmission.validate("SessionTransactionWitness",s));
  }

  @Test void liveResourceIsExactV4SuccessorWithNoV3Fallback()throws Exception {
    try(var in=RuntimeObservationAdmission.class.getResourceAsStream("/provider-native-observation-schema-v1.json")) {
      assertNotNull(in);assertEquals("d0affa2deb9c75e8aa2a52b52abc70e8052e81a9c1d98540af889ec32d27e2a1",Jcs.digest(in.readAllBytes()));
    }
    var f=admissionFixture(java.time.Instant.now().plusSeconds(30));var v3=new TreeMap<>(f.admit().document);
    v3.put("schema","provider-native-observation-admission.v3");assertThrows(Refused.class,()->RuntimeObservationAdmission.validate("ObservationAdmission",v3));
    var responseProperties=Json.object(RuntimeObservationAdmission.definition("Response").get("properties"));
    var resultProperties=Json.object(Json.object(responseProperties.get("result")).get("properties"));
    var recheckProperties=Json.object(Json.object(resultProperties.get("emission_recheck")).get("properties"));
    assertEquals("finite-private-emission-stage.v1",Json.object(recheckProperties.get("validation_protocol")).get("const"));
  }
  @Test void repeatedOwnedFdGuardsTerminateWithoutRecursiveNativeRead()throws Exception {
    var f=admissionFixture(java.time.Instant.now().plusSeconds(30));var admitted=f.admit();
    assertTimeout(java.time.Duration.ofSeconds(5),()->{for(int n=0;n<100;n++)admitted.current();});
  }
  /** The preserved 825cb0b7 failure: an FD closed between enumeration and its attribute
   * read raised NoSuchFileException and was sanitized into Refused with the errno lost.
   * The census must vanish-tolerate per entry under concurrent churn. */
  @Test void descriptorCensusToleratesConcurrentDescriptorChurn()throws Exception {
    org.junit.jupiter.api.Assumptions.assumeTrue(Files.isDirectory(Path.of("/proc/self/fd")));
    var churn=new ArrayList<java.nio.channels.FileChannel>();
    var closer=new Thread(()-> {
      var rnd=new Random(4242);
      while(!Thread.currentThread().isInterrupted()) {
        try {
          var channel=java.nio.channels.FileChannel.open(Path.of("/proc/self/environ"),
              java.nio.file.StandardOpenOption.READ);
          synchronized(churn) {churn.add(channel);if(churn.size()>32){churn.get(0).close();churn.remove(0);}}
          if(rnd.nextInt(8)==0)try(var victim=java.nio.channels.FileChannel.open(Path.of("/proc/self/environ"),
              java.nio.file.StandardOpenOption.READ)){Thread.sleep(0,100);}
        }catch(InterruptedException interrupted){return;}
        catch(Exception ignored){}
      }
    },"census-churn");
    closer.setDaemon(true);closer.start();
    try {assertTimeout(java.time.Duration.ofSeconds(10),()->{for(int n=0;n<200;n++)RuntimeObservationAdmission.descriptorCensus();});}
    finally {
      closer.interrupt();synchronized(churn){for(var channel:churn)try{channel.close();}catch(Exception ignored){}}
    }
  }
  @Test void terminalPureGuardHasNoHiddenFilesystemRead()throws Exception {
    var f=admissionFixture(java.time.Instant.now().plusSeconds(30));var admitted=f.admit();Files.delete(f.path());
    assertDoesNotThrow(admitted::timeOnly);assertThrows(Refused.class,admitted::current);
  }
  @ParameterizedTest @ValueSource(strings={"completedStage","ready","internal","internalWait","validation_protocol","callback"})
  void callerCannotClassifyWaitsOrCompleteStage(String field){var r=request();r.put(field,true);assertThrows(Refused.class,()->ingress(Json.bytes(r)));}
  @Test void completionStageHasNoPublicConstructorOrSetter()throws Exception {
    Class<?> stage=Class.forName("br.com.maezo.workload.RuntimeDefinitionObservation$EmissionValidationStage");
    assertTrue(Modifier.isPrivate(stage.getModifiers()));
    for(var ctor:stage.getDeclaredConstructors())assertTrue(Modifier.isPrivate(ctor.getModifiers()));
    for(var method:stage.getDeclaredMethods())assertTrue(Modifier.isPrivate(method.getModifiers()));
  }

  Path largeOwnJar(Path root)throws Exception {
    Path jar=root.resolve("lib/maezo-human-command.jar");Files.createDirectories(jar.getParent());
    byte[] payload=new byte[1_100_000];new Random(92179).nextBytes(payload);
    try(var archive=new JarOutputStream(Files.newOutputStream(jar))) {archive.putNextEntry(new JarEntry("TEST_ONLY_GENERATED_BINARY.bin"));archive.write(payload);archive.closeEntry();}
    Files.setPosixFilePermissions(jar,PosixFilePermissions.fromString("rw-r--r--"));assertTrue(Files.size(jar)>Json.LIMIT);return jar;
  }
  @Test void genuineLargeJarRequiresLinuxRealFdOrExplicitUnsupportedRefusal()throws Exception {
    Path root=temp.toRealPath(),jar=largeOwnJar(root);String previous=System.getProperty("catalina.base");
    System.setProperty("catalina.base",root.toString());
    try {
      var ref=Map.<String,Object>of("path",jar.toString(),"sha256",Jcs.digest(Files.readAllBytes(jar)));
      if(Files.isDirectory(Path.of("/proc/self/fd")))assertArrayEquals(Files.readAllBytes(jar),BoundaryPolicy.readFile(ref,root,System.currentTimeMillis()+30000));
      else assertThrows(Refused.class,()->BoundaryPolicy.readFile(ref,root,System.currentTimeMillis()+30000));
      assertThrows(Refused.class,()->BoundaryPolicy.read(jar,Json.string(ref,"sha256")),"public JSON reader retains1MiB even for ownJar");
    }finally{if(previous==null)System.clearProperty("catalina.base");else System.setProperty("catalina.base",previous);}
  }
  @Test void binaryReaderRejectsWrongRootExpiryModeHardlinkAndNonOwnLargeFile()throws Exception {
    Path root=temp.toRealPath(),jar=largeOwnJar(root);String previous=System.getProperty("catalina.base");System.setProperty("catalina.base",root.toString());
    try {
      var ref=Map.<String,Object>of("path",jar.toString(),"sha256",Jcs.digest(Files.readAllBytes(jar)));
      assertThrows(Refused.class,()->BoundaryPolicy.readFile(ref,root,System.currentTimeMillis()-1));
      Path alien=Files.createDirectory(root.resolve("alien"));Path alienJar=largeOwnJar(alien);
      assertThrows(Refused.class,()->BoundaryPolicy.readFile(Map.of("path",alienJar.toString(),"sha256",Jcs.digest(Files.readAllBytes(alienJar))),alien,System.currentTimeMillis()+30000));
      Files.setPosixFilePermissions(jar,PosixFilePermissions.fromString("rw-rw-rw-"));assertThrows(Refused.class,()->BoundaryPolicy.readFile(ref,root,System.currentTimeMillis()+30000));
      Files.setPosixFilePermissions(jar,PosixFilePermissions.fromString("rw-r--r--"));Files.createLink(root.resolve("hardlink.jar"),jar);
      assertThrows(Refused.class,()->BoundaryPolicy.readFile(ref,root,System.currentTimeMillis()+30000));
      Path other=root.resolve("lib/vendor-other.jar");Files.copy(jar,other);var otherRef=Map.<String,Object>of("path",other.toString(),"sha256",Jcs.digest(Files.readAllBytes(other)));
      assertThrows(Refused.class,()->BoundaryPolicy.readFile(otherRef,root,System.currentTimeMillis()+30000));
    }finally{if(previous==null)System.clearProperty("catalina.base");else System.setProperty("catalina.base",previous);}
  }
  @Test void binaryGuardRejectsSymlinkAndDigestMismatchOnLinux()throws Exception {
    Path root=temp.toRealPath(),jar=largeOwnJar(root);String previous=System.getProperty("catalina.base");System.setProperty("catalina.base",root.toString());
    try {
      var ref=Map.<String,Object>of("path",jar.toString(),"sha256","a".repeat(64));assertThrows(Refused.class,()->BoundaryPolicy.readFile(ref,root,System.currentTimeMillis()+30000));
      Path real=root.resolve("real.jar");Files.move(jar,real);Files.createSymbolicLink(jar,real);
      assertThrows(Refused.class,()->BoundaryPolicy.readFile(Map.of("path",jar.toString(),"sha256",Jcs.digest(Files.readAllBytes(real))),root,System.currentTimeMillis()+30000));
    }finally{if(previous==null)System.clearProperty("catalina.base");else System.setProperty("catalina.base",previous);}
  }

  @Test void binary32MiBBoundRefusesBeforeDescriptorDiscovery()throws Exception {
    Path root=temp.toRealPath(),jar=largeOwnJar(root);String previous=System.getProperty("catalina.base");System.setProperty("catalina.base",root.toString());
    try {
      try(var file=new java.io.RandomAccessFile(jar.toFile(),"rw")){file.setLength(33_554_433L);}
      assertThrows(Refused.class,()->BoundaryPolicy.readFile(Map.of("path",jar.toString(),"sha256","a".repeat(64)),root,System.currentTimeMillis()+30000));
    }finally{if(previous==null)System.clearProperty("catalina.base");else System.setProperty("catalina.base",previous);}
  }

  @Test void originalStageThreadCannotBeHandedOffBeforeEmission()throws Exception {
    // No engine is substituted: this unit exercises only the mandatory pure thread identity guard.
    var token=new RuntimeDefinitionObservation.StageToken(null,null,null,null,null);
    assertDoesNotThrow(token::ownerThread);
    java.util.concurrent.CompletableFuture.runAsync(()->assertThrows(Refused.class,token::ownerThread)).get();
    assertDoesNotThrow(token::ownerThread);
  }

}

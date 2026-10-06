package br.com.maezo.workload;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import br.com.maezo.human.Jcs;

/** Immutable technical values only. Configuration is an expectation, never a measured fact. */
final class NativeMeasurementConfiguration {
  static final String FILE="provider-native-independent-measurement-config.json";
  static final String SCHEMA="provider-native-independent-measurement-schema-v1.json";
  static final String SCHEMA_SHA="d55733ba7703f39c7c71f13da0eb20f14825cbff03c1106894aae610423cebfe";
  private static final Map<String,Object> CONTRACT=contract();
  private final Map<String,Object> document;
  private final byte[] raw;
  private final String digest;
  private final long initialWall,initialNano,budget;
  private long lastWall,lastNano;
  private NativeMeasurementConfiguration(byte[] bytes,String digest,Map<String,Object> boundary) {
    initialWall=System.currentTimeMillis();initialNano=System.nanoTime();lastWall=initialWall;lastNano=initialNano;
    require(bytes.length>0 && bytes.length<=65536 && Jcs.digest(bytes).equals(digest));
    try {StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
        .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes));}
    catch(java.nio.charset.CharacterCodingException failure){throw Refused.unavailable();}
    document=Collections.unmodifiableMap(new TreeMap<>(Json.parse(bytes)));validate("Config",document);relations(document);
    require(environment().equals(boundary.get("environment")) && tenant().equals(boundary.get("tenant"))
        && engineName().equals(boundary.get("engine_name")));
    Instant issued=Instant.parse(issuedAt()),deadline=Instant.parse(originalDeadline());
    require(!issued.isAfter(Instant.ofEpochMilli(initialWall)) && issued.isBefore(deadline)
        && Duration.between(issued,deadline).compareTo(Duration.ofMinutes(15))<=0
        && !deadline.isAfter(Instant.ofEpochSecond(Json.number(boundary,"expires_at"))));
    long remaining=deadline.toEpochMilli()-initialWall;
    require(remaining>0 && remaining<=Long.MAX_VALUE/1000000L);budget=Math.multiplyExact(remaining,1000000L);
    this.raw=bytes.clone();this.digest=digest;timeOnly();
  }
  static NativeMeasurementConfiguration load(Map<String,Object> ref,Map<String,Object> boundary) {
    Path path=Path.of(Json.string(ref,"path"));require(path.getFileName().toString().equals(FILE));
    String digest=Json.token(ref,"sha256");
    return new NativeMeasurementConfiguration(RuntimeObservationAdmission.readProtected(path,digest,65536),digest,boundary);
  }
  private static void relations(Map<String,Object> d) {
    String nonce=Json.string(d,"session_nonce");
    require(Json.string(d,"socket_path").equals("/run/maezo-native-qualify/"+nonce+"/root.sock")
        && Json.string(d,"outcome_socket_path").equals("/run/maezo-native-qualify/"+nonce+"/outcome.sock")
        && Json.number(d,"root_peer_uid")!=Json.number(d,"jvm_uid")
        && SCHEMA_SHA.equals(d.get("protocol_schema_sha256")));
    Path root=Path.of(Json.string(d,"root_evidence_directory"));
    require(root.isAbsolute() && root.equals(root.normalize()) && !root.toString().contains("//")
        && root.getFileName().toString().equals("measurement-"+nonce));
  }
  String digest(){return digest;}
  String bootUuid(){return RuntimeObservationAdmission.BOOT;}
  byte[] rawConfiguration(){return raw.clone();}
  synchronized void timeOnly() {
    long wall=System.currentTimeMillis(),nano=System.nanoTime();
    require(!Thread.currentThread().isInterrupted() && wall>=lastWall && nano>=lastNano
        && nano-initialNano<budget && wall<Instant.parse(originalDeadline()).toEpochMilli()
        && Math.abs((wall-initialWall)-(nano-initialNano)/1000000L)<=5000);
    lastWall=wall;lastNano=nano;
  }
  void current(){timeOnly();NativeMeasurementBinding.current(this);timeOnly();}
  String schema() { return Json.string(document,"schema"); }
  String protocol() { return Json.string(document,"protocol"); }
  String environment() { return Json.string(document,"environment"); }
  String tenant() { return Json.string(document,"tenant"); }
  String engineName() { return Json.string(document,"engine_name"); }
  String candidateSha() { return Json.string(document,"candidate_sha"); }
  String expectedImageId() { return Json.string(document,"expected_image_id"); }
  String supportJarSha256() { return Json.string(document,"support_jar_sha256"); }
  String instrumentClassSha256() { return Json.string(document,"instrument_class_sha256"); }
  String instrumentClass() { return Json.string(document,"instrument_class"); }
  String instrumentJarRelativePath() { return Json.string(document,"instrument_jar_relative_path"); }
  String protocolSchemaSha256() { return Json.string(document,"protocol_schema_sha256"); }
  String observationSchemaSha256() { return Json.string(document,"observation_schema_sha256"); }
  String observationAdmissionSha256() { return Json.string(document,"observation_admission_sha256"); }
  String rootPgPrebootWitnessSha256() { return Json.string(document,"root_pg_preboot_witness_sha256"); }
  String socketPath() { return Json.string(document,"socket_path"); }
  String rootPeerUser() { return Json.string(document,"root_peer_user"); }
  String rootPeerGroup() { return Json.string(document,"root_peer_group"); }
  long rootPeerUid() { return Json.number(document,"root_peer_uid"); }
  long rootPeerGid() { return Json.number(document,"root_peer_gid"); }
  long jvmUid() { return Json.number(document,"jvm_uid"); }
  long jvmGid() { return Json.number(document,"jvm_gid"); }
  String issuedAt() { return Json.string(document,"issued_at"); }
  String originalDeadline() { return Json.string(document,"original_deadline"); }
  String sessionNonce() { return Json.string(document,"session_nonce"); }
  String requestSha256() { return Json.string(document,"request_sha256"); }
  long maximumStageSequence() { return Json.number(document,"maximum_stage_sequence"); }
  long maximumEndpoints() { return Json.number(document,"maximum_endpoints"); }
  long maximumTotalFrames() { return Json.number(document,"maximum_total_frames"); }
  long maximumFrameBytes() { return Json.number(document,"maximum_frame_bytes"); }
  long maximumTotalBytes() { return Json.number(document,"maximum_total_bytes"); }
  String rootEvidenceDirectory() { return Json.string(document,"root_evidence_directory"); }
  long maximumConnections() { return Json.number(document,"maximum_connections"); }
  long maximumTerminalRecords() { return Json.number(document,"maximum_terminal_records"); }
  String outcomeSocketPath() { return Json.string(document,"outcome_socket_path"); }
  String primaryChannelId() { return Json.string(document,"primary_channel_id"); }
  String outcomeChannelId() { return Json.string(document,"outcome_channel_id"); }

  private static Map<String,Object> contract() {
    try(var stream=NativeMeasurementConfiguration.class.getResourceAsStream("/"+SCHEMA)) {
      require(stream!=null);byte[] raw=stream.readNBytes(1048577);require(raw.length<=1048576 && Jcs.digest(raw).equals(SCHEMA_SHA));
      return Json.parse(raw);
    }catch(IOException failure){throw Refused.unavailable();}
  }
  static void validate(String definition,Object value) {
    node(Json.object(Json.object(CONTRACT.get("$defs")).get(definition)),value);
  }
  private static void node(Map<String,Object> rule,Object value) {
    if(rule.containsKey("$ref")){String ref=Json.string(rule,"$ref");require(ref.startsWith("#/$defs/"));validate(ref.substring(8),value);return;}
    if(rule.containsKey("allOf"))for(Object child:Json.list(rule.get("allOf")))node(Json.object(child),value);
    if(rule.containsKey("oneOf")) {
      int count=0;for(Object child:Json.list(rule.get("oneOf")))try{node(Json.object(child),value);count++;}catch(Refused mismatch){}
      require(count==1);
    }
    if(rule.containsKey("const"))require(Objects.equals(rule.get("const"),value));
    if(rule.containsKey("enum"))require(Json.list(rule.get("enum")).contains(value));
    if(rule.containsKey("type"))switch(Json.string(rule,"type")) {
      case "object": {
        var map=Json.object(value);var props=Json.object(rule.get("properties"));
        require(map.keySet().containsAll(Json.list(rule.get("required"))));
        if(Boolean.FALSE.equals(rule.get("additionalProperties")))require(props.keySet().containsAll(map.keySet()));
        for(var entry:map.entrySet())if(props.containsKey(entry.getKey()))node(Json.object(props.get(entry.getKey())),entry.getValue());
        break;
      }
      case "array": {
        var list=Json.list(value);if(rule.containsKey("minItems"))require(list.size()>=Json.number(rule,"minItems"));
        if(rule.containsKey("maxItems"))require(list.size()<=Json.number(rule,"maxItems"));
        if(Boolean.TRUE.equals(rule.get("uniqueItems")))require(new HashSet<>(list).size()==list.size());
        if(rule.containsKey("items"))for(Object item:list)node(Json.object(rule.get("items")),item);
        if(rule.containsKey("contains")) {
          int count=0;for(Object item:list)try{node(Json.object(rule.get("contains")),item);count++;}catch(Refused mismatch){}
          require(count>=(rule.containsKey("minContains")?Json.number(rule,"minContains"):1));
          if(rule.containsKey("maxContains"))require(count<=Json.number(rule,"maxContains"));
        }break;
      }
      case "string": {
        require(value instanceof String);String text=(String)value;
        if(rule.containsKey("pattern"))require(text.matches(Json.string(rule,"pattern")));
        if(rule.containsKey("minLength"))require(text.length()>=Json.number(rule,"minLength"));
        if(rule.containsKey("maxLength"))require(text.length()<=Json.number(rule,"maxLength"));
        if("date-time".equals(rule.get("format")))try{Instant.parse(text);}catch(java.time.DateTimeException invalid){throw Refused.unavailable();}
        break;
      }
      case "integer": {
        require(value instanceof Long || value instanceof Integer);long number=((Number)value).longValue();
        if(rule.containsKey("minimum"))require(number>=Json.number(rule,"minimum"));
        if(rule.containsKey("maximum"))require(number<=Json.number(rule,"maximum"));break;
      }
      case "boolean":require(value instanceof Boolean);break;
      case "null":require(value==null);break;
      default:throw Refused.unavailable();
    }
  }
  private static void require(boolean value){RuntimeObservationAdmission.require(value);}
}

// ADR-0049 D7 investigation: observer failures are not application or readiness verdicts.
import java.io.*;
import java.lang.instrument.Instrumentation;
import java.nio.file.*;
import java.nio.file.attribute.*;
import java.security.MessageDigest;
import java.time.*;
import java.util.*;
import jdk.jfr.*;
import jdk.jfr.consumer.*;

public final class StageProjection {
  static final long MAX_BYTES=32L*1024*1024, MAX_EVENTS=100_000, MAX_OUTPUT=256*1024;
  static final Path AREA=Path.of("/run/maezo-startup-observation");
  static final Path SETTINGS=Path.of("/run/maezo-startup-observation-input/startup.jfc");
  static final Set<String> EVENTS=Set.of("jdk.ExecutionSample","jdk.NativeMethodSample","jdk.ThreadPark","jdk.JavaMonitorWait","jdk.JavaMonitorEnter","jdk.ThreadCPULoad","jdk.DataLoss","maezo.D7ClockAnchor");
  static final Map<String,String> STAGES=Map.ofEntries(
    Map.entry("org.apache.catalina.startup.HostConfig#migrateLegacyApps","MIGRATION"),
    Map.entry("org.apache.catalina.startup.HostConfig#migrateLegacyApp","MIGRATION"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployDescriptor","DESCRIPTOR_DEPLOY"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployDescriptors","DESCRIPTOR_DEPLOY"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployWAR","WAR_DEPLOY"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployWARs","WAR_DEPLOY"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployDirectory","DIRECTORY_DEPLOY"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployDirectories","DIRECTORY_DEPLOY"),
    Map.entry("org.apache.catalina.startup.HostConfig#deployApps","DEPLOY_DISPATCH"),
    Map.entry("org.apache.catalina.core.StandardEngine#startInternal","ENGINE_CONTAINER_START"),
    Map.entry("org.apache.catalina.core.ContainerBase#startInternal","CONTAINER_START"),
    Map.entry("org.apache.catalina.core.StandardServer#startInternal","SERVER_START"),
    Map.entry("org.apache.catalina.startup.Catalina#load","CATALINA_LOAD"),
    Map.entry("org.apache.catalina.startup.Catalina#start","CATALINA_START"),
    Map.entry("org.apache.coyote.AbstractProtocol#start","CONNECTOR_START"));
  @Name("maezo.D7ClockAnchor") @StackTrace(false)
  public static final class Anchor extends Event {public long beforeNanos, wallMillis, afterNanos; public int sequence;}
  // No configuration path or arbitrary options are accepted by the agent.
  public static void premain(String ignored, Instrumentation instrumentation) {
    try {Thread worker=new Thread(()->observe(AREA,SETTINGS),"maezo-d7-observer");worker.setDaemon(true);worker.start();}
    catch(Throwable ignoredFailure) { /* Observer failure never calls exit or rethrows into main. */ }
  }
  static void status(Path area,String code) {
    try {Files.writeString(area.resolve("status"),code+"\n",StandardOpenOption.CREATE_NEW);}
    catch(Throwable ignoredFailure) { }
  }
  static void observe(Path area,Path settings) {
    try (Recording r=new Recording(Configuration.create(settings))) {
      r.setName("MaezoD7Startup");r.setToDisk(true);r.setMaxSize(8L*1024*1024);r.setDumpOnExit(false);
      r.start();String project=System.getProperty("maezo.d7.project","");require(project.matches("d7-[a-z0-9-]{8,64}"));status(area,"RECORDING_STARTED|"+ProcessHandle.current().pid()+"|"+project);
      long end=System.nanoTime()+300_000_000_000L;int sequence=0;
      while(r.getState()==RecordingState.RUNNING && System.nanoTime()<end) {
        Anchor a=new Anchor();a.begin();a.beforeNanos=System.nanoTime();a.wallMillis=System.currentTimeMillis();a.afterNanos=System.nanoTime();a.sequence=sequence++;a.end();a.commit();Thread.sleep(1000);
      }
    } catch(Throwable ignoredFailure) {status(area,"RECORDING_UNAVAILABLE");}
  }
  static void require(boolean value) {if(!value)throw new IllegalArgumentException();}
  static String stage(List<String> frames) {for(String frame:frames){String s=STAGES.get(frame);if(s!=null)return s;}return "UNKNOWN";}
  static String eventCode(String name) {return switch(name){case "jdk.ExecutionSample"->"EXECUTION_SAMPLE";case "jdk.NativeMethodSample"->"NATIVE_SAMPLE";case "jdk.ThreadPark"->"PARK_WAIT";case "jdk.JavaMonitorWait"->"MONITOR_WAIT";case "jdk.JavaMonitorEnter"->"MONITOR_ENTER_WAIT";case "jdk.ThreadCPULoad"->"THREAD_CPU_LOAD";default->throw new IllegalArgumentException();};}
  static String role(RecordedEvent e,String name) {RecordedThread t=e.hasField("sampledThread")?e.getThread("sampledThread"):e.getThread();return t!=null&&"main".equals(t.getJavaName())?"MAIN":"OTHER_OR_UNAVAILABLE";}
  static final class Row {
    long count,min=Long.MAX_VALUE,max,loadMin=1000,loadMax;boolean duration,cpu;
    void add(long ns,Long load) {count++;if(ns>=0){duration=true;min=Math.min(min,ns);max=Math.max(max,ns);}if(load!=null){cpu=true;loadMin=Math.min(loadMin,load);loadMax=Math.max(loadMax,load);}}
  }
  static String project(Path input) throws Exception {
    BasicFileAttributes before=Files.readAttributes(input,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
    require(before.isRegularFile()&&!Files.isSymbolicLink(input)&&before.size()>0&&before.size()<=MAX_BYTES);
    long end=System.nanoTime()+14_000_000_000L, count=0,unknown=0,truncated=0,loss=0,first=Long.MAX_VALUE,last=0;
    TreeMap<String,Row> rows=new TreeMap<>();List<String> anchors=new ArrayList<>();
    try(RecordingFile file=new RecordingFile(input)) {
      while(file.hasMoreEvents()) {
        require(++count<=MAX_EVENTS&&System.nanoTime()<end);RecordedEvent e=file.readEvent();String name=e.getEventType().getName();
        if(!EVENTS.contains(name)){unknown++;continue;}
        long stamp=e.getStartTime().toEpochMilli();require(stamp>=0&&stamp<4102444800000L);first=Math.min(first,stamp);last=Math.max(last,stamp);
        if(name.equals("jdk.DataLoss")){loss++;continue;}
        if(name.equals("maezo.D7ClockAnchor")){
          long a=e.getLong("beforeNanos"),b=e.getLong("afterNanos"),wall=e.getLong("wallMillis");int seq=e.getInt("sequence");
          require(a>=0&&b>=a&&b-a<=1_000_000_000L&&wall>=0&&wall<4102444800000L&&seq>=0&&seq<=300&&anchors.size()<301);
          anchors.add("["+stamp+","+a+","+wall+","+b+","+seq+"]");continue;
        }
        RecordedStackTrace stack=e.getStackTrace();List<String> frames=new ArrayList<>();
        if(stack!=null){require(stack.getFrames().size()<=128);if(stack.isTruncated())truncated++;for(RecordedFrame f:stack.getFrames())frames.add(f.getMethod().getType().getName()+"#"+f.getMethod().getName());}
        String s=stage(frames),kind=eventCode(name);long ns=-1;Long load=null;
        if(kind.endsWith("WAIT")){ns=e.getDuration().toNanos();require(ns>=0&&ns<=600_000_000_000L);}
        if(kind.equals("THREAD_CPU_LOAD")){double value=e.getFloat("user")+e.getFloat("system");require(Double.isFinite(value)&&value>=0&&value<=1.001);load=Math.min(1000L,Math.round(value*1000));}
        String key=(stamp/1000*1000)+"|"+s+"|"+kind+"|"+role(e,name);require(rows.containsKey(key)||rows.size()<600);rows.computeIfAbsent(key,k->new Row()).add(ns,load);
      }
    }
    BasicFileAttributes after=Files.readAttributes(input,BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
    require(Objects.equals(before.fileKey(),after.fileKey())&&before.size()==after.size()&&before.lastModifiedTime().equals(after.lastModifiedTime()));
    StringBuilder out=new StringBuilder("{\"schema\":\"d7-startup-stage.v1\",\"status\":\"PROJECTED\",\"cause\":\"UNDETERMINED\",\"readiness\":false,\"complete_history\":false,\"clock\":\"JFR_EVENT_TIME_NO_HOST_BRIDGE\",\"sampling\":\"OBSERVED_STACKS_NOT_PHASE_DURATIONS\",\"aggregation\":\"NO_WAIT_SUM_OR_CPU_TO_STAGE_JOIN\",\"records\":").append(count).append(",\"unknown_events\":").append(unknown).append(",\"truncated_stacks\":").append(truncated).append(",\"data_loss_events\":").append(loss).append(",\"first_event_epoch_ms\":").append(first==Long.MAX_VALUE?"null":first).append(",\"last_event_epoch_ms\":").append(first==Long.MAX_VALUE?"null":last).append(",\"jvm_anchor_columns\":[\"jfr_epoch_ms\",\"before_nanos\",\"wall_ms\",\"after_nanos\",\"sequence\"],\"jvm_anchors\":[").append(String.join(",",anchors)).append("],\"rows\":[");
    boolean comma=false;for(var entry:rows.entrySet()){if(comma)out.append(',');comma=true;String[] k=entry.getKey().split("\\|");Row r=entry.getValue();out.append("{\"epoch_bucket_ms\":").append(k[0]).append(",\"stage\":\"").append(k[1]).append("\",\"observation\":\"").append(k[2]).append("\",\"thread_role\":\"").append(k[3]).append("\",\"count\":").append(r.count).append(",\"duration_ns_min\":").append(r.duration?r.min:"null").append(",\"duration_ns_max\":").append(r.duration?r.max:"null").append(",\"cpu_permille_min\":").append(r.cpu?r.loadMin:"null").append(",\"cpu_permille_max\":").append(r.cpu?r.loadMax:"null").append('}');}
    out.append("]}\n");require(out.length()<=MAX_OUTPUT);return out.toString();
  }
  public static void main(String[] args) {
    if(args.length!=2||!args[0].equals("--private-recording")){System.out.println("INVOCATION_REFUSED");System.exit(2);}
    try{String result=project(Path.of(args[1]));System.out.print(result);}
    catch(Throwable failure){System.out.println("{\"schema\":\"d7-startup-stage.v1\",\"status\":\"UNAVAILABLE\",\"cause\":\"UNDETERMINED\",\"readiness\":false}");System.exit(4);}
  }
}

package br.com.maezo.workload;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.apache.catalina.core.StandardContext;
import org.w3c.dom.Document;

/** Pinned installed byte inventory, read before provider/class initialization. */
final class StartupAdmission {
  static final Set<String> APPS=Set.of("ROOT","camunda","docs","engine-rest","examples",
      "host-manager","maezo-human","manager","webapp");
  static final Set<String> PATHS=Set.of("","/camunda","/docs","/engine-rest","/examples",
      "/host-manager","/maezo-human","/manager","/webapp");
  private final Path root;
  private final Map<String,String> vendor;
  private final Set<String> pinned;
  private final Map<String,Object> observation;
  private final List<Map<String,Object>> admittedFiles;
  StartupAdmission(StartupCustody custody) {
    root=custody.root;pinned=new HashSet<>();admittedFiles=custody.files;
    var candidates=custody.files.stream().filter(f->Path.of(Json.string(f,"path")).getFileName().toString().equals(RuntimeObservationAdmission.ADMISSION_FILE)).toList();
    if(candidates.isEmpty())observation=null;
    else {
      require(candidates.size()==1);var ref=candidates.get(0);
      observation=Json.parse(RuntimeObservationAdmission.readProtected(Path.of(Json.string(ref,"path")),Json.string(ref,"sha256"),65536));
      RuntimeObservationAdmission.validate("ObservationAdmission",observation);
      var boundary=Json.parse(BoundaryPolicy.read(custody.policyPath,custody.policyDigest));
      require(observation.get("engine_name").equals(boundary.get("engine_name"))
          && Json.object(observation.get("identity")).get("tenant").equals(boundary.get("tenant"))
          && Json.object(observation.get("identity")).get("environment").equals(boundary.get("environment")));
    }
    custody.files.forEach(f->pinned.add(Json.string(f,"path")));
    vendor=inventory("vendor");current();
    NativeMeasurementBinding.startup(custody,Json.parse(BoundaryPolicy.read(custody.policyPath,custody.policyDigest)));
  }
  void current() {
    try {
      var apps=new HashSet<String>();
      try(var stream=Files.list(root.resolve("webapps"))) {
        for(var path:stream.toList()) {
          require(Files.isDirectory(path,LinkOption.NOFOLLOW_LINKS));apps.add(path.getFileName().toString());
        }
      }
      require(apps.equals(APPS));
      if(observation!=null)currentObservation();
      for(var entry:inventory("descriptors").entrySet()) {
        requirePinned(entry.getKey());require(entry.getValue().equals(digest(root.resolve(entry.getKey()))));
      }
      verifyOwnArtifacts();
      var actual=new TreeSet<String>();
      for(String directory:List.of("lib","webapps"))try(var stream=Files.walk(root.resolve(directory))) {
        for(var path:stream.toList()) {
          require(!Files.isSymbolicLink(path));
          if(!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS))continue;
          String relative=root.relativize(path).toString();
          if(inventoried(relative)) {
            actual.add(relative);require(vendor.containsKey(relative));
            require(vendor.get(relative).equals(digest(path)));
          }
          String name=path.getFileName().toString();
          require(!Set.of("camunda.cfg.xml","activiti.cfg.xml","activiti-context.xml","processes.xml").contains(name));
          require(!relative.endsWith("WEB-INF/classes/META-INF/services/jakarta.servlet.ServletContainerInitializer")
              && !relative.endsWith("WEB-INF/classes/META-INF/services/javax.servlet.ServletContainerInitializer"));
          require(!relative.endsWith("WEB-INF/tomcat-web.xml"));
        }
      }
      require(actual.equals(vendor.keySet()));
      requirePinned("conf/context.xml");
      var defaults=parse(root.resolve("conf/context.xml")).getDocumentElement();
      require("Context".equals(defaults.getTagName()) && ".*".equals(defaults.getAttribute("containerSciFilter")));
      for(String app:APPS) {
        String descriptor="webapps/"+app+"/WEB-INF/web.xml";requirePinned(descriptor);
        sealed(parse(root.resolve(descriptor)));
        Path context=root.resolve("webapps/"+app+"/META-INF/context.xml");
        if(Files.exists(context))require(!parse(context).getDocumentElement().hasAttribute("containerSciFilter"));
      }
      // No host-specific context override may run before our default admission policy.
      Path hostConfig=root.resolve("conf/Catalina");
      if(Files.exists(hostConfig))try(var stream=Files.walk(hostConfig)) {
        require(stream.noneMatch(p->Files.isRegularFile(p) && p.toString().endsWith(".xml")));
      }
    } catch(IOException e) {throw Refused.unavailable();}
  }
  private void currentObservation() {
    var ref=RuntimeObservationAdmission.named(admittedFiles,RuntimeObservationAdmission.ADMISSION_FILE);
    require(observation.equals(Json.parse(RuntimeObservationAdmission.readProtected(Path.of(Json.string(ref,"path")),Json.string(ref,"sha256"),65536))));
    var now=java.time.Instant.now();require(!now.isBefore(java.time.Instant.parse(Json.string(observation,"issued_at")))
        && now.isBefore(java.time.Instant.parse(Json.string(observation,"original_deadline"))));
    for(var binding:Map.of("native_jar_sha256","lib/maezo-human-command.jar","support_jar_sha256","lib/provider-auth-test-support.jar",
        "rest_spi_jar_sha256","webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar","descriptor_sha256","conf/bpm-platform.xml").entrySet()) {
      String path=root.resolve(binding.getValue()).toString(),sha=Json.string(observation,binding.getKey());
      require(admittedFiles.stream().filter(f->path.equals(f.get("path")) && sha.equals(f.get("sha256"))).count()==1
          && sha.equals(RuntimeDefinitionObservation.binaryDigest(Path.of(path))));
    }
  }
  private Map<String,String> inventory(String kind) {
    if(observation==null)return readInventory("/secured-startup-"+kind+".sha256");
    currentObservation();String variant=Json.string(observation,"startup_variant");
    require(Set.of("phase-a","phase-b").contains(variant));
    Path jar=root.resolve(kind.equals("descriptors")?"lib/provider-auth-test-support.jar":"lib/maezo-human-command.jar");
    String entry="provider-native/"+variant+"/secured-startup-"+kind+".sha256";
    String key=kind.equals("descriptors")?"startup_descriptor_inventory_sha256":"startup_vendor_inventory_sha256";
    require(RuntimeDefinitionObservation.jarResource(jar,entry).equals(observation.get(key)));
    try {
      var resources=Collections.list(StartupAdmission.class.getClassLoader().getResources(entry));require(resources.size()==1);
      var connection=(java.net.JarURLConnection)resources.get(0).openConnection();
      require(Path.of(connection.getJarFileURL().toURI()).equals(jar));
      try(var file=new java.util.jar.JarFile(jar.toFile());var stream=file.getInputStream(file.getJarEntry(entry))) {
        return parseInventory(stream.readNBytes(Json.LIMIT+1));
      }
    }catch(Exception e){throw Refused.unavailable();}
  }
  /** AFTER_INIT/BEFORE_START: ContextConfig.init has already parsed context defaults. */
  void beforeContextStart(StandardContext context) {
    require(PATHS.contains(context.getPath()) && ".*".equals(context.getContainerSciFilter()));
    String app=context.getPath().isEmpty()?"ROOT":context.getPath().substring(1);
    Path actual=Path.of(context.getDocBase());
    if(!actual.isAbsolute())actual=root.resolve("webapps").resolve(actual);
    require(actual.normalize().equals(root.resolve("webapps").resolve(app)));
    sealed(parse(actual.resolve("WEB-INF/web.xml")));
    Path service=actual.resolve("WEB-INF/classes/META-INF/services");
    require(!Files.exists(service.resolve("jakarta.servlet.ServletContainerInitializer"))
        && !Files.exists(service.resolve("javax.servlet.ServletContainerInitializer")));
  }
  static void sealed(Document xml) {
    var element=xml.getDocumentElement();
    require("true".equals(element.getAttribute("metadata-complete")));
    var ordering=element.getElementsByTagName("absolute-ordering");
    require(ordering.getLength()==1 && ordering.item(0).getParentNode()==element);
    for(var child=ordering.item(0).getFirstChild();child!=null;child=child.getNextSibling())
      require(child.getNodeType()!=org.w3c.dom.Node.ELEMENT_NODE && child.getTextContent().isBlank());
  }
  private void verifyOwnArtifacts() {
    try {
      Path common=root.resolve("lib/maezo-human-command.jar");
      for(Class<?> type:List.of(StartupAdmission.class,SecuredBpmPlatformBootstrap.class,
          WorkloadPlugin.class,BoundaryFilter.class)) {
        require(type.getClassLoader()==StartupAdmission.class.getClassLoader());
        require(Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(common));
      }
      for(String relative:List.of("lib/maezo-human-command.jar","webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar"))
        try(var jar=new java.util.jar.JarFile(root.resolve(relative).toFile())) {
          var entries=jar.entries();
          while(entries.hasMoreElements()) {
            String name=entries.nextElement().getName();
            require(!name.equals("META-INF/services/jakarta.servlet.ServletContainerInitializer")
                && !name.equals("META-INF/services/javax.servlet.ServletContainerInitializer")
                && !Set.of("camunda.cfg.xml","activiti.cfg.xml","activiti-context.xml","META-INF/processes.xml").contains(name));
            if(relative.startsWith("lib/"))require(!name.startsWith("br/com/maezo/workload/SecuredFetchAndLockContextListener")
                && !name.equals("br/com/maezo/workload/CertificateAuthenticationProvider.class"));
          }
        }
    } catch(Exception e) {throw Refused.unavailable();}
  }
  private void requirePinned(String relative) {require(pinned.contains(root.resolve(relative).toString()));}
  private static boolean inventoried(String relative) {
    return (relative.endsWith(".jar") && !Set.of("lib/maezo-human-command.jar",
        "webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar").contains(relative))
        || relative.contains("/WEB-INF/classes/") || relative.endsWith("/META-INF/context.xml");
  }
  private static Map<String,String> readInventory(String resource) {
    try(var bytes=StartupAdmission.class.getResourceAsStream(resource)) {
      require(bytes!=null);return parseInventory(bytes.readNBytes(Json.LIMIT+1));
    } catch(IOException e) {throw Refused.unavailable();}
  }
  private static Map<String,String> parseInventory(byte[] bytes) {
      require(bytes.length>0 && bytes.length<=Json.LIMIT);var result=new TreeMap<String,String>();
      for(String line:new String(bytes,StandardCharsets.UTF_8).split("\n")) {
        require(line.matches("[0-9a-f]{64} [A-Za-z0-9_./$@-]+") && !line.contains(".."));
        require(result.put(line.substring(65),line.substring(0,64))==null);
      }
      require(!result.isEmpty());return Map.copyOf(result);
  }
  private static String digest(Path path) {
    try(var stream=Files.newInputStream(path)) {
      var hash=MessageDigest.getInstance("SHA-256");byte[] buffer=new byte[65536];int n;
      while((n=stream.read(buffer))!=-1)hash.update(buffer,0,n);
      return HexFormat.of().formatHex(hash.digest());
    } catch(Exception e) {throw Refused.unavailable();}
  }
  static Document parse(Path path) {
    try {
      var factory=DocumentBuilderFactory.newInstance();
      factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl",true);
      factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD,"");factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA,"");
      return factory.newDocumentBuilder().parse(path.toFile());
    } catch(Exception e) {throw Refused.unavailable();}
  }
  static void require(boolean value) {if(!value)throw Refused.unavailable();}
}

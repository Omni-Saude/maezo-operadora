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
  StartupAdmission(StartupCustody custody) {
    root=custody.root;pinned=new HashSet<>();
    custody.files.forEach(f->pinned.add(Json.string(f,"path")));
    vendor=readInventory("/secured-startup-vendor.sha256");current();
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
      for(var entry:readInventory("/secured-startup-descriptors.sha256").entrySet()) {
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
      require(bytes!=null);var result=new TreeMap<String,String>();
      for(String line:new String(bytes.readAllBytes(),StandardCharsets.UTF_8).split("\n")) {
        require(line.matches("[0-9a-f]{64} [A-Za-z0-9_./$@-]+") && !line.contains(".."));
        require(result.put(line.substring(65),line.substring(0,64))==null);
      }
      require(!result.isEmpty());return Map.copyOf(result);
    } catch(IOException e) {throw Refused.unavailable();}
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

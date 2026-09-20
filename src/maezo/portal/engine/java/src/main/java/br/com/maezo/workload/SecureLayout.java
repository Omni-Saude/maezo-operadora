package br.com.maezo.workload;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.*;

/** Checks the actual mounted Tomcat layout, not an unused YAML switch or descriptor comment. */
final class SecureLayout {
  static void verifyV2(BoundaryPolicyV2 policy){
    String base=System.getenv("CATALINA_BASE");if(base==null)throw Refused.unavailable();Path root=Path.of(base);
    verify(policy.transport,root);
    String path=root.resolve("webapps/maezo-workload/WEB-INF/web.xml").toString();
    if(policy.transport.files.stream().noneMatch(f->path.equals(f.get("path"))))throw Refused.unavailable();
    var xml=parse(Path.of(path));
    if(!"true".equals(xml.getDocumentElement().getAttribute("metadata-complete")) || xml.getElementsByTagName("servlet").getLength()!=1
        || !"br.com.maezo.workload.WorkloadV2Servlet".equals(text(xml.getDocumentElement(),"servlet-class"))
        || !"/v2/*".equals(text(xml.getDocumentElement(),"url-pattern")))throw Refused.unavailable();
    var bpm=parse(root.resolve("conf/bpm-platform.xml"));
    if(!hasPair(bpm.getDocumentElement(),"property","name","databaseSchemaUpdate",null,"false"))throw Refused.unavailable();
  }
  static void verify(BoundaryPolicy policy) {
    String base=System.getenv("CATALINA_BASE");
    if (base==null) throw Refused.unavailable();
    verify(policy,Path.of(base));
  }
  static void verify(BoundaryPolicy policy,Path root) {
    verifyArtifacts(policy.files,policy.port,root);
  }
  /** Custody/transport only: this never admits the boundary document's peers or capabilities. */
  static void verifyArtifacts(List<Map<String,Object>> files,int port,Path root) {
    Set<String> pinned=new HashSet<>();
    files.forEach(f->pinned.add(Json.string(f,"path")));
    List<String> required=List.of("conf/server.xml","conf/web.xml","conf/bpm-platform.xml",
        "webapps/engine-rest/WEB-INF/web.xml","webapps/maezo-human/WEB-INF/web.xml","webapps/camunda/WEB-INF/web.xml");
    for (String relative:required) if (!pinned.contains(root.resolve(relative).toString())) throw Refused.unavailable();
    Document server=parse(root.resolve("conf/server.xml"));
    var connectors=server.getElementsByTagName("Connector");
    if (connectors.getLength()!=1) throw Refused.unavailable();
    Element connector=(Element)connectors.item(0);
    if (!"true".equals(connector.getAttribute("SSLEnabled")) || !"https".equals(connector.getAttribute("scheme"))
        || !"true".equals(connector.getAttribute("secure")) || !Integer.toString(port).equals(connector.getAttribute("port"))
        || !"false".equals(connector.getAttribute("allowTrace"))
        || connector.hasAttribute("proxyPort") || connector.hasAttribute("proxyName")) throw Refused.unavailable();
    var engines=server.getElementsByTagName("Engine");
    if(engines.getLength()!=1)throw Refused.unavailable();
    Element firstValve=null;int traceValves=0;
    var children=engines.item(0).getChildNodes();
    for(int i=0;i<children.getLength();i++)if(children.item(i) instanceof Element e && "Valve".equals(e.getTagName())) {
      if(firstValve==null)firstValve=e;
      if("br.com.maezo.workload.TraceRefusalValve".equals(e.getAttribute("className")))traceValves++;
    }
    if(firstValve==null || traceValves!=1 || firstValve.getAttributes().getLength()!=1
        || !"br.com.maezo.workload.TraceRefusalValve".equals(firstValve.getAttribute("className")))throw Refused.unavailable();
    var ssl=connector.getElementsByTagName("SSLHostConfig");
    if (ssl.getLength()!=1 || !"required".equals(((Element)ssl.item(0)).getAttribute("certificateVerification"))) throw Refused.unavailable();
    Element host=(Element)server.getElementsByTagName("Host").item(0);
    if (host==null || !"false".equals(host.getAttribute("autoDeploy"))) throw Refused.unavailable();
    var global=parse(root.resolve("conf/web.xml"));
    requireFilter(global,"maezo-boundary","br.com.maezo.workload.BoundaryFilter","/*",true);
    var nativeXml=parse(root.resolve("webapps/engine-rest/WEB-INF/web.xml"));
    requireFilter(nativeXml,"maezo-boundary","br.com.maezo.workload.BoundaryFilter","/*",true);
    var nativeMappings=nativeXml.getElementsByTagName("filter-mapping");
    if(nativeMappings.getLength()!=2
        || !"maezo-boundary".equals(text((Element)nativeMappings.item(0),"filter-name"))
        || !"maezo-native-auth".equals(text((Element)nativeMappings.item(1),"filter-name")))throw Refused.unavailable();
    Element filter=requireFilter(nativeXml,"maezo-native-auth","org.cibseven.bpm.engine.rest.security.auth.ProcessEngineAuthenticationFilter","/*",true);
    if (!hasPair(filter,"init-param","param-name","authentication-provider","param-value","br.com.maezo.workload.CertificateAuthenticationProvider")) throw Refused.unavailable();
    var servlets=nativeXml.getElementsByTagName("servlet");int restServlets=0;
    for(int i=0;i<servlets.getLength();i++) {
      Element servlet=(Element)servlets.item(i);
      if(!"Resteasy".equals(text(servlet,"servlet-name")))continue;
      restServlets++;
      if(!"org.jboss.resteasy.plugins.server.servlet.HttpServletDispatcher".equals(text(servlet,"servlet-class"))
          || !"0".equals(text(servlet,"load-on-startup"))
          || !hasPair(servlet,"init-param","param-name","jakarta.ws.rs.Application","param-value",
              "org.cibseven.bpm.engine.rest.impl.application.DefaultApplication"))throw Refused.unavailable();
    }
    if(restServlets!=1)throw Refused.unavailable();
    var legacy=parse(root.resolve("webapps/camunda/WEB-INF/web.xml"));
    if(!"true".equals(legacy.getDocumentElement().getAttribute("metadata-complete"))
        || legacy.getElementsByTagName("absolute-ordering").getLength()!=1
        || legacy.getElementsByTagName("listener").getLength()!=0 || legacy.getElementsByTagName("filter").getLength()!=0
        || !"br.com.maezo.workload.ClosedServlet".equals(text(legacy.getDocumentElement(),"servlet-class"))
        || !"/*".equals(text(legacy.getDocumentElement(),"url-pattern")))throw Refused.unavailable();
    var bpm=parse(root.resolve("conf/bpm-platform.xml"));
    if (!hasPair(bpm.getDocumentElement(),"property","name","authorizationEnabled",null,"true")) throw Refused.unavailable();
    Set<String> plugins=new HashSet<>(); var classes=bpm.getElementsByTagName("class");
    for(int i=0;i<classes.getLength();i++) plugins.add(classes.item(i).getTextContent().strip());
    if (!plugins.containsAll(Set.of("br.com.maezo.human.HumanCommandPlugin","br.com.maezo.workload.WorkloadPlugin"))) throw Refused.unavailable();
  }
  private static Element requireFilter(Document xml,String name,String type,String pattern,boolean allDispatchers) {
    Element found=null; var filters=xml.getElementsByTagName("filter");
    for(int i=0;i<filters.getLength();i++) {
      Element f=(Element)filters.item(i);
      if (name.equals(text(f,"filter-name"))) { if(found!=null || !type.equals(text(f,"filter-class")))throw Refused.unavailable(); found=f; }
    }
    if(found==null)throw Refused.unavailable();
    int mappings=0;var ms=xml.getElementsByTagName("filter-mapping");
    for(int i=0;i<ms.getLength();i++) {
      Element m=(Element)ms.item(i);if(!name.equals(text(m,"filter-name")))continue;
      mappings++;if(!pattern.equals(text(m,"url-pattern")))throw Refused.unavailable();
      Set<String> dispatch=new HashSet<>();var ds=m.getElementsByTagName("dispatcher");
      for(int j=0;j<ds.getLength();j++)dispatch.add(ds.item(j).getTextContent().strip());
      if(allDispatchers&&!dispatch.equals(Set.of("REQUEST","FORWARD","INCLUDE","ERROR","ASYNC")))throw Refused.unavailable();
    }
    if(mappings!=1)throw Refused.unavailable();return found;
  }
  private static String text(Element node,String tag) { var v=node.getElementsByTagName(tag);return v.getLength()==1?v.item(0).getTextContent().strip():""; }
  private static boolean hasPair(Element root,String tag,String keyTag,String key,String valueTag,String value) {
    var list=root.getElementsByTagName(tag);int matches=0;
    for(int i=0;i<list.getLength();i++) {
      Element n=(Element)list.item(i);String actualKey=keyTag.equals("name")?n.getAttribute("name"):text(n,keyTag);
      if(key.equals(actualKey)) {if(!(valueTag==null?n.getTextContent().strip():text(n,valueTag)).equals(value))return false;matches++;}
    }
    return matches==1;
  }
  static Document parse(Path path) {
    try {
      var factory=DocumentBuilderFactory.newInstance();
      factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl",true);
      factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD,"");factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA,"");
      return factory.newDocumentBuilder().parse(path.toFile());
    } catch(Exception e) {throw Refused.unavailable();}
  }
}

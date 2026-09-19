package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import jakarta.servlet.*;
import java.lang.reflect.Proxy;
import java.net.URL;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.catalina.Context;
import org.apache.catalina.startup.WebappServiceLoader;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Exercise the real pinned Tomcat provider loader, including constructor side effects. */
class StartupInitializerTest {
  @TempDir Path temp;
  public static final class Canary implements ServletContainerInitializer {
    static final AtomicInteger constructed=new AtomicInteger();
    public Canary(){constructed.incrementAndGet();}
    @Override public void onStartup(Set<Class<?>> classes,ServletContext context) {}
  }
  private List<ServletContainerInitializer> providers(String filter,boolean classesProvider) throws Exception {
    var file=temp.resolve("provider");Files.writeString(file,Canary.class.getName()+"\n");URL url=file.toUri().toURL();
    var loader=new ClassLoader(getClass().getClassLoader()) {
      @Override public Enumeration<URL> getResources(String name) {
        return Collections.enumeration(name.equals("META-INF/services/jakarta.servlet.ServletContainerInitializer")?List.of(url):List.of());
      }
    };
    var servlet=(ServletContext)Proxy.newProxyInstance(getClass().getClassLoader(),new Class[]{ServletContext.class},(o,m,args)->switch(m.getName()) {
      case "getAttribute" -> ServletContext.ORDERED_LIBS.equals(args[0])?List.of():null;
      case "getClassLoader" -> loader;
      case "getResource" -> classesProvider?url:null;
      default -> null;
    });
    var context=(Context)Proxy.newProxyInstance(getClass().getClassLoader(),new Class[]{Context.class},(o,m,args)->switch(m.getName()) {
      case "getServletContext" -> servlet;
      case "getParentClassLoader" -> loader;
      case "getContainerSciFilter" -> filter;
      default -> null;
    });
    return new WebappServiceLoader<ServletContainerInitializer>(context).load(ServletContainerInitializer.class);
  }
  @Test void filterSuppressesContainerProviderBeforeItsConstructor() throws Exception {
    Canary.constructed.set(0);assertTrue(providers(".*",false).isEmpty());assertEquals(0,Canary.constructed.get());
  }
  @Test void missingFilterActuallyConstructsTheContainerProvider() throws Exception {
    Canary.constructed.set(0);assertEquals(1,providers(null,false).size());assertEquals(1,Canary.constructed.get());
  }
  @Test void emptyOrderedLibsStillConstructsAClassesProviderAndMustBeSeparatelyRefused() throws Exception {
    Canary.constructed.set(0);assertEquals(1,providers(".*",true).size());assertEquals(1,Canary.constructed.get());
  }
  @Test void orderingMustBeExplicitlyEmptyAndMetadataComplete() throws Exception {
    var file=temp.resolve("web.xml");
    for(String xml:List.of("<web-app metadata-complete='true'/>",
        "<web-app><absolute-ordering/></web-app>",
        "<web-app metadata-complete='true'><absolute-ordering><others/></absolute-ordering></web-app>",
        "<web-app metadata-complete='true'><absolute-ordering/><absolute-ordering/></web-app>")) {
      Files.writeString(file,xml);assertThrows(Refused.class,()->StartupAdmission.sealed(StartupAdmission.parse(file)));
    }
    Files.writeString(file,"<web-app metadata-complete='true'><absolute-ordering/></web-app>");
    assertDoesNotThrow(()->StartupAdmission.sealed(StartupAdmission.parse(file)));
  }
}

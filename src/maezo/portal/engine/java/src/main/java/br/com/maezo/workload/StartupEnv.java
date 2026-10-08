package br.com.maezo.workload;

import java.util.*;
import org.apache.tomcat.util.IntrospectionUtils;
import org.apache.tomcat.util.digester.Digester;

/** The digester ${} substitution for conf/server.xml values, rebuilt from the digester's own inputs. */
final class StartupEnv {
  private StartupEnv() {}
  /** Digester.updateAttributes: no static constants, the constructor-built source array, the digester classloader. */
  static String resolve(String raw,ClassLoader loader) {
    return IntrospectionUtils.replaceProperties(raw,null,sources(),loader);
  }
  /** Digester constructor: PROPERTY_SOURCE token list, then SystemPropertySource appended when absent. */
  private static IntrospectionUtils.PropertySource[] sources() {
    var list=new ArrayList<IntrospectionUtils.PropertySource>();var hasSystemPropertySource=false;
    String property=System.getProperty("org.apache.tomcat.util.digester.PROPERTY_SOURCE");
    if(property!=null) {
      var tokens=new StringTokenizer(property,",");
      while(tokens.hasMoreTokens()) {
        var token=tokens.nextToken().trim();
        for(var loader:new ClassLoader[]{Digester.class.getClassLoader(),Thread.currentThread().getContextClassLoader()}) {
          try {
            var source=(IntrospectionUtils.PropertySource)Class.forName(token,true,loader).getConstructor().newInstance();
            hasSystemPropertySource|=source instanceof org.apache.tomcat.util.digester.SystemPropertySource;list.add(source);break;
          }
          catch(ReflectiveOperationException | ClassCastException failure) {
            // The digester logged and skipped this token at parse time. Load drift between parse and admission changes the resolved snapshot and refuses the equality in StartupNaming. If BOTH sides skip the same token, both snapshots stay literal and the equality passes; the literal ${} datasource then fails at first connection (engine unavailable). The sealed image pins EnvironmentPropertySource in setenv.sh, so reaching this case requires a launcher-level override of CATALINA_OPTS.
          }
        }
      }
    }
    if(!hasSystemPropertySource)list.add(new org.apache.tomcat.util.digester.SystemPropertySource());
    return list.toArray(new IntrospectionUtils.PropertySource[0]);
  }
}

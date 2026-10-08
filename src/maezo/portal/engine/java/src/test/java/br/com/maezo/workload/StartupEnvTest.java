package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import org.apache.catalina.core.StandardServer;
import org.apache.tomcat.util.IntrospectionUtils;
import org.apache.tomcat.util.digester.EnvironmentPropertySource;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/** Pins the rebuilt digester ${} machinery against the real pinned classes; env anchors, no mocks. */
class StartupEnvTest {
  private static final String PROPERTY="org.apache.tomcat.util.digester.PROPERTY_SOURCE";
  private static final String ENV_PATH=System.getenv("PATH"),ENV_HOME=System.getenv("HOME");
  public static final class First implements IntrospectionUtils.PropertySource {
    public String getProperty(String key){return "first:"+key;}
  }
  public static final class Second implements IntrospectionUtils.PropertySource {
    public String getProperty(String key){return "second:"+key;}
  }
  @AfterEach void clear(){System.clearProperty(PROPERTY);System.clearProperty("PATH");System.clearProperty("MAEZO_P25_ABSENT");}

  @Test void environmentPropertySourceReadsTheEnvironmentAndNotSystemProperties() {
    System.setProperty(PROPERTY,EnvironmentPropertySource.class.getName());
    System.setProperty("PATH","sys-property-must-lose");
    assertEquals(ENV_PATH,StartupEnv.resolve("${PATH}",StandardServer.class.getClassLoader()));
  }
  @Test void environmentBeatsTheSystemPropertyFallbackAndResolvedNamesBeatDefaults() {
    System.setProperty(PROPERTY,EnvironmentPropertySource.class.getName());
    System.setProperty("MAEZO_P25_ABSENT","sys-property-must-not-fill");
    assertEquals("sys-property-must-not-fill",StartupEnv.resolve("${MAEZO_P25_ABSENT}",StandardServer.class.getClassLoader()));
    assertEquals("sys-property-must-not-fill",StartupEnv.resolve("${MAEZO_P25_ABSENT:-unit-default}",StandardServer.class.getClassLoader()));
    assertEquals(ENV_PATH,StartupEnv.resolve("${PATH:-unit-default}",StandardServer.class.getClassLoader()));
  }
  @Test void multipleEmbeddedPlaceholdersResolveInsideOneValue() {
    System.setProperty(PROPERTY,EnvironmentPropertySource.class.getName());
    assertEquals("jdbc:x://"+ENV_HOME+":"+ENV_PATH+"/db?a=${MAEZO_P25_ABSENT}&b="+ENV_PATH,
        StartupEnv.resolve("jdbc:x://${HOME}:${PATH}/db?a=${MAEZO_P25_ABSENT}&b=${PATH}",StandardServer.class.getClassLoader()));
  }
  @Test void sourceListIsCommaSeparatedTrimmedOrderedAndSkipsUnloadableTokens() {
    System.setProperty(PROPERTY," no.such.MaezoP25Source , "+First.class.getName()+" , "+Second.class.getName());
    assertEquals("first:k",StartupEnv.resolve("${k}",StandardServer.class.getClassLoader()));
    System.setProperty(PROPERTY,Second.class.getName());
    assertEquals("second:k",StartupEnv.resolve("${k}",StandardServer.class.getClassLoader()));
  }
  @Test void absentPropertySourceResolvesThroughTheAutoAppendedSystemPropertySource() {
    System.setProperty("MAEZO_P25_ABSENT","sys-property-must-not-fill");
    assertEquals("sys-property-must-not-fill",StartupEnv.resolve("${MAEZO_P25_ABSENT}",StandardServer.class.getClassLoader()));
    assertEquals(System.getProperty("PATH")==null?"${PATH}":System.getProperty("PATH"),StartupEnv.resolve("${PATH}",StandardServer.class.getClassLoader()));
    System.setProperty(PROPERTY," no.such.MaezoP25Source , no.such.MaezoP25Too ");
    assertEquals("sys-property-must-not-fill",StartupEnv.resolve("${MAEZO_P25_ABSENT}",StandardServer.class.getClassLoader()));
  }
}

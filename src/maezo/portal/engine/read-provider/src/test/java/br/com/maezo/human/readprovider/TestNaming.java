package br.com.maezo.human.readprovider;

import java.lang.reflect.Proxy;
import java.util.Hashtable;
import javax.naming.Context;
import javax.naming.NameNotFoundException;
import javax.naming.OperationNotSupportedException;
import javax.naming.spi.InitialContextFactory;
import javax.sql.DataSource;

/**
 * Test-only JNDI root (selected by the {@code java.naming.factory.initial} system property of the
 * JAR IT execution). It binds exactly one name, the one the engine descriptor uses, so the provider
 * resolves its DataSource the same way it does inside Tomcat: through {@code new InitialContext()}.
 */
public final class TestNaming implements InitialContextFactory {
  public static final String PROCESS_ENGINE = "java:jdbc/ProcessEngine";
  private static volatile DataSource bound;

  public static void bind(DataSource source) {
    bound = source;
  }

  @Override
  public Context getInitialContext(Hashtable<?, ?> environment) {
    return (Context) Proxy.newProxyInstance(TestNaming.class.getClassLoader(),
        new Class<?>[] {Context.class}, (proxy, method, args) -> {
          switch (method.getName()) {
            case "lookup":
              String name = String.valueOf(args[0]);
              DataSource source = bound;
              if (!PROCESS_ENGINE.equals(name) || source == null)
                throw new NameNotFoundException(name);
              return source;
            case "close":
              return null;
            case "toString":
              return "TestNaming";
            case "hashCode":
              return System.identityHashCode(proxy);
            case "equals":
              return proxy == args[0];
            default:
              throw new OperationNotSupportedException(method.getName());
          }
        });
  }
}

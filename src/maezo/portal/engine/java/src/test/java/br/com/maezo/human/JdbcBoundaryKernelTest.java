package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.lang.reflect.Proxy;
import java.sql.Connection;
import java.util.*;
import org.apache.ibatis.executor.SimpleExecutor;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.defaults.DefaultSqlSession;
import org.apache.ibatis.transaction.jdbc.JdbcTransaction;
import org.junit.jupiter.api.Test;

/** Real MyBatis kernel with a recording JDBC interface. No engine or DB integration claim. */
class JdbcBoundaryKernelTest {
  DefaultSqlSession session(List<String> events) {
    var connection = (Connection) Proxy.newProxyInstance(Connection.class.getClassLoader(),
        new Class<?>[]{Connection.class}, (proxy, method, args) -> {
          switch (method.getName()) {
            case "getAutoCommit": return false;
            case "setAutoCommit": events.add("setAutoCommit:" + args[0]); return null;
            case "commit": case "rollback": case "close": events.add(method.getName()); return null;
            case "toString": return "independent-recording-jdbc";
            default: throw new UnsupportedOperationException(method.getName());
          }
        });
    var config = new Configuration();
    return new DefaultSqlSession(config, new SimpleExecutor(config, new JdbcTransaction(connection)), false);
  }

  @Test void rawConnectionAccessLeavesUnforcedRollbackSkippedUntilCloseResetsAutocommit() {
    var events = new ArrayList<String>();
    var session = session(events);
    assertNotNull(session.getConnection());
    session.rollback();
    assertTrue(events.isEmpty(), "Unforced rollback of an unmarked session does not reach JDBC");
    session.close();
    assertEquals(List.of("setAutoCommit:true", "close"), events);
  }

  @Test void forcedRollbackControlReachesJdbcBeforeClose() {
    var events = new ArrayList<String>();
    var session = session(events);
    assertNotNull(session.getConnection());
    session.rollback(true);
    session.close();
    assertEquals(List.of("rollback", "setAutoCommit:true", "close"), events);
  }
}

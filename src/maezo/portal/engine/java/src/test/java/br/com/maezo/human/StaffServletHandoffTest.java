package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;
import jakarta.servlet.ServletOutputStream;
import jakarta.servlet.WriteListener;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;

/** Actual output handoff with synthetic HTTP/time; no engine or full route qualification. */
class StaffServletHandoffTest {
  private static final long UNTIL = 10;
  private static final byte[] PROTECTED =
      "{\"synthetic_staff_receipt\":\"retained-original\"}".getBytes(StandardCharsets.UTF_8);
  private static final byte[] UNAVAILABLE =
      "{\"error\":\"HUMAN_ENGINE_UNAVAILABLE\"}".getBytes(StandardCharsets.UTF_8);

  @Test
  void currentReadAndPublicationWriteTheOriginalCommittedBytes() throws Exception {
    for (boolean publication : List.of(false, true)) {
      var fixture = new Fixture(publication, true, false);
      var reply = new Reply(fixture, false);
      HumanServlet.writeStaff(reply.response, fixture.response);
      assertEquals(200, reply.status);
      assertArrayEquals(PROTECTED, reply.body.toByteArray());
      assertEquals(List.of("stream", "guard", "guard", "write"), fixture.events);
      assertEquals(2, fixture.guardCalls);
      fixture.assertRetained(true);
    }
  }

  @Test
  void streamSetupExpiryRefusesBothKindsBeforeAnyProtectedByte() throws Exception {
    for (boolean publication : List.of(false, true)) {
      var fixture = new Fixture(publication, true, false);
      var reply = new Reply(fixture, true);
      Rejected rejected = assertThrows(Rejected.class,
          () -> HumanServlet.writeStaff(reply.response, fixture.response));
      assertEquals(List.of("stream", "guard"), fixture.events);
      assertEquals(UNTIL, fixture.now);
      assertClosedUnavailable(reply, rejected);
      fixture.assertRetained(true);
    }
  }

  @Test
  void expiryAfterBytesCheckStillRequiresTheFinalOriginalGuard() throws Exception {
    for (boolean publication : List.of(false, true)) {
      var fixture = new Fixture(publication, true, true);
      var reply = new Reply(fixture, false);
      Rejected rejected = assertThrows(Rejected.class,
          () -> HumanServlet.writeStaff(reply.response, fixture.response));
      assertEquals(List.of("stream", "guard", "guard"), fixture.events);
      assertEquals(2, fixture.guardCalls);
      assertClosedUnavailable(reply, rejected);
      fixture.assertRetained(true);
    }
  }

  @Test
  void uncommittedReadAndPublicationCannotBeConvertedIntoSuccess() throws Exception {
    for (boolean publication : List.of(false, true)) {
      var fixture = new Fixture(publication, false, false);
      var reply = new Reply(fixture, false);
      Rejected rejected = assertThrows(Rejected.class,
          () -> HumanServlet.writeStaff(reply.response, fixture.response));
      assertEquals(0, fixture.guardCalls);
      assertClosedUnavailable(reply, rejected);
      fixture.assertRetained(false);
    }
  }

  private static void assertClosedUnavailable(Reply reply, Rejected rejected) throws Exception {
    assertEquals(503, rejected.status);
    assertEquals("HUMAN_ENGINE_UNAVAILABLE", rejected.code);
    // The production helper throws into service's existing Rejected handler. Invoke
    // that exact private formatter, not a test copy; full routing is a separate gate.
    assertEquals(0, reply.body.size(), "no protected bytes before the closed error");
    var error = HumanServlet.class.getDeclaredMethod(
        "error", HttpServletResponse.class, int.class, String.class);
    error.setAccessible(true);
    error.invoke(null, reply.response, rejected.status, rejected.code);
    assertEquals(503, reply.status);
    assertArrayEquals(UNAVAILABLE, reply.body.toByteArray());
  }

  private static final class Fixture {
    long now;
    int guardCalls;
    final List<String> events = new ArrayList<>();
    final HumanCommandPlugin.StaffResponse response;
    final BooleanSupplier committed;
    final Supplier<byte[]> frozen;

    Fixture(boolean publication, boolean physicallyCommitted, boolean expireAfterFirstCheck)
        throws Exception {
      Runnable current = () -> {
        events.add("guard");
        guardCalls++;
        if (now >= UNTIL) throw PortalReadModels.unavailable();
        // Model elapsed time after Result.bytes' own check, while preserving that
        // same clock/deadline for the final original guard before output.write.
        if (expireAfterFirstCheck && guardCalls == 1) now = UNTIL;
      };
      Supplier<byte[]> bytes;
      if (publication) {
        var result = new StaffCasePublicationCommand.Result();
        result.frozen = PROTECTED.clone();
        result.committed = physicallyCommitted;
        result.current = current;
        bytes = result::bytes;
        committed = () -> result.committed;
        frozen = () -> result.frozen;
      } else {
        var result = new StaffCaseReadCommand.Result();
        result.frozen = PROTECTED.clone();
        result.committed = physicallyCommitted;
        result.current = current;
        bytes = result::bytes;
        committed = () -> result.committed;
        frozen = () -> result.frozen;
      }
      // Instantiate the actual private handoff wrapper without widening production
      // constructors or replacing a native executor. Actual Result.bytes is retained.
      var constructor = HumanCommandPlugin.StaffResponse.class.getDeclaredConstructor(
          Supplier.class, Runnable.class);
      constructor.setAccessible(true);
      response = constructor.newInstance(bytes, current);
    }

    void assertRetained(boolean expectedCommit) {
      assertEquals(expectedCommit, committed.getAsBoolean());
      assertArrayEquals(PROTECTED, frozen.get());
    }
  }

  private static final class Reply {
    final ByteArrayOutputStream body = new ByteArrayOutputStream();
    final HttpServletResponse response;
    int status;
    boolean wrote;
    int streamCalls;

    Reply(Fixture fixture, boolean expireDuringStreamSetup) {
      var stream = new ServletOutputStream() {
        public void write(int value) {
          if (!wrote) { fixture.events.add("write"); wrote = true; }
          body.write(value);
        }
        public boolean isReady() { return true; }
        public void setWriteListener(WriteListener listener) {}
      };
      response = (HttpServletResponse) Proxy.newProxyInstance(
          StaffServletHandoffTest.class.getClassLoader(),
          new Class<?>[] {HttpServletResponse.class}, (object, method, args) -> {
            if (method.getName().equals("setStatus")) status = (Integer) args[0];
            if (method.getName().equals("getOutputStream")) {
              fixture.events.add("stream");
              streamCalls++;
              if (expireDuringStreamSetup && streamCalls == 1) fixture.now = UNTIL;
              return stream;
            }
            return null;
          });
    }
  }
}

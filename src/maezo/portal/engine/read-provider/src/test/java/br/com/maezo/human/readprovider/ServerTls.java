package br.com.maezo.human.readprovider;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

/**
 * Test-only TLS for the IT PostgreSQL, so the membership observer's {@code verify-full} is proven
 * against a real handshake instead of being assumed. Once per JVM: a synthetic CA (plus a second,
 * unrelated CA for the negative) and a server certificate whose SAN is the IT host are generated
 * with the JDK's {@code keytool}; the server files are written INTO the server's data directory with
 * {@code COPY ... TO} and TLS is switched on with {@code ALTER SYSTEM} + {@code pg_reload_conf()}.
 *
 * <p>This needs a superuser on a DISPOSABLE server (the CI service container, the local Docker
 * container): it changes the server configuration. Nothing here is a production certificate.
 */
public final class ServerTls {
  private ServerTls() {}

  /** What the ITs need: the CA that signed the server, an unrelated CA, and two host names. */
  public record Material(Path ca, Path otherCa, String host, String mismatchHost) {}

  private static Material material;

  public static synchronized Material ensure(String adminUrl, String user, String password,
      Path directory) throws Exception {
    if (material != null)
      return material;
    String host = host(adminUrl);
    Files.createDirectories(directory);
    Path ca = directory.resolve("source-ca.pem"), other = directory.resolve("other-ca.pem");
    Path caStore = directory.resolve("ca.p12"), otherStore = directory.resolve("other.p12"),
         serverStore = directory.resolve("server.p12");
    for (Path p : List.of(caStore, otherStore, serverStore)) Files.deleteIfExists(p);
    String pass = "it-only-" + Long.toHexString(System.nanoTime());
    keytool(null, "-genkeypair", "-alias", "ca", "-keyalg", "EC", "-groupname", "secp256r1",
        "-dname", "CN=maezo-read-provider-it-ca", "-ext", "bc:c", "-validity", "2", "-keystore",
        caStore.toString(), "-storetype", "PKCS12", "-storepass", pass);
    Files.write(ca, keytool(null, "-exportcert", "-rfc", "-alias", "ca", "-keystore",
        caStore.toString(), "-storepass", pass));
    keytool(null, "-genkeypair", "-alias", "ca", "-keyalg", "EC", "-groupname", "secp256r1",
        "-dname", "CN=maezo-read-provider-it-other-ca", "-ext", "bc:c", "-validity", "2",
        "-keystore", otherStore.toString(), "-storetype", "PKCS12", "-storepass", pass);
    Files.write(other, keytool(null, "-exportcert", "-rfc", "-alias", "ca", "-keystore",
        otherStore.toString(), "-storepass", pass));
    keytool(null, "-genkeypair", "-alias", "server", "-keyalg", "EC", "-groupname", "secp256r1",
        "-dname", "CN=" + host, "-validity", "2", "-keystore", serverStore.toString(),
        "-storetype", "PKCS12", "-storepass", pass);
    byte[] request = keytool(null, "-certreq", "-alias", "server", "-keystore",
        serverStore.toString(), "-storepass", pass);
    String san = host.matches("[0-9.]+") ? "ip:" + host : "dns:" + host;
    byte[] serverCert = keytool(request, "-gencert", "-rfc", "-alias", "ca", "-keystore",
        caStore.toString(), "-storepass", pass, "-ext", "SAN=" + san, "-ext", "EKU=serverAuth",
        "-validity", "2");
    var store = KeyStore.getInstance("PKCS12");
    try (InputStream in = Files.newInputStream(serverStore)) {
      store.load(in, pass.toCharArray());
    }
    PrivateKey key = (PrivateKey) store.getKey("server", pass.toCharArray());
    String keyPem = "-----BEGIN PRIVATE KEY-----\n"
        + Base64.getMimeEncoder(64, "\n".getBytes(StandardCharsets.US_ASCII))
              .encodeToString(key.getEncoded())
        + "\n-----END PRIVATE KEY-----\n";
    String mismatch;
    try (Connection c = DriverManager.getConnection(adminUrl, user, password);
         Statement s = c.createStatement()) {
      String data;
      try (ResultSet r = s.executeQuery("SHOW data_directory")) {
        r.next();
        data = r.getString(1);
      }
      String certFile = data + "/maezo_read_provider_it.crt";
      String keyFile = data + "/maezo_read_provider_it.key";
      s.execute(copy(new String(serverCert, StandardCharsets.US_ASCII), certFile));
      s.execute(copy(keyPem, keyFile));
      s.execute("ALTER SYSTEM SET ssl_cert_file = '" + certFile + "'");
      s.execute("ALTER SYSTEM SET ssl_key_file = '" + keyFile + "'");
      s.execute("ALTER SYSTEM SET ssl_ca_file = ''");
      s.execute("ALTER SYSTEM SET ssl = 'on'");
      s.execute("SELECT pg_catalog.pg_reload_conf()");
      if (host.equals("localhost")) {
        mismatch = "127.0.0.1";
      } else {
        try (ResultSet r =
                 s.executeQuery("SELECT pg_catalog.host(pg_catalog.inet_server_addr())")) {
          r.next();
          mismatch = r.getString(1);
        }
      }
    }
    if (mismatch == null || !mismatch.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}") || mismatch.equals(host))
      throw new IllegalStateException("no IPv4 address outside the certificate SAN: " + mismatch);
    // SHOW ssl says "on" even when the server refused to load the files: prove a TLS session.
    var tls = new java.util.Properties();
    tls.setProperty("user", user);
    tls.setProperty("password", password);
    tls.setProperty("sslmode", "require");
    for (int i = 0; i < 100; i++) {
      try (Connection c = DriverManager.getConnection(adminUrl, tls);
           Statement s = c.createStatement(); ResultSet r = s.executeQuery(
               "SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_catalog.pg_backend_pid()")) {
        if (r.next() && r.getBoolean(1)) {
          material = new Material(ca, other, host, mismatch);
          return material;
        }
      } catch (java.sql.SQLException notYet) {
        // the postmaster applies the reload asynchronously
      }
      Thread.sleep(100);
    }
    throw new IllegalStateException("the IT server did not turn TLS on (see the server log)");
  }

  /** The host of {@code jdbc:postgresql://host[:port]/db}. */
  static String host(String url) {
    var m = java.util.regex.Pattern.compile("jdbc:postgresql://([A-Za-z0-9.-]+)(:[0-9]+)?/.*")
                .matcher(url);
    if (!m.matches())
      throw new IllegalStateException("IT JDBC URL must be jdbc:postgresql://host[:port]/db");
    return m.group(1);
  }

  private static String copy(String pem, String file) {
    pem = pem.replace("\r\n", "\n");
    if (!pem.matches("[A-Za-z0-9+/=\\n -]+"))
      throw new IllegalStateException("unexpected PEM text: "
          + pem.chars().filter(c -> !(Character.isLetterOrDigit(c) || "+/=\n -".indexOf(c) >= 0))
                .mapToObj(Integer::toString).distinct().toList());
    // COPY ... TO 'file' creates 0644, and the server refuses a private key with group/world
    // access; TO PROGRAM (superuser) lets the shell create it with umask 077 instead.
    if (!file.matches("[A-Za-z0-9_./-]+"))
      throw new IllegalStateException("unexpected server path: " + file);
    return "COPY (SELECT pg_catalog.unnest(pg_catalog.string_to_array($pem$" + pem.strip()
        + "$pem$, E'\\n'))) TO PROGRAM 'rm -f " + file + " && umask 077 && cat > "
        + file + "'";
  }

  private static byte[] keytool(byte[] input, String... args) throws IOException,
      InterruptedException {
    List<String> command = new ArrayList<>();
    command.add(Path.of(System.getProperty("java.home"), "bin", "keytool").toString());
    command.addAll(List.of(args));
    command.add("-noprompt");
    Process process = new ProcessBuilder(command).redirectErrorStream(false).start();
    if (input != null)
      process.getOutputStream().write(input);
    process.getOutputStream().close();
    var out = new ByteArrayOutputStream();
    process.getInputStream().transferTo(out);
    String errors = new String(process.getErrorStream().readAllBytes(), StandardCharsets.UTF_8);
    if (process.waitFor() != 0)
      throw new IllegalStateException("keytool failed: " + errors);
    return out.toByteArray();
  }
}

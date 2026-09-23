package br.com.maezo.human.readprovider;

import br.com.maezo.human.PortalReadTrust;
import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * One verified observation of the installed admission. Every method is synchronous, local and does
 * no I/O (the plugin calls them inside engine commands, after the tenant lock). Only
 * {@link InstalledReadProviders#acquire} reads the database.
 */
final class ProviderAdmission implements PortalReadTrust.Admission {
  final InstalledReadProviders provider;
  final AdmissionRecord record;
  /** The purpose this observation was acquired for; publication qualification requires its own. */
  final String purpose;
  private final Instant observedAt, validUntil;

  ProviderAdmission(InstalledReadProviders provider, AdmissionRecord record, String purpose,
      Instant observedAt, Instant validUntil) {
    this.provider = provider;
    this.record = record;
    this.purpose = purpose;
    this.observedAt = observedAt;
    this.validUntil = validUntil;
  }

  @Override
  public String generation() {
    return Long.toString(record.revision);
  }

  @Override
  public String providerRef() {
    return record.admissionRef;
  }

  @Override
  public String providerRevision() {
    return Long.toString(record.revision);
  }

  @Override
  public String capabilityDigest() {
    return record.digest;
  }

  @Override
  public Instant observedAt() {
    return observedAt;
  }

  @Override
  public Instant validUntil() {
    return validUntil;
  }

  @Override
  public int statementTimeoutSeconds() {
    return record.statementTimeoutSeconds;
  }

  /**
   * Current while inside its own bounded window AND not superseded nor revoked by any later
   * {@code acquire} of this provider. Revocation latency: the next staff operation (the staff path
   * acquires per request); for an admission already retained, at most {@code observation_seconds}.
   */
  @Override
  public void requireCurrent() {
    if (!provider.clock.instant().isBefore(validUntil) || !provider.current(record.revision))
      throw Fields.unavailable();
  }

  @Override
  public void verifySource(String kind, Map<String, Object> source) {
    requireCurrent();
    try {
      var publisher = record.publishers.get(kind);
      if (publisher == null || source == null)
        throw Fields.unavailable();
      Fields.exact(source, "publisher_ref", publisher.publisherRef());
      if (!Fields.ref(source, "source_ref").startsWith(publisher.sourceRefPrefix()))
        throw Fields.unavailable();
      Instant now = provider.clock.instant();
      if (Fields.time(source, "observed_at").isAfter(now)
          || !now.isBefore(Fields.time(source, "valid_until")))
        throw Fields.unavailable();
    } catch (RuntimeException refused) {
      throw Fields.unavailable();
    }
  }

  @Override
  public void verifyCatalog(Map<String, Object> artifact) {
    requireCurrent();
    try {
      if (artifact == null || !record.catalogRef.equals(artifact.get("catalog_ref"))
          || !record.catalogPublisherRef.equals(artifact.get("publisher_ref"))
          || !record.catalogDigest.equals(Fields.digest(artifact)))
        throw Fields.unavailable();
    } catch (RuntimeException refused) {
      throw Fields.unavailable();
    }
  }

  /** Task identity policy is Onda 8: nothing in the staff scope may be admitted through here. */
  @Override
  public void verifyIdentityPolicy(
      Map<String, Object> policy, Map<String, Object> task, List<Object> links) {
    throw Fields.unavailable();
  }

  /** Task field classification is Onda 8: always refused. */
  @Override
  public void verifyClassification(Map<String, Object> classification, Map<String, Object> entry) {
    throw Fields.unavailable();
  }

  @Override
  public String toString() {
    return "PortalReadAdmission[" + record.admissionRef + "@" + record.revision + "]";
  }
}

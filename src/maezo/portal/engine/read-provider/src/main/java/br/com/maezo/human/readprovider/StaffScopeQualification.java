package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.PortalReadTrust;
import java.time.Instant;
import java.util.Arrays;
import java.util.Map;

/**
 * One qualified publication of the staff scope (T1.7b), built by
 * {@link InstalledReadProviders#qualifyPublication} OUTSIDE the engine command and consumed by
 * {@code PortalReadPublication} INSIDE it. Everything here is synchronous, local and I/O-free: the
 * live source was read before this object existed, and {@link #verify} only compares in memory.
 *
 * <p>{@link #verify} accepts exactly the (kind, source, payload) that was qualified, and for each kind
 * the facts the provider established on its own:
 * <ul>
 *   <li>{@code membership}: the payload IS the projection of the live
 *       {@code portal_memberships} row, {@code source_revision} is that row's revision,
 *       {@code source_digest = SHA-256(JCS(payload))}, {@code receipt_ref} is
 *       {@code portal-identity:<tenant>:membership:<principal_ref>@<revision>}, and
 *       {@code valid_until = observed_at + observation_seconds} (the T1.5 contract);
 *   <li>{@code catalog-designate}: catalog ref, digest and publisher are the ADMITTED ones;
 *   <li>{@code catalog-revoke}: catalog ref and publisher are the ADMITTED ones;
 *   <li>{@code revoke-key}: nothing more; it only reduces authority and the envelope was already
 *       verified with the publication key;
 *   <li>{@code resource} (H1): {@code source_ref = <admitted prefix> + task_id},
 *       {@code source_revision = resource_revision}, {@code source_digest = SHA-256(JCS(payload))},
 *       {@code receipt_ref = portal-resource:<tenant>:task:<task_id>@<resource_revision>},
 *       {@code valid_until = observed_at + observation_seconds}, and a classification the approver
 *       admitted for that process definition.
 * </ul>
 * The snapshot lives and dies with the admission it was taken under (see {@link #requireCurrent}).
 */
final class StaffScopeQualification implements PortalReadTrust.PublicationQualification {
  private final ProviderAdmission admission;
  private final String kind;
  private final byte[] source, payload;
  /** The live projection for {@code membership}; null for every other kind. */
  private final Map<String, Object> observed;

  /** Only {@link InstalledReadProviders#qualifyPublication} builds one, for an admitted kind. */
  StaffScopeQualification(ProviderAdmission admission, String kind, Map<String, Object> source,
      Map<String, Object> payload, Map<String, Object> observed) {
    if (kind.equals("membership") != (observed != null))
      throw Fields.unavailable();
    this.admission = admission;
    this.kind = kind;
    this.source = Jcs.canonical(source);
    this.payload = Jcs.canonical(payload);
    this.observed = observed;
  }

  /**
   * The snapshot is bound to the admission it was taken under. That admission was acquired BEFORE
   * the snapshot and is good for at most {@code observation_seconds} from its acquisition, so it is
   * also the tighter bound on the snapshot's age; a revoked or superseded admission kills it.
   */
  @Override
  public void requireCurrent() {
    admission.requireCurrent();
  }

  @Override
  public Instant validUntil() {
    return admission.validUntil();
  }

  @Override
  public void verify(String kind, Map<String, Object> source, Map<String, Object> payload) {
    requireCurrent();
    try {
      if (!this.kind.equals(kind) || source == null || payload == null
          || !Arrays.equals(this.source, Jcs.canonical(source))
          || !Arrays.equals(this.payload, Jcs.canonical(payload)))
        throw Fields.unavailable();
      var record = admission.record;
      switch (kind) {
        case "membership" -> {
          if (!Arrays.equals(Jcs.canonical(observed), this.payload))
            throw Fields.unavailable();
          String revision = (String) observed.get("membership_revision");
          String tenant = (String) record.scope.get("tenant");
          Instant observedAt = Fields.time(source, "observed_at");
          if (!revision.equals(source.get("source_revision"))
              || !Fields.digest(observed).equals(source.get("source_digest"))
              || !("portal-identity:" + tenant + ":membership:" + observed.get("principal_ref")
                      + "@" + revision)
                      .equals(source.get("receipt_ref"))
              || !Fields.time(source, "valid_until")
                      .equals(observedAt.plusSeconds(record.observationSeconds)))
            throw Fields.unavailable();
        }
        case "catalog-designate" -> {
          if (!record.catalogRef.equals(payload.get("catalog_ref"))
              || !record.catalogDigest.equals(payload.get("catalog_digest"))
              || !record.catalogPublisherRef.equals(source.get("publisher_ref")))
            throw Fields.unavailable();
        }
        case "catalog-revoke" -> {
          // Only the admitted catalog publisher may tombstone the admitted catalog.
          if (!record.catalogRef.equals(payload.get("catalog_ref"))
              || !record.catalogPublisherRef.equals(source.get("publisher_ref")))
            throw Fields.unavailable();
        }
        case "revoke-key" -> {}
        case "resource" -> {
          // H1 contract with the Python publisher (tests/fixtures/portal_read/jcs-resource-vector.json):
          // the provenance is DERIVED from the payload, never a free-standing claim.
          var publisher = record.publishers.get("resource");
          String task = Fields.ref(payload, "task_id");
          String revision = Long.toString(Fields.decimal(payload, "resource_revision", 0,
              Long.MAX_VALUE - 1));
          Instant observedAt = Fields.time(source, "observed_at");
          if (publisher == null
              || !(publisher.sourceRefPrefix() + task).equals(source.get("source_ref"))
              || !revision.equals(source.get("source_revision"))
              || !Fields.digest(payload).equals(source.get("source_digest"))
              || !("portal-resource:" + record.scope.get("tenant") + ":task:" + task + "@"
                      + revision).equals(source.get("receipt_ref"))
              || !Fields.time(source, "valid_until")
                      .equals(observedAt.plusSeconds(record.observationSeconds)))
            throw Fields.unavailable();
          record.human.verifyPublishedClassification(Fields.ref(payload, "process_definition_id"),
              Fields.object(payload.get("classification")), record.validUntil);
        }
        default -> throw Fields.unavailable();
      }
    } catch (RuntimeException refused) {
      throw Fields.unavailable();
    }
  }

  @Override
  public String toString() {
    return "StaffScopeQualification[" + kind + ",redacted]";
  }
}

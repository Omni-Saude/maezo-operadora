# ADR-0053: Secured v1 startup refusal before engine activation

**Status:** Accepted for the explicitly authorized isolated engineering source allocation; independent source/runtime and production gates remain pending.
**Date:** 2026-09-15
**Area:** Engine transport and lifecycle ownership

## Context

ADR-0049 D7 requires closed engine ingress. D7B-ACCEPT-02 requires real failed protected contexts with authenticated HTTPS404, unchanged native effects and recovery by a new container. The pinned CIB2.1 Server listener starts platform pools, jobs and engines before Tomcat starts Services. A bad boundary CA/tenant/environment therefore aborts the Server and destroys HTTPS. Vendor rollback can miss constructed-but-unregistered resources; catching a late engine failure and continuing cannot establish containment.

## Decision

The opt-in secured v1 distribution owns one Server startup adapter. After verifying exact artifact custody and actual safe mTLS/container topology, it applies the existing BoundaryPolicy loader before any vendor deployment operation. A typed policy refusal becomes terminal PREFLIGHT_REFUSED with zero engine/platform/REST-worker activation. The unchanged native preInit checks still run on the positive vendor path; all late/bootstrap errors remain fatal. No same-JVM retry, mutable caller latch or authority callback is exposed.

A lazy REST-loader listener does not construct or start its vendor fetch-handler delegate on refusal. The genuine BoundaryFilter then throws, preserving the required native filter/context logs and Tomcat404. Exact Context lifecycle provenance records failed startup and completed cleanup, normally STOPPED. A synchronous Engine AFTER_START observer validates this provenance and zero engine resources before the Service starts connector acceptors. Engine/Service/Server throwOnFailure must be true and guard errors propagate. All actual installed initializer/SCI/listener/filter and loader identities are pinned; no alternate engine bootstrap is admitted.

The original vendor class bytes, ordinary/human image behavior, TLS requirement, TRACE refusal, policy/layout validation and native-v2 admission remain intact. No synthetic response or fallback transport earns acceptance. No SQL, controller, clinical autonomy or production authority is changed.

The reviewed detailed contract is [STARTUP-CONTAINMENT.md](../../deploy/cibseven/secured/STARTUP-CONTAINMENT.md). It derives from ADR-0049 D7/D8, ADR-0004 isolation and ADR-0006 perimeter protection. Design-v3 SHA256: `54eadc5b22ef844527cb1b7765ce181d375c6f52b7d8793d863b6f04b8e40dab`, independently approved for bounded source implementation.

## Preventive initializer admission

The independently checked actual image contains common CIB, Jasper and WebSocket SCIs, a separate Spring webapp without a descriptor, and background listeners in examples. The secured v1 package therefore denies container SCIs before construction with the effective Context `containerSciFilter=".*"`, and admits no webapp JAR SCIs through metadata-complete descriptors with an empty absolute ordering. WEB-INF/classes service providers remain independently forbidden: empty ordering does not suppress them. These controls apply before context initialization in both startup branches.

The existing finite REST and human routes retain explicit bootstrap declarations. A closed descriptor is additive for webapp and replaces the examples initializer descriptor; all context directories and original route oracles remain. Manager and host-manager keep their exact realm, security constraints, servlets, filters and RemoteAddrValve. ROOT/docs retain static declarations. Global eager JSP startup and JSP mappings are excluded from the secured finite route package. Common loader and webapp initializer entries are pinned against the actual exported image, and effective configuration is verified before provider construction. Ordinary images are unaffected.

## Consequences

Unsafe transport/custody, ambiguous object/loader ownership, residual resources and late failures remain fatal and receive no authenticated404 credit. Actual Jakarta ABI, initializer inventory, zero-worker lifecycle controls and all three unchanged packaged faults require independent exact-artifact verification. Tomcat transport infrastructure may run to serve mTLS; native jobs/acquisition/metrics/fetch workers may not start on refusal. Restore uses a new owned container and the original pending operation.

## Supersedes

None. Existing acceptance or authority requirements are not reduced.

## Independent repair of startup ordering and completion

The independent fa9 review found SS-F01 (CONFIGURE_START eagerly constructs JDBC before START), SS-F02 (later GlobalResources resolves it again), and SS-F03 (positive completion omitted actual context/filter/servlet state and fresh policy). The separately authored repair follows approved design-v4 SHA256 `4c7e57e2c10a5d9516cdf5cefb043a3bc87cc7173e027c39dde0ebe7e1620e92`. BEFORE_START establishes PREFLIGHT_ACCEPTED or terminal PREFLIGHT_REFUSED before naming configuration. An exact bound-reference factory refuses before constructing the original JDBC delegate; positive resolution retains the original singleton and JMX semantics. A lazy GlobalResources wrapper preserves positive lifecycle ownership. The final Engine guard requires complete actual successful context/filter/REST/eager-servlet provenance and freshly admitted policy; unexpected failures remain fatal. No source or runtime approval is implied by this decision.

## Pinned Tomcat listener sequencing (STARTUP-F03)

The actual Tomcat10.1.47 server ThreadLocalLeakPreventionListener registers itself on each new context after the secured Host ADD_CHILD observer. Bind that exact already admitted server object and its one post-enrollment position. Preserve captured listener identities and order; no class-name whitelist extension. This supported installation requires bootstrap-before-TLL ordering, an initially empty Host and serial Host deployment (startStopThreads=1).

The real ContextConfig also creates a HostWebXmlCacheCleaner during its first successful default-web configuration. Require initial cache/listener absence, then capture the actual pinned cleaner and cache-entry identities only after the admitted ContextConfig CONFIGURE_START callback. Retain exact identities/order thereafter and leave destruction to the vendor. Unsupported concurrency, missing reflection access, cache drift and unknown/duplicate/substituted listeners refuse. Both allocations require real HostConfig positive and negative regressions before fresh packaged startup acceptance. Original D7 HTTP/refusal/effect and native-v2 boundaries remain unchanged.

The Host cache/listener identity gate is a pre-Mapper startup check: StandardService starts the real MapperListener after Engine.start returns and before connectors start. Later Mapper registration is not admitted into that earlier set. Positive and mutation controls run at the actual Engine AFTER_START boundary; post-server-start observations retain the legitimate Mapper separately and cannot supply a negative baseline.

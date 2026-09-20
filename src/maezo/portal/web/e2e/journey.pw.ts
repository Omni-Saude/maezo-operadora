/*
 * The synthetic browser proof of the portal session slice (WP-J1-10, J1-DESIGN §3 passo 5).
 *
 * REAL in this journey: the built SPA bundle, the FastAPI BFF with its production login/
 * callback/session routes, the transaction state+browser cookie protocol, the code
 * single-claim fence, session issuance and membership resolution.
 * SYNTHETIC: only the identity source — the OIDC stub answers the fixed code, and the one
 * IdP hop of the handshake is performed by support/portal-target.ts (no IdP, no network).
 * NOT CLAIMED HERE: engine-committed intake/claim/decision legs (custody lane) and the
 * human VoiceOver pass (flagged in the evidence, never skipped silently).
 */

import fs from "node:fs";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { interceptIdp, type TargetObservation } from "./support/portal-target";
import { assertNoPersistedState, readStorage, sha256, type StorageSnapshot } from "./support/privacy";
import { assertFocusIsVisible, scanAxe, tabTo, type AxeResult } from "./support/wcag";

type Runtime = {
  artifacts: string;
  dist: string;
  portalOrigin: string;
  idpOrigin: string;
  code: string;
  fixture: {
    expectations: Record<string, string>;
    synthetic_phi_markers: Record<string, string>;
    roles: string[];
    audience: string;
  };
};

function runtime(): Runtime {
  const directory = process.env.MAEZO_PORTAL_E2E_RUNTIME;
  if (!directory) throw new Error("MAEZO_PORTAL_E2E_RUNTIME is required (written by run_portal_journey.py)");
  const artifacts = process.env.MAEZO_PORTAL_E2E_ARTIFACTS;
  const dist = process.env.MAEZO_PORTAL_E2E_DIST;
  const portalOrigin = process.env.MAEZO_PORTAL_E2E_ORIGIN;
  if (!artifacts || !dist || !portalOrigin) {
    throw new Error("MAEZO_PORTAL_E2E_ARTIFACTS/_DIST/_ORIGIN are required");
  }
  const fixture = JSON.parse(
    fs.readFileSync(new URL("fixtures/journey-fixture.json", import.meta.url), "utf8"),
  ) as Runtime["fixture"];
  const written = JSON.parse(fs.readFileSync(path.join(directory, "runtime.json"), "utf8"));
  return { artifacts, dist, portalOrigin, idpOrigin: written.idp_origin, code: written.authorization_code, fixture };
}

let shared: BrowserContext;
let storageSnapshots: { when: string; snapshot: StorageSnapshot }[] = [];
let axeResults: { when: string; result: AxeResult }[] = [];
const observation: TargetObservation = { thirdPartyRequests: [], syntheticAuthorizeHits: 0 };

test.describe("portal session journey", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async ({ browser }) => {
    // ONE context for the whole journey: the session earned by the login step is the one
    // the later steps spend (a fresh context per test would prove nothing about the slice).
    shared = await browser.newContext();
  });
  test.afterAll(async () => {
    await shared.close();
  });

  async function install(): Promise<void> {
    const config = runtime();
    await interceptIdp(
      shared,
      { portalOrigin: config.portalOrigin, idpOrigin: config.idpOrigin, code: config.code },
      observation,
    );
  }

  test("signed-out portal renders, scans clean and operates by keyboard", async () => {
    const config = runtime();
    await install();
    const page = await shared.newPage();
    await page.goto(`${config.portalOrigin}/`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      config.fixture.expectations.signed_out_heading,
    );
    axeResults.push({ when: "signed-out", result: await scanAxe(page) });
    const focused = await tabTo(page, config.fixture.expectations.sign_in_label);
    await assertFocusIsVisible(focused);
    storageSnapshots.push({ when: "signed-out", snapshot: await readStorage(page) });
    await page.close();
  });

  test("login completes through the stub exchange into a staff session", async () => {
    const config = runtime();
    await install();
    const page = await shared.newPage();
    await page.goto(`${config.portalOrigin}/`);
    await page.getByRole("link", { name: config.fixture.expectations.sign_in_label }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      config.fixture.expectations.signed_in_heading,
      { timeout: 20_000 },
    );
    await expect(page.getByText("Sessão ativa")).toBeVisible();
    const session = await page.evaluate(async () => {
      const response = await fetch("/api/v1/portal/session");
      return { status: response.status, body: await response.json() };
    });
    expect(session.status).toBe(200);
    expect(session.body.audience).toBe(config.fixture.audience);
    expect(session.body.roles).toEqual(config.fixture.roles);
    // D3: no PHI in URLs — the callback's code/state must not survive into the address bar.
    if (new URL(page.url()).search !== "") {
      throw new Error(`the browser kept a query string after login: ${page.url()}`);
    }
    await page.close();
  });

  test("the earned session spends, storage holds nothing and WCAG scans clean", async () => {
    const config = runtime();
    await install();
    const page = await shared.newPage();
    // The SAME context: the session earned by the login step authorizes this one.
    await page.goto(`${config.portalOrigin}/`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      config.fixture.expectations.signed_in_heading,
      { timeout: 20_000 },
    );
    storageSnapshots.push({ when: "signed-in", snapshot: await readStorage(page) });
    axeResults.push({ when: "signed-in", result: await scanAxe(page) });
    await expect(page.locator("[role='status']")).toHaveCount(1);
    const signOut = await tabTo(page, config.fixture.expectations.sign_out_label);
    await assertFocusIsVisible(signOut);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      config.fixture.expectations.signed_out_again_heading,
      { timeout: 20_000 },
    );
    storageSnapshots.push({ when: "signed-out-again", snapshot: await readStorage(page) });
    for (const entry of storageSnapshots) {
      assertNoPersistedState(entry.snapshot, config.fixture.synthetic_phi_markers);
    }
    if (observation.thirdPartyRequests.length > 0) {
      throw new Error(`unexpected third-party requests: ${observation.thirdPartyRequests.join(", ")}`);
    }
    if (observation.syntheticAuthorizeHits !== 1) {
      throw new Error(`expected exactly one synthetic authorize hop, saw ${observation.syntheticAuthorizeHits}`);
    }
    const evidence = {
      schema: "portal-journey-evidence.v1",
      storage_snapshots: storageSnapshots,
      axe: axeResults,
      origin: observation,
      digests: {
        index_html: sha256(fs.readFileSync(path.join(config.dist, "index.html"), "utf8")),
        storage: sha256(JSON.stringify(storageSnapshots)),
        axe: sha256(JSON.stringify(axeResults)),
        requests: sha256(JSON.stringify(observation)),
      },
      outstanding: {
        voiceover_human_pass: "NOT PERFORMED — required by ADR-0049 D10, human-only",
        engine_committed_legs: "NOT CLAIMED — custody lane (run_portal_journey.py --leg journey)",
      },
    };
    fs.writeFileSync(
      path.join(config.artifacts, "journey-evidence.json"),
      JSON.stringify(evidence, null, 2) + "\n",
    );
    await page.close();
  });
});

export type { Page };

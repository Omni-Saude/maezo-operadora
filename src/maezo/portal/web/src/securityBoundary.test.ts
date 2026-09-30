// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const productionSources = ["src/App.tsx", "src/main.tsx", "src/sessionClient.ts"]
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");
const taskReadSources = ["src/EmployeeQueues.tsx", "src/taskReadClient.ts"]
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");

// DL-0050: the hand-off summary is classified PHI in this repository, so the two files that fetch
// and render it get the same browser fence as every other clinical surface.
const contextSources = ["src/TaskContextPanel.tsx", "src/contextClient.ts"]
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");

const decisionSources = ["src/DecisionWorkspace.tsx", "src/decisionClient.ts", "src/decisionSchema.ts"]
  .map((path) => readFileSync(path, "utf8")).join("\n");

describe("fronteira do navegador", () => {
  it.each(["localStorage", "sessionStorage", "indexedDB", "serviceWorker", "sendBeacon", "analytics", "Authorization", "Bearer ", "console.", "actor_id", "tenant_id", "human_approved"])("não inclui %s na decisão humana", (forbidden) => {
    expect(decisionSources).not.toContain(forbidden);
  });
  it.each([
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "serviceWorker",
    "navigator.storage",
    "caches.open",
    "sendBeacon",
    "analytics",
    "Authorization",
    "Bearer ",
    "console.",
  ])("não inclui %s no código de produção", (forbidden) => {
    expect(productionSources).not.toContain(forbidden);
  });

  it("mantém chamadas da sessão em same-origin e no-store", () => {
    expect(productionSources.match(/credentials: "same-origin"/g)).toHaveLength(2);
    expect(productionSources.match(/cache: "no-store"/g)).toHaveLength(2);
  });

  it.each([
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "serviceWorker",
    "navigator.storage",
    "caches.open",
    "sendBeacon",
    "analytics",
    "Authorization",
    "Bearer ",
    "console.",
    "actor_id",
    "tenant_id",
    "human_approved",
  ])("não inclui %s na leitura de tarefas", (forbidden) => {
    expect(taskReadSources).not.toContain(forbidden);
  });

  it.each([
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "serviceWorker",
    "navigator.storage",
    "caches.open",
    "sendBeacon",
    "analytics",
    "Authorization",
    "Bearer ",
    "console.",
    "actor_id",
    "tenant_id",
    "human_approved",
    "dangerouslySetInnerHTML",
    "innerHTML",
    "document.cookie",
  ])("não inclui %s no contexto do caso", (forbidden) => {
    expect(contextSources).not.toContain(forbidden);
  });

  it("mantém a leitura do contexto em same-origin, no-store, sem redirecionamento e sem corpo", () => {
    expect(contextSources.match(/credentials: "same-origin"/g)).toHaveLength(1);
    expect(contextSources.match(/cache: "no-store"/g)).toHaveLength(1);
    expect(contextSources.match(/redirect: "error"/g)).toHaveLength(1);
    expect(contextSources.match(/method: "GET"/g)).toHaveLength(1);
    expect(contextSources).not.toMatch(/\bbody:/);
  });

  it("mantém a leitura em same-origin/no-store e não converte decimais exatos", () => {
    expect(taskReadSources.match(/credentials: "same-origin"/g)).toHaveLength(1);
    expect(taskReadSources.match(/cache: "no-store"/g)).toHaveLength(1);
    expect(taskReadSources).not.toMatch(/parseInt\(|parseFloat\(|Number\(/);
  });
});

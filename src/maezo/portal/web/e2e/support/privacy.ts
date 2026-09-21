/*
 * ADR-0049 D3 storage assertions for the browser journey (WP-J1-10).
 *
 * "Sem PHI em URLs, analytics de browser, armazenamento persistente do navegador ou caches
 * offline" and "Não enviar tokens, dossiê ou estado de aprovação a
 * localStorage/IndexedDB/service-worker cache". This module reads the browser storage the
 * journey actually produced and asserts it holds nothing at all — the strongest form of the
 * rule for this slice — and, belt to that braces, that nothing it could hold matches a
 * synthetic PHI marker or a token shape.
 */

import { createHash } from "node:crypto";

import type { Page } from "@playwright/test";

export type StorageSnapshot = {
  localStorage: Record<string, string>;
  sessionStorage: Record<string, string>;
  indexedDatabases: string[];
  documentCookie: string;
  url: string;
  serviceWorkerRegistrations: string[];
};

export async function readStorage(page: Page): Promise<StorageSnapshot> {
  const webStorage = await page.evaluate(() => ({
    localStorage: Object.fromEntries(
      Array.from({ length: window.localStorage.length }, (_, index) => {
        const key = window.localStorage.key(index);
        return [key ?? "", key === null ? "" : (window.localStorage.getItem(key) ?? "")];
      }),
    ),
    sessionStorage: Object.fromEntries(
      Array.from({ length: window.sessionStorage.length }, (_, index) => {
        const key = window.sessionStorage.key(index);
        return [key ?? "", key === null ? "" : (window.sessionStorage.getItem(key) ?? "")];
      }),
    ),
    documentCookie: document.cookie,
    url: window.location.href,
  }));
  const indexedDatabases = await page.evaluate(async () => {
    if (!("databases" in indexedDB)) return ["indexedDB.databases unavailable"];
    return (await indexedDB.databases()).map((database) => database.name ?? "(unnamed)");
  });
  const serviceWorkerRegistrations = await page.evaluate(async () => {
    if (!("serviceWorker" in navigator)) return [];
    const registrations = await navigator.serviceWorker.getRegistrations();
    return registrations.map((registration) => registration.scope);
  });
  return {
    localStorage: webStorage.localStorage,
    sessionStorage: webStorage.sessionStorage,
    indexedDatabases,
    documentCookie: webStorage.documentCookie,
    url: webStorage.url,
    serviceWorkerRegistrations,
  };
}

export function assertNoPersistedState(
  snapshot: StorageSnapshot,
  markers: Record<string, string>,
): void {
  const held = {
    ...snapshot.localStorage,
    ...snapshot.sessionStorage,
  };
  const blobs = Object.values(held);
  // D3, strongest form: this slice persists nothing in browser storage.
  if (blobs.length > 0) {
    throw new Error(`browser storage held ${blobs.length} entr(ies): ${Object.keys(held).join(", ")}`);
  }
  if (snapshot.indexedDatabases.length > 0) {
    throw new Error(`indexedDB databases present: ${snapshot.indexedDatabases.join(", ")}`);
  }
  if (snapshot.serviceWorkerRegistrations.length > 0) {
    throw new Error(`service workers registered: ${snapshot.serviceWorkerRegistrations.join(", ")}`);
  }
  // Belt: even a future non-empty storage must never carry a PHI marker or a token shape.
  for (const blob of blobs) {
    for (const [label, marker] of Object.entries(markers)) {
      if (marker && blob.includes(marker)) {
        throw new Error(`synthetic PHI marker "${label}" reached browser storage`);
      }
    }
    if (/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\./.test(blob)) {
      throw new Error("JWT-shaped value reached browser storage");
    }
  }
  if (snapshot.documentCookie.length > 0) {
    throw new Error(`document.cookie is readable: ${snapshot.documentCookie}`);
  }
}

export function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

/*
 * ADR-0049 D10 acceptance checks the synthetic browser can make by itself (WP-J1-10).
 *
 * "Aceitação WCAG 2.2 AA com scans, teclado e VoiceOver real ... Scan automatizado sozinho
 * não certifica conformidade." This module runs the automated part — an axe-core scan of
 * the rendered states plus keyboard operation and focus-visibility assertions — and the
 * journey report records that the human VoiceOver pass is still OUTSTANDING; no result
 * here claims that certification.
 */

import type { Locator, Page } from "@playwright/test";

import { AXE_PATH } from "./portal-target";

export type AxeResult = {
  violations: { id: string; impact: string | null; nodes: number }[];
  passes: number;
  url: string;
};

export async function scanAxe(page: Page): Promise<AxeResult> {
  // Same-origin injection: the audited page CSP is `script-src 'self'` and stays untouched.
  await page.addScriptTag({ url: AXE_PATH });
  const result = (await page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run(options?: object): Promise<object> } }).axe;
    const summary = (await axe.run({
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
      },
    })) as {
      violations: { id: string; impact: string | null; nodes: unknown[] }[];
      passes: { id: string }[];
      url: string;
    };
    return {
      violations: summary.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        nodes: violation.nodes.length,
      })),
      passes: summary.passes.length,
      url: summary.url,
    };
  })) as AxeResult;
  if (result.violations.length > 0) {
    const rendered = result.violations
      .map((violation) => `${violation.id}(${violation.impact})x${violation.nodes}`)
      .join(", ");
    throw new Error(`axe WCAG 2.2 AA violations on ${result.url}: ${rendered}`);
  }
  return result;
}

export async function assertFocusIsVisible(target: Locator): Promise<void> {
  const visible = await target.evaluate((element) => {
    const style = window.getComputedStyle(element);
    const outline = `${style.outlineStyle} ${style.outlineWidth}`;
    const ring = style.boxShadow !== "none" ? style.boxShadow : "";
    const decoration =
      style.textDecorationLine !== "none" ? style.textDecorationLine : "";
    const signal = [outline, ring, decoration].filter((part) => part && part !== "none").join(" | ");
    const rect = element.getBoundingClientRect();
    return {
      signal,
      obscured:
        rect.width === 0 ||
        rect.height === 0 ||
        style.visibility === "hidden" ||
        style.display === "none",
    };
  });
  if (visible.obscured) {
    throw new Error("keyboard focus landed on an invisible or non-rendered element");
  }
  if (!visible.signal) {
    throw new Error("focused element exposes no outline, ring or decoration (WCAG 2.4.7)");
  }
}

export async function tabTo(page: Page, label: string): Promise<Locator> {
  const target = page.getByRole("link", { name: label }).or(page.getByRole("button", { name: label }));
  for (let step = 0; step < 12; step += 1) {
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    const description = await focused.evaluate((element) => {
      const text = (element.textContent ?? "").trim();
      return element.getAttribute("aria-label") ?? text;
    });
    if (description === label) return focused;
  }
  throw new Error(`keyboard never reached "${label}" within 12 stops`);
}

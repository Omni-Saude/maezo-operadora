import { useEffect } from "react";

// F-104 (R-089, WCAG 2.4.2 Page Titled): the static `<title>` in index.html only names the
// application. Each view names its own topic first so a browser tab, history entry or screen
// reader announcement says WHERE the user is. Titles carry area names only — never a case,
// document or principal reference, which would leak into history and shared screens.

export const PORTAL_TITLE = "Portal Maezo";

export function pageTitle(...parts: readonly string[]): string {
  return [...parts, PORTAL_TITLE].join(" · ");
}

/** Sets `document.title` while the calling view is shown; `null` leaves it to a child view. */
export function usePageTitle(title: string | null): void {
  useEffect(() => {
    if (title !== null) document.title = title;
  }, [title]);
}

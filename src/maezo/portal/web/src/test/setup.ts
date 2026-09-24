import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  // Routed areas write the address bar; every test starts from the portal root.
  if (typeof window !== "undefined") window.history.replaceState(null, "", "/");
});

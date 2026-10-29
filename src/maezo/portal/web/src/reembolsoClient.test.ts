import { describe, expect, it } from "vitest";
import { anexoUrl, formatarCentavos, lerReais } from "./reembolsoClient";

describe("reembolsoClient (DL-0159)", () => {
  it("formata centavos sem passar por float", () => {
    expect(formatarCentavos("120050")).toBe("R$ 1.200,50");
    expect(formatarCentavos("5")).toBe("R$ 0,05");
    expect(formatarCentavos("9007199254740993")).toBe("R$ 90.071.992.547.409,93");
    expect(formatarCentavos(null)).toBe("—");
    expect(formatarCentavos("-1")).toBe("—");
  });

  it("le reais digitados como centavos canonicos", () => {
    expect(lerReais("150")).toBe("15000");
    expect(lerReais("150,5")).toBe("15050");
    expect(lerReais("R$ 1.200,00")).toBe("120000");
    expect(lerReais("0,99")).toBe("99");
    expect(lerReais("0")).toBeNull();
    expect(lerReais("abc")).toBeNull();
    expect(lerReais("1.2.3")).toBeNull();
  });

  it("so monta o endereco do anexo para uma referencia fechada", () => {
    const ref = `rdoc_${"a".repeat(32)}`;
    expect(anexoUrl("task-1", ref)).toBe(`/api/v1/portal/tasks/task-1/reembolso/anexos/${ref}`);
    expect(anexoUrl("task-1", "../segredo")).toBeNull();
    expect(anexoUrl("task 1", ref)).toBeNull();
  });
});

import document from "../openapi.json";
import { timestampMicroseconds } from "./taskReadTime";

// Consume the reviewed OpenAPI artifact. This checks wire shape, never business
// rules or activation: canonical conditional validators remain server-owned.
export type Schema = {
  $ref?: string; type?: string; const?: unknown; enum?: unknown[];
  anyOf?: Schema[]; oneOf?: Schema[]; properties?: Record<string, Schema>;
  required?: string[]; additionalProperties?: boolean; items?: Schema;
  prefixItems?: Schema[]; minItems?: number; maxItems?: number;
  minLength?: number; maxLength?: number; pattern?: string; format?: string;
  minimum?: number; maximum?: number; discriminator?: { mapping: Record<string, string> };
};
const schemas = document.components.schemas as unknown as Record<string, Schema>;
export function resolveSchema(schema: Schema): Schema {
  return schema.$ref ? schemas[schema.$ref.replace("#/components/schemas/", "")] ?? {} : schema;
}
export function wireSchema(name: string): Schema { return schemas[name] ?? {}; }
const supportedKeys = new Set(["$ref", "type", "const", "enum", "anyOf", "oneOf", "properties", "required", "additionalProperties", "items", "prefixItems", "minItems", "maxItems", "minLength", "maxLength", "pattern", "format", "minimum", "maximum", "discriminator", "default", "description", "title"]);
export function validWire(value: unknown, raw: Schema): boolean {
  const s = resolveSchema(raw);
  if (Object.keys(s).some((key) => !supportedKeys.has(key))) return false;
  if (s.anyOf) return s.anyOf.some((choice) => validWire(value, choice));
  if (s.oneOf) return s.oneOf.filter((choice) => validWire(value, choice)).length === 1;
  if ("const" in s && value !== s.const) return false;
  if (s.enum && !s.enum.includes(value)) return false;
  if (s.type === "null") return value === null;
  if (s.type === "boolean") return typeof value === "boolean";
  if (s.type === "integer") return typeof value === "number" && Number.isSafeInteger(value) &&
    (s.minimum === undefined || value >= s.minimum) && (s.maximum === undefined || value <= s.maximum);
  if (s.type === "string") {
    if (typeof value !== "string") return false;
    if (s.minLength !== undefined && [...value].length < s.minLength) return false;
    if (s.maxLength !== undefined && [...value].length > s.maxLength) return false;
    if (s.pattern) {
      // Python OpaqueRef uses Unicode whitespace; JS \\s differs at e.g. U+0085.
      if (s.pattern.includes("\\s") && /[\p{White_Space}\u0000-\u001f\u007f/?#\ud800-\udfff]/u.test(value)) return false;
      if (!s.pattern.includes("\\s") && new RegExp(s.pattern).exec(value)?.[0] !== value) return false;
    }
    return s.format !== "date-time" || timestampMicroseconds(value) !== null;
  }
  if (s.type === "array") return Array.isArray(value) &&
    (s.minItems === undefined || value.length >= s.minItems) &&
    (s.maxItems === undefined || value.length <= s.maxItems) &&
    value.every((item, i) => validWire(item, s.prefixItems?.[i] ?? s.items ?? {}));
  if (s.type === "object") {
    if (typeof value !== "object" || value === null || Array.isArray(value) || !s.properties) return false;
    const record = value as Record<string, unknown>;
    return (s.required ?? []).every((key) => Object.hasOwn(record, key)) &&
      Object.entries(record).every(([key, item]) => Object.hasOwn(s.properties!, key) && validWire(item, s.properties![key]));
  }
  return false; // Unknown schema shapes cannot silently expand browser authority.
}
export function formSchema(kind: string): Schema | null {
  const ref = schemas.BrowserTaskDecision?.properties?.inputs.discriminator?.mapping[kind];
  return ref ? resolveSchema({ $ref: ref }) : null;
}
export function fieldSchema(raw: Schema): Schema {
  const s = resolveSchema(raw);
  return resolveSchema(s.anyOf?.find((choice) => choice.type !== "null") ?? s);
}

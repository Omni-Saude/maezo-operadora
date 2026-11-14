// F-155 (R-089): a prepared download is saved with an extension, so the operating system
// can open it. The PHI plane answers every document as `application/octet-stream` and
// carries no filename by design (portal/api/document_phi.py: a name would be metadata PHI),
// so the extension is read from the content's own signature — never from a reference or
// from anything the user typed. Unknown content stays a generic binary file.

const signatures: readonly Readonly<{ bytes: readonly number[]; extension: string }>[] = [
  { bytes: [0x25, 0x50, 0x44, 0x46, 0x2d], extension: "pdf" }, // %PDF-
  { bytes: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], extension: "png" },
  { bytes: [0xff, 0xd8, 0xff], extension: "jpg" },
];

const BASE_NAME = "documento";
const FALLBACK_EXTENSION = "bin";
const PROBE_LENGTH = Math.max(...signatures.map((signature) => signature.bytes.length));

async function leadingBytes(blob: Blob): Promise<Uint8Array> {
  return new Uint8Array(await blob.slice(0, PROBE_LENGTH).arrayBuffer());
}

export async function downloadFileName(blob: Blob): Promise<string> {
  let head: Uint8Array;
  try {
    head = await leadingBytes(blob);
  } catch {
    return `${BASE_NAME}.${FALLBACK_EXTENSION}`;
  }
  const match = signatures.find(({ bytes }) => bytes.every((byte, index) => head[index] === byte));
  return `${BASE_NAME}.${match?.extension ?? FALLBACK_EXTENSION}`;
}

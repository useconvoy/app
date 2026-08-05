/**
 * The evidence zip encoder: correct CRC-32, correct structure bytes, and a
 * real round trip through Python's zipfile, which validates local headers,
 * the central directory, and the end record for us.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildZip, crc32 } from "@/lib/evidence/zip";

const encoder = new TextEncoder();

describe("crc32", () => {
  it("matches the standard check value", () => {
    // The canonical CRC-32 test vector.
    expect(crc32(encoder.encode("123456789"))).toBe(0xcbf43926);
  });

  it("handles empty input", () => {
    expect(crc32(new Uint8Array(0))).toBe(0);
  });
});

describe("buildZip", () => {
  const entries = [
    { name: "manifest.json", data: encoder.encode('{"hello":"world"}') },
    { name: "events.ndjson", data: encoder.encode('{"seq":1}\n{"seq":2}\n') },
  ];

  it("starts with a local header and ends with the end-of-directory record", () => {
    const zip = buildZip(entries);
    // Local file header signature PK\x03\x04.
    expect([...zip.slice(0, 4)]).toEqual([0x50, 0x4b, 0x03, 0x04]);
    // End of central directory signature PK\x05\x06, 22 bytes from the end.
    expect([...zip.slice(zip.length - 22, zip.length - 18)]).toEqual([0x50, 0x4b, 0x05, 0x06]);
  });

  it("round-trips through Python's zipfile", () => {
    const dir = mkdtempSync(join(tmpdir(), "evidence-zip-"));
    const path = join(dir, "binder.zip");
    writeFileSync(path, buildZip(entries));

    const output = execFileSync(
      "python3",
      [
        "-c",
        [
          "import json, sys, zipfile",
          "archive = zipfile.ZipFile(sys.argv[1])",
          "bad = archive.testzip()",
          "assert bad is None, bad",
          "print(json.dumps({name: archive.read(name).decode() for name in archive.namelist()}))",
        ].join("\n"),
        path,
      ],
      { encoding: "utf8" },
    );
    const contents = JSON.parse(output) as Record<string, string>;
    expect(Object.keys(contents)).toEqual(["manifest.json", "events.ndjson"]);
    expect(contents["manifest.json"]).toBe('{"hello":"world"}');
    expect(contents["events.ndjson"]).toBe('{"seq":1}\n{"seq":2}\n');
  });
});

/**
 * A minimal ZIP encoder for the evidence binder. STORE method only:
 * evidence files are small JSON/NDJSON and an uncompressed archive keeps
 * this dependency-free and byte-auditable. Layout per the PKWARE APPNOTE:
 * local file headers + data, then the central directory, then the end
 * record. CRC-32 is the standard reflected polynomial, table-driven.
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i += 1) {
    crc = CRC_TABLE[(crc ^ data[i]!) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export interface ZipEntry {
  /** Path inside the archive; forward slashes only. */
  name: string;
  data: Uint8Array;
}

/** MS-DOS date/time pair for a timestamp (ZIP's native stamp format). */
function dosDateTime(at: Date): { date: number; time: number } {
  const year = Math.max(at.getFullYear(), 1980);
  return {
    date: ((year - 1980) << 9) | ((at.getMonth() + 1) << 5) | at.getDate(),
    time: (at.getHours() << 11) | (at.getMinutes() << 5) | Math.floor(at.getSeconds() / 2),
  };
}

function writeU16(target: Uint8Array, offset: number, value: number): void {
  target[offset] = value & 0xff;
  target[offset + 1] = (value >>> 8) & 0xff;
}

function writeU32(target: Uint8Array, offset: number, value: number): void {
  target[offset] = value & 0xff;
  target[offset + 1] = (value >>> 8) & 0xff;
  target[offset + 2] = (value >>> 16) & 0xff;
  target[offset + 3] = (value >>> 24) & 0xff;
}

/** Build a complete STORE-only archive from the entries, in order. */
export function buildZip(entries: ZipEntry[], now: Date = new Date()): Uint8Array {
  const encoder = new TextEncoder();
  const { date, time } = dosDateTime(now);

  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const crc = crc32(entry.data);

    const local = new Uint8Array(30 + name.length + entry.data.length);
    writeU32(local, 0, 0x04034b50); // local file header signature
    writeU16(local, 4, 20); // version needed: 2.0
    writeU16(local, 6, 0); // flags
    writeU16(local, 8, 0); // method: STORE
    writeU16(local, 10, time);
    writeU16(local, 12, date);
    writeU32(local, 14, crc);
    writeU32(local, 18, entry.data.length); // compressed size (= stored)
    writeU32(local, 22, entry.data.length); // uncompressed size
    writeU16(local, 26, name.length);
    writeU16(local, 28, 0); // extra length
    local.set(name, 30);
    local.set(entry.data, 30 + name.length);
    locals.push(local);

    const central = new Uint8Array(46 + name.length);
    writeU32(central, 0, 0x02014b50); // central directory signature
    writeU16(central, 4, 20); // version made by
    writeU16(central, 6, 20); // version needed
    writeU16(central, 8, 0); // flags
    writeU16(central, 10, 0); // method: STORE
    writeU16(central, 12, time);
    writeU16(central, 14, date);
    writeU32(central, 16, crc);
    writeU32(central, 20, entry.data.length);
    writeU32(central, 24, entry.data.length);
    writeU16(central, 28, name.length);
    writeU16(central, 30, 0); // extra length
    writeU16(central, 32, 0); // comment length
    writeU16(central, 34, 0); // disk number
    writeU16(central, 36, 0); // internal attributes
    writeU32(central, 38, 0); // external attributes
    writeU32(central, 42, offset); // local header offset
    central.set(name, 46);
    centrals.push(central);

    offset += local.length;
  }

  const centralSize = centrals.reduce((sum, c) => sum + c.length, 0);
  const end = new Uint8Array(22);
  writeU32(end, 0, 0x06054b50); // end of central directory signature
  writeU16(end, 4, 0); // this disk
  writeU16(end, 6, 0); // central directory disk
  writeU16(end, 8, entries.length);
  writeU16(end, 10, entries.length);
  writeU32(end, 12, centralSize);
  writeU32(end, 16, offset); // central directory offset
  writeU16(end, 20, 0); // comment length

  const total = offset + centralSize + end.length;
  const zip = new Uint8Array(total);
  let cursor = 0;
  for (const chunk of [...locals, ...centrals, end]) {
    zip.set(chunk, cursor);
    cursor += chunk.length;
  }
  return zip;
}

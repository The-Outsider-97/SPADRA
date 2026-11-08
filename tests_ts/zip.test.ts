import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { crc32, zipStore } from "../src/client/zip.js";

test("CRC-32 matches the standard check value", () => {
  assert.equal(crc32(new TextEncoder().encode("123456789")), 0xcbf43926);
  assert.equal(crc32(new Uint8Array()), 0);
});

test("a ZIP made here is read back byte for byte by Python's zipfile, which also verifies every CRC", () => {
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, ...Array.from({ length: 300 }, (_, i) => (i * 7) % 256)]);
  const entries = [{ name: "frame_0001.png", data: png }, { name: "frame_0002.png", data: png.slice().reverse() }, { name: "readme é.txt", data: new TextEncoder().encode("hello") }];
  const dir = mkdtempSync(join(tmpdir(), "zip-"));
  const file = join(dir, "t.zip");
  writeFileSync(file, zipStore(entries, new Date(2026, 10, 2, 13, 55, 30)));
  const script = "import sys, zipfile\nz = zipfile.ZipFile(sys.argv[1])\nassert z.testzip() is None\nsys.stdout.buffer.write('|'.join(f'{i.filename}:{len(z.read(i))}' for i in z.infolist()).encode('utf-8'))\n";
  const out = execFileSync("python3", ["-I", "-c", script, file]).toString().trim();
  assert.equal(out, `frame_0001.png:${png.length}|frame_0002.png:${png.length}|readme é.txt:5`);
  const check = "import sys, zipfile\nz = zipfile.ZipFile(sys.argv[1])\nsys.stdout.buffer.write(z.read('frame_0002.png'))\n";
  assert.deepEqual(new Uint8Array(execFileSync("python3", ["-I", "-c", check, file])), png.slice().reverse());
  assert.ok(readFileSync(file).length > 0);
});

test("an empty ZIP is valid and the same input gives the same bytes", () => {
  assert.equal(zipStore([]).length, 22);
  const e = [{ name: "a.bin", data: new Uint8Array([1, 2, 3]) }];
  const d = new Date(2026, 0, 1, 0, 0, 0);
  assert.deepEqual(zipStore(e, d), zipStore(e, d));
});

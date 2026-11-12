// Windows-only repair of `next build` output in export mode (Next.js 16.4.6).
//
// The client router prefetches a page's segment payload from a flat file name,
// `/soita/__next.soita.__PAGE__.txt`. The exporter builds that name by replacing "/" in a
// path made with `path.relative`, which uses "\" on Windows, so there it writes
// `soita/__next.soita/__PAGE__.txt` instead and every prefetch answers 404 (navigation still
// works through the full payload). On Linux, where the image is built, the names are already
// flat and this script changes nothing.
import fs from "node:fs";
import path from "node:path";

const outDir = path.resolve(process.argv[2] ?? "out");
if (!fs.existsSync(outDir)) {
  console.error(`flatten-export-segments: ${outDir} does not exist`);
  process.exit(1);
}

let moved = 0;

function filesUnder(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? filesUnder(full) : [full];
  });
}

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const full = path.join(dir, entry.name);
    if (!entry.name.startsWith("__next.")) {
      if (entry.name !== "_next") walk(full);
      continue;
    }
    for (const file of filesUnder(full)) {
      const flat = path.relative(full, file).split(path.sep).join(".");
      fs.renameSync(file, path.join(dir, `${entry.name}.${flat}`));
      moved += 1;
    }
    fs.rmSync(full, { recursive: true });
  }
}

walk(outDir);
console.log(`flatten-export-segments: ${moved} file(s) renamed`);

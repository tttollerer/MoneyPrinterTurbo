#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';
import {validateManifest} from './validate.mjs';
const root = path.dirname(fileURLToPath(import.meta.url));
const emit = obj => process.stdout.write(`${JSON.stringify(obj)}\n`);
try {
  const [input, output] = process.argv.slice(2);
  if (!input || !output) throw new Error('Usage: node render.mjs <manifest.json> <output.mp4>');
  const manifest = JSON.parse(await fs.readFile(input, 'utf8'));
  const assets = validateManifest(manifest);
  for (const asset of assets) {
    const response = await fetch(asset.url, {method:'HEAD', signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error(`Asset unavailable: ${asset.name} (${response.status})`);
  }
  emit({progress:0});
  const serveUrl = await bundle({entryPoint:path.join(root,'src/index.jsx'), onProgress:p=>emit({progress:p/100*.1})});
  const inputProps = {manifest};
  const composition = await selectComposition({serveUrl,id:'SpotForge',inputProps});
  await fs.mkdir(path.dirname(path.resolve(output)),{recursive:true});
  await renderMedia({serveUrl,composition,inputProps,codec:'h264',audioCodec:'aac',pixelFormat:'yuv420p',outputLocation:output,concurrency:1,onProgress:({progress})=>emit({progress:.1+progress*.9})});
  emit({output:path.resolve(output),progress:1});
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}

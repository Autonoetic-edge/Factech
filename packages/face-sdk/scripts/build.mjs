import { build } from 'esbuild';
import { stat } from 'node:fs/promises';

const minify = process.argv.includes('--min');

async function bundle(outfile, min, entry = 'src/index.ts') {
  const result = await build({
    entryPoints: [entry],
    outfile,
    bundle: true,
    format: 'esm',
    platform: 'browser',
    target: 'es2022',

    resolveExtensions: ['.ts', '.js'],
    sourcemap: true,
    minify: min,
    legalComments: 'external',
    minifyWhitespace: true,
    metafile: true,
  });
  const { size } = await stat(outfile);
  const external = Object.keys(result.metafile.inputs).filter((p) => p.includes('node_modules'));
  return { outfile, size, external };
}

const built = [await bundle('dist/index.js', false)];
if (minify) built.push(await bundle('dist/index.min.js', true));

built.push(await bundle('dist/internal.js', false, 'src/internal.ts'));

for (const b of built) {
  console.log(`${b.outfile}  ${(b.size / 1024).toFixed(1)} KiB`);
  if (b.external.length) {
    console.error('runtime dependency leaked into the bundle: ' + b.external.join(', '));
    process.exitCode = 1;
  }
}

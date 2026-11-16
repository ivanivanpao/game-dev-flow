// 用法：node lab/build_lab.mjs <模型名稱> [輸出路徑]
// 把 lab/lab.js ＋ models/<名稱>/model.js ＋ three.js 打包成「一個可直接雙擊開啟的 HTML」
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const name = process.argv[2];
if (!name) { console.error('請給模型名稱'); process.exit(2); }
const modelPath = path.join(root, 'models', name, 'model.js');
if (!fs.existsSync(modelPath)) { console.error('找不到 ' + modelPath); process.exit(2); }
const out = process.argv[3] || path.join(root, 'workspace', 'lab', name + '.html');
fs.mkdirSync(path.dirname(out), { recursive: true });
const r = await build({
  entryPoints: [path.join(root, 'lab', 'lab.js')], bundle: true, minify: true, format: 'iife', write: false, logLevel: 'warning',
  alias: { 'model-under-test': modelPath }, absWorkingDir: root,
});
const js = r.outputFiles[0].text.replaceAll('</script', '<\\/script');
const html = fs.readFileSync(path.join(root, 'lab', 'lab.template.html'), 'utf8').replace('/*__BUNDLE__*/', () => js);
fs.writeFileSync(out, html);
console.log('完成：' + out);

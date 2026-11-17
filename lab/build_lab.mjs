// 用法：node lab/build_lab.mjs <模型資料夾路徑> [輸出路徑]
//   例如在模型資料夾裡：node <工具>/lab/build_lab.mjs . 輸出.html
// 把 lab/lab.js ＋ <模型資料夾>/model.js ＋ three.js 打包成「一個可直接雙擊開啟的 HTML」
// 不寫輸出路徑時，放在專案的 workspace/lab/<模型名稱>.html（模型資料夾是 projects/<專案>/models/<名稱>/）
import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (!process.argv[2]) { console.error('請給模型資料夾路徑'); process.exit(2); }
const modelDir = path.resolve(process.argv[2]);
const name = path.basename(modelDir);
const modelPath = path.join(modelDir, 'model.js');
if (!fs.existsSync(modelPath)) { console.error('找不到 ' + modelPath); process.exit(2); }
const out = process.argv[3] ? path.resolve(process.argv[3]) : path.join(modelDir, '..', '..', 'workspace', 'lab', name + '.html');
fs.mkdirSync(path.dirname(out), { recursive: true });
const r = await build({
  entryPoints: [path.join(root, 'lab', 'lab.js')], bundle: true, minify: true, format: 'iife', write: false, logLevel: 'warning',
  alias: { 'model-under-test': modelPath }, absWorkingDir: root,
});
const js = r.outputFiles[0].text.replaceAll('</script', '<\\/script');
const html = fs.readFileSync(path.join(root, 'lab', 'lab.template.html'), 'utf8').replace('/*__BUNDLE__*/', () => js);
fs.writeFileSync(out, html);
console.log('完成：' + out);

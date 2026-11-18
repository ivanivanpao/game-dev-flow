// 架構檢查：專案的自訂檢查，掛在 flow.config.json 的 extra_checks
//
// 用法：協調器在遊戲資料夾執行 extra_checks，所以設定裡寫 node ../checks/arch-check.mjs
//      （遊戲資料夾就在專案資料夾底下時是這個路徑；也可以在後面給遊戲資料夾路徑：node arch-check.mjs <遊戲資料夾>）
//
// 只檢查「結構規則」：依賴方向、循環引用、模型從哪裡引用、main.js 只做組裝、全域變數。
// 刻意不限制檔案行數：遊戲變大程式一定變多，行數設上限只會逼 AI 亂拆檔案。檔案是不是太雜，交給程式審查者判斷。
//
// 這支放在專案的 checks/ 裡：做遊戲時 AI 開發者改不到（改之前會被擋下，事後也會還原），它沒辦法靠改規則過關。
// 想改架構：同時改下面的 RULES 和 prompt.md 的「程式架構」一節，兩邊要一致。
// 只用 Node 內建模組，遊戲資料夾不用安裝任何套件。
import fs from 'node:fs';
import path from 'node:path';

const RULES = {
  srcDir: 'src',                    // 原始碼資料夾（相對於遊戲資料夾）；不存在就掃整個遊戲資料夾
  // 分層：左邊資料夾裡的檔案，不准 import 右邊這些資料夾（都在 srcDir 底下）
  forbid: {
    core: ['entities', 'systems', 'debug'],
    entities: ['systems', 'debug'],
    systems: ['debug'],
  },
  noCycles: true,                   // 不准循環引用（A import B，B 又 import A）
  modelsEntry: 'src/models.js',     // 唯一能 import 已發布模型（models/_released/）的檔案；null＝不檢查
  mainFile: 'src/main.js',          // 只做組裝：只能 import 遊戲自己的模組，不能直接 import 套件（three 等）；null＝不檢查
  allowedGlobals: ['__game', '__renderBackend'],   // 允許掛在 window／globalThis 上的名稱
};

const SKIP = new Set(['node_modules', 'dist', 'build', '.git', 'workspace', 'coverage', '.vite', '.claude-tmp']);
const EXTS = ['.js', '.mjs', '.cjs', '.ts', '.mts', '.cts', '.jsx', '.tsx'];
const MAX_SHOW = 40;                // 最多列幾項（協調器只把輸出最後一段交給開發者）

const ROOT = path.resolve(process.argv[2] || process.cwd());
if (!fs.existsSync(ROOT) || !fs.statSync(ROOT).isDirectory()) {
  console.error('架構檢查：找不到遊戲資料夾 ' + ROOT);
  process.exit(2);
}
const SRC = path.join(ROOT, RULES.srcDir);
const SCAN = fs.existsSync(SRC) && fs.statSync(SRC).isDirectory() ? SRC : ROOT;
const rel = p => path.relative(ROOT, p).split(path.sep).join('/');
const norm = p => (process.platform === 'win32' ? path.resolve(p).toLowerCase() : path.resolve(p));   // Windows 路徑不分大小寫
const same = (a, b) => norm(a) === norm(b);

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) { if (!SKIP.has(e.name)) walk(path.join(dir, e.name), out); }
    else if (EXTS.includes(path.extname(e.name).toLowerCase())) out.push(path.join(dir, e.name));
  }
  return out;
}

// 把註解換成空白：註解裡的 import、window.x = … 不算數。保留換行與字元位置，行號才對得上；字串原樣保留
function stripComments(s) {
  let out = '', i = 0, q = null;
  while (i < s.length) {
    const c = s[i], n = s[i + 1];
    if (q) {                                            // 在字串裡：只找結束的引號，跳過跳脫字元
      out += c;
      if (c === '\\') { out += n ?? ''; i += 2; continue; }
      if (c === q) q = null;
      i++; continue;
    }
    if (c === '"' || c === "'" || c === '`') { q = c; out += c; i++; continue; }
    if (c === '/' && n === '/') { while (i < s.length && s[i] !== '\n') { out += ' '; i++; } continue; }
    if (c === '/' && n === '*') {
      const end = s.indexOf('*/', i + 2), stop = end < 0 ? s.length : end + 2;
      for (; i < stop; i++) out += s[i] === '\n' ? '\n' : ' ';
      continue;
    }
    out += c; i++;
  }
  return out;
}

// 抓出 import … from '路徑'、import '路徑'、export … from '路徑'、import('路徑')、require('路徑')
const IMPORT_RES = [
  /\bimport\s+(?:type\s+)?(?:[\w$*{}\s,]+?\s+from\s*)?(['"])([^'"\n]+)\1/g,
  /\bexport\s+(?:type\s+)?[\w$*{}\s,]*?\s*from\s*(['"])([^'"\n]+)\1/g,
  /\bimport\s*\(\s*(['"`])([^'"`\n]+)\1\s*\)/g,
  /\brequire\s*\(\s*(['"])([^'"\n]+)\1\s*\)/g,
];
// window.x = …、globalThis['x'] = …（== 比較不算）
const GLOBAL_RE = /\b(?:window|globalThis)\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*(['"`])([^'"`]+)\2\s*\])\s*=(?!=)/g;
const GLOBAL_BULK_RE = /\bObject\s*\.\s*(?:assign|defineProperty|defineProperties)\s*\(\s*(?:window|globalThis)\b/g;

const lineOf = (text, idx) => text.slice(0, idx).split('\n').length;

// 相對路徑換成實際檔案（可以省略副檔名，或指向資料夾的 index）；套件名稱回傳 null
function resolveSpec(from, spec) {
  if (!spec.startsWith('./') && !spec.startsWith('../')) return null;
  const base = path.resolve(path.dirname(from), spec.split(/[?#]/)[0]);
  const cands = [base, ...EXTS.map(e => base + e), ...EXTS.map(e => path.join(base, 'index' + e))];
  return cands.find(c => fs.existsSync(c) && fs.statSync(c).isFile()) || base;
}

// 檔案屬於 srcDir 底下哪一層（第一層資料夾名稱）；srcDir 根目錄的檔案（main.js、models.js）不屬於任何一層
function layerOf(p) {
  const r = path.relative(SRC, p);
  if (r.startsWith('..') || path.isAbsolute(r)) return null;
  const parts = r.split(path.sep);
  return parts.length > 1 ? parts[0] : null;
}

const files = walk(SCAN);
const known = new Map(files.map(f => [norm(f), f]));
const graph = new Map(files.map(f => [f, []]));
const problems = [];

for (const file of files) {
  const text = stripComments(fs.readFileSync(file, 'utf8'));
  const where = idx => `${rel(file)}:${lineOf(text, idx)}`;
  const isMain = RULES.mainFile && same(file, path.join(ROOT, RULES.mainFile));
  const isModelsEntry = RULES.modelsEntry && same(file, path.join(ROOT, RULES.modelsEntry));
  const myLayer = layerOf(file);

  for (const re of IMPORT_RES) {
    for (const m of text.matchAll(re)) {
      const spec = m[2], at = where(m.index + m[0].indexOf(spec));
      const target = resolveSpec(file, spec);
      if (!target) {
        if (isMain) problems.push(`[main.js] ${at} 直接 import 了套件 ${spec}：main.js 只做組裝，用到套件的工作（例如建立 WebGPURenderer）請放到 core/ 等模組，main.js 再 import 那個模組`);
        continue;
      }
      if (RULES.modelsEntry && target.split(path.sep).includes('_released') && !isModelsEntry)
        problems.push(`[模型] ${at} 直接 import 已發布模型（${spec}）：只能在 ${RULES.modelsEntry} 引用，其他檔案從它拿模型`);
      const tLayer = layerOf(target);
      if (myLayer && tLayer && (RULES.forbid[myLayer] || []).includes(tLayer))
        problems.push(`[分層] ${at} import 了 ${rel(target)}：${myLayer}/ 不能 import ${tLayer}/（依賴方向見 prompt.md 的「程式架構」）`);
      const t = known.get(norm(target));
      if (t) graph.get(file).push(t);
    }
  }

  for (const m of text.matchAll(GLOBAL_RE)) {
    const name = m[1] || m[3];
    if (!RULES.allowedGlobals.includes(name))
      problems.push(`[全域變數] ${where(m.index)} 掛了全域變數 ${name}：只允許 ${RULES.allowedGlobals.join('、')}；要共用的東西請用 import 傳遞`);
  }
  for (const m of text.matchAll(GLOBAL_BULK_RE))
    problems.push(`[全域變數] ${where(m.index)} 用 Object.assign／defineProperty 一次掛全域變數：看不出掛了什麼，請改成 import 傳遞`);
}

// 循環引用：深度優先走訪，遇到「還在走訪路徑上」的檔案就是一個環；同一組檔案的環只報一次
if (RULES.noCycles) {
  const state = new Map(), stack = [], cycles = new Map();
  const dfs = u => {
    state.set(u, 1); stack.push(u);
    for (const v of graph.get(u)) {
      if (state.get(v) === 1) {
        const cyc = stack.slice(stack.indexOf(v));
        const key = cyc.map(norm).sort().join('|');
        if (!cycles.has(key)) cycles.set(key, cyc);
      } else if (!state.get(v)) dfs(v);
    }
    stack.pop(); state.set(u, 2);
  };
  for (const f of files) if (!state.get(f)) dfs(f);
  for (const cyc of cycles.values())
    problems.push(`[循環引用] ${[...cyc, cyc[0]].map(rel).join(' → ')}：把共用的部分抽到下層模組，或改用事件溝通`);
}

const scope = SCAN === ROOT ? '整個遊戲資料夾' : RULES.srcDir + '/';
if (problems.length) {
  console.log(`架構檢查不通過（${problems.length} 項）：`);
  problems.slice(0, MAX_SHOW).forEach(p => console.log('- ' + p));
  if (problems.length > MAX_SHOW) console.log(`- ……還有 ${problems.length - MAX_SHOW} 項沒列出`);
  console.log(`（掃描 ${scope} 的 ${files.length} 個檔案；規則在 checks/arch-check.mjs 開頭的 RULES）`);
  process.exit(1);
}
console.log(`架構檢查通過（掃描 ${scope} 的 ${files.length} 個檔案）`);

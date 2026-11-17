"""
協調器的自動測試：改了 orchestrator.py、關卡程式或 GUI 之後跑一次，確認之前修好的行為沒被改壞。

用法（在專案根目錄）：.venv/Scripts/python.exe tests/run_tests.py

做法：
  - 在系統暫存區建一份專案副本，裡面放一個極小的假遊戲（有自己的 git）和測試用模型
  - 用「假的 claude」（tests/fake_claude.py）代替真的 Claude：不呼叫 AI、不消耗訂閱額度
  - 跑做模型、做遊戲、GUI 的實際流程，逐項印出 PASS／FAIL
  - 不會動到你真正的專案和遊戲。全部通過就刪掉副本；有失敗就保留副本並印出位置，方便查原因
需要：.venv（含 Playwright 與 Chromium）、node、git、專案根目錄已執行過 npm install
"""
import json, os, re, shutil, stat, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REAL = Path(__file__).resolve().parents[1]                  # 專案根目錄
TMP = Path(tempfile.mkdtemp(prefix='flow-test-'))           # 這次測試的暫存資料夾
SB = TMP / 'sb'                                             # 工具副本
PJ = SB / 'projects' / 't'                                  # 副本裡的測試專案（一個遊戲一個專案）
PY = str(REAL / '.venv' / 'Scripts' / 'python.exe')
LOG = TMP / 'fake-log.jsonl'      # 假 claude 的呼叫紀錄；放在副本外面，否則會被「保護檔案」機制當成偷改而還原
print('測試副本位置：', SB, flush=True)
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  ← {detail}'), flush=True)


def rm(p):
    def onerr(f, path, _):
        os.chmod(path, stat.S_IWRITE); f(path)
    if p.exists():
        shutil.rmtree(p, onerror=onerr)


def g(*a):
    r = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *a], cwd=PJ / 'game', capture_output=True, text=True, encoding='utf-8')
    return r.stdout.strip()


# ---------- 建副本 ----------
rm(SB); SB.mkdir(parents=True)
# 只複製工具本身（版控裡有的東西）；你的檔案（設定、任務、清單）由協調器第一次執行時從 templates/ 建立，跟別人剛下載工具時一樣
for f in ('orchestrator.py', 'package.json'):
    shutil.copy(REAL / f, SB / f)
for d in ('gates', 'lab', 'agents', 'templates', 'gui', 'node_modules'):
    shutil.copytree(REAL / d, SB / d)
# 測試專案：設定、任務、清單由協調器切換到專案時從 templates/ 補上（跟新專案一樣）
(PJ / 'models' / '_released').mkdir(parents=True)
(SB / 'projects' / '.current').write_text('t', 'utf-8')
(PJ / 'models' / 'axe').mkdir()
(PJ / 'models' / 'axe' / 'spec.json').write_text('{"name":"axe","checks":{},"views":[]}', 'utf-8')
# 測試模型：尺寸要求故意做不到，關卡一定不過
(PJ / 'models' / 'box').mkdir()
(PJ / 'models' / 'box' / 'model.js').write_text(
    "export const meta = { name: 'box' };\nexport function build(THREE) { const g = new THREE.Group();\n"
    "g.add(new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), new THREE.MeshStandardMaterial({ color: 0x8899aa }))); return g; }\n", 'utf-8')
(PJ / 'models' / 'box' / 'spec.md').write_text('# 方塊', 'utf-8')
(PJ / 'models' / 'box' / 'spec.json').write_text(json.dumps({'name': 'box', 'checks': {'size': [[5, 6], None, None]},
    'views': [{'name': '1-iso', 'dir': [1, 0.6, 1], 'dist': 'fit', 'describe': '方塊'}]}), 'utf-8')
(PJ / 'models' / 'box' / 'checklist.md').write_text('# 清單', 'utf-8')


def png1x1():
    """產生一張 1×1 的 PNG（測試用的參考圖）"""
    import struct, zlib
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


# 有參考圖的測試模型：沒有尺寸要求，關卡會過，才會叫到視覺審查者
(PJ / 'models' / 'refcar').mkdir()
shutil.copy(PJ / 'models' / 'box' / 'model.js', PJ / 'models' / 'refcar' / 'model.js')
(PJ / 'models' / 'refcar' / 'spec.md').write_text('# 車', 'utf-8')
(PJ / 'models' / 'refcar' / 'spec.json').write_text(json.dumps({'name': 'refcar', 'checks': {},
    'views': [{'name': '1-iso', 'dir': [1, 0.6, 1], 'dist': 'fit', 'describe': '車'}]}), 'utf-8')
(PJ / 'models' / 'refcar' / 'checklist.md').write_text('# 清單', 'utf-8')
(PJ / 'models' / 'refcar' / 'ref.png').write_bytes(png1x1())
# 實際比例的大模型（約 300×180 公尺的平板，像一條賽道）：地板要跟著放大
(PJ / 'models' / 'bigtrack').mkdir()
(PJ / 'models' / 'bigtrack' / 'model.js').write_text(
    "export const meta = { name: 'bigtrack' };\nexport function build(THREE) { const g = new THREE.Group();\n"
    "g.add(new THREE.Mesh(new THREE.BoxGeometry(300, 0.5, 180), new THREE.MeshStandardMaterial({ color: 0x556677 }))); return g; }\n", 'utf-8')
(PJ / 'models' / 'bigtrack' / 'spec.md').write_text('# 賽道', 'utf-8')
(PJ / 'models' / 'bigtrack' / 'spec.json').write_text(json.dumps({'name': 'bigtrack', 'checks': {},
    'views': [{'name': '1-top', 'dir': [0, 1, 0], 'up': [0, 0, -1], 'dist': 'fit', 'describe': '賽道'}]}), 'utf-8')
# 假的 claude
fake = SB / 'fake'; fake.mkdir()
shutil.copy(Path(__file__).resolve().parent / 'fake_claude.py', fake / 'fake_claude.py')
(fake / 'claude.cmd').write_text(f'@"{PY}" "%~dp0fake_claude.py" %*\r\n', 'mbcs')   # cmd 用系統編碼讀批次檔，路徑有中文要這樣存
# 極小的假遊戲（有自己的 git）
game = PJ / 'game'; game.mkdir()
(game / 'index.html').write_text('<!doctype html><html><body><canvas></canvas><script>window.__game={ready:true}</script></body></html>', 'utf-8')
(game / 'build.js').write_text("const fs=require('fs');fs.mkdirSync('dist',{recursive:true});fs.copyFileSync('index.html','dist/index.html');", 'utf-8')
(game / 'shots.json').write_text(json.dumps({'ready': 'window.__game && window.__game.ready', 'settle_ms': 100,
    'shots': [{'name': '01', 'wait_ms': 50, 'describe': '畫面'}]}), 'utf-8')
(game / '.gitignore').write_text('dist/\n', 'utf-8')
g('init', '-b', 'main'); g('add', '-A'); g('commit', '-m', 'init')
TPL_CFG = json.loads((REAL / 'templates' / 'flow.config.json').read_text('utf-8'))
cfg = json.loads(json.dumps(TPL_CFG))
del cfg['model_viewport']                 # 故意少一項、巢狀的也少一項：要靠範本的預設值補上
del cfg['models']['model_code']
cfg.update({'project_dir': 'game', 'build_cmd': 'node build.js', 'entry': 'dist/index.html', 'shots_spec': 'game/shots.json',
            'claude_bin': str(fake / 'claude.cmd'), 'require_webgpu': False, 'uses_models': [], 'stall_rounds': 2,
            'perf': {'target_avg_ms': None, 'target_p95_ms': None}, 'agent_timeout_sec': 120})
(PJ / 'flow.config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), 'utf-8')
ENV = dict(os.environ, FAKE_LOG=str(LOG), FAKE_SB=str(PJ), PYTHONIOENCODING='utf-8')     # FAKE_SB：假開發者偷改清單時要改的專案


def orch(*a, scenario=''):
    r = subprocess.run([PY, str(SB / 'orchestrator.py'), *a], cwd=SB, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=dict(ENV, FAKE_SCENARIO=scenario), timeout=600)
    return r.returncode, r.stdout + r.stderr


def last_game_status():
    runs = sorted(p for p in (PJ / 'workspace').iterdir() if p.is_dir() and re.match(r'\d{8}-\d{6}', p.name))
    return json.loads((runs[-1] / 'status.json').read_text('utf-8')), runs[-1]


# ---------- 單元測試（匯入副本的 orchestrator）----------
sys.path.insert(0, str(SB)); os.chdir(SB)
import orchestrator as O
O.set_project('t')

def rv(verdict, defects, body='## 我最不確定的 3 件事\n- ……'):
    """組一段審查者輸出：文字段落＋最後的 ```json 區塊"""
    return body + '\n\n```json\n' + json.dumps({'verdict': verdict, 'defects': defects}, ensure_ascii=False) + '\n```\n'


t = O.model_defects(rv('CHANGES_REQUESTED', [
    {'severity': '低', 'target': '模型', 'model': 'axe', 'problem': '小問題'},
    {'severity': '高', 'target': '模型', 'model': 'axe', 'where': '01', 'problem': '像鋤頭'},
    {'severity': '中', 'target': '遊戲', 'problem': '光線'}],
    body='## 逐條對照驗收清單\n1. [高][模型:pick] 符合\n## 我最不確定的 3 件事\n- [高][模型:pick] 不確定'))
check('#2 只算 JSON 裡 target 是模型的高/中（文字段落、「低」、遊戲的都不算）', t == {'axe': ['[高][模型:axe] 01：像鋤頭']}, t)
t = O.model_defects(rv('CHANGES_REQUESTED', [{'severity': '中', 'target': '模型', 'model': 'axe', 'where': 'a.js:3', 'problem': '程式審查者的格式'}]))
check('#2 程式審查者的缺陷也會判斷', list(t) == ['axe'], t)

# #11 審查結果的固定格式
check('#11 沒有 JSON 區塊 → NO_VERDICT（舊格式的 VERDICT 行不算）', O.verdict('## 缺陷清單\n（無）\n\nVERDICT: APPROVED') == 'NO_VERDICT')
check('#11 JSON 壞掉 → BAD_FORMAT', O.verdict('```json\n{"verdict": "APPROVED", "defects": [}\n```') == 'BAD_FORMAT')
r = O.parse_review(rv('APPROVED', [{'severity': '超高', 'problem': 'x'}]))
check('#11 嚴重度不是高/中/低 → BAD_FORMAT 並寫明原因', r['verdict'] == 'BAD_FORMAT' and 'severity' in r['error'], r)
r = O.parse_review(rv('APPROVED', [{'severity': '中', 'target': '模型', 'problem': 'x'}]))
check('#11 target 是模型卻沒寫模型名稱 → BAD_FORMAT', r['verdict'] == 'BAD_FORMAT' and 'model' in r['error'], r)
r = O.parse_review('```json\n{"verdict": "APPROVED"}\n```')
check('#11 少了 defects → BAD_FORMAT', r['verdict'] == 'BAD_FORMAT' and 'defects' in r['error'], r)
r = O.parse_review(rv('APPROVED', [{'severity': '高', 'problem': '破面'}]))
check('#11 判 APPROVED 卻列了高/中缺陷 → 協調器改判 CHANGES_REQUESTED', r['verdict'] == 'CHANGES_REQUESTED' and r['note'], r)
check('#11 判 APPROVED 只列低缺陷 → 維持 APPROVED', O.verdict(rv('APPROVED', [{'severity': '低', 'problem': '可以更好'}])) == 'APPROVED')
check('#11 有多段 JSON 時只看最後一段', O.verdict(rv('APPROVED', [], body='```json\n{"verdict": "CHANGES_REQUESTED", "defects": []}\n```')) == 'APPROVED')
t = O.review_text(rv('CHANGES_REQUESTED', [{'severity': '中', 'target': '模型', 'model': 'axe', 'where': '01', 'problem': '像鋤頭'}]))
check('#11 給開發者看的意見：缺陷轉成「[中][模型:axe] 01：…」，不留 JSON', '1. [中][模型:axe] 01：像鋤頭' in t and '```json' not in t and t.endswith('VERDICT: CHANGES_REQUESTED'), t)
t = O.review_text(rv('CHANGES_REQUESTED', [{'severity': '低', 'where': '2-side', 'problem': '稍暗'}]))
check('#11 模型工坊的缺陷沒寫 target → 不加 [遊戲] 標籤', '1. [低] 2-side：稍暗' in t, t)


# #12 改檔守門員（gates/guard.py）：直接餵它 Claude Code 會傳的 JSON
def guard(rules, tool_input, tool='Write', raw=None, cwd=PJ / 'models' / 'box'):
    env = {k: v for k, v in ENV.items() if k != 'FLOW_GUARD_RULES'}
    if rules is not None:
        env['FLOW_GUARD_RULES'] = json.dumps(rules)
    data = raw if raw is not None else json.dumps({'tool_name': tool, 'tool_input': tool_input, 'cwd': str(cwd)})
    r = subprocess.run([PY, str(SB / 'gates' / 'guard.py')], input=data.encode('utf-8'), capture_output=True, env=env, timeout=60)
    return r.returncode, r.stderr.decode('utf-8', 'replace')


mrule = {'allow_files': [str(PJ / 'models' / 'box' / 'model.js')], 'hint': '只能改 model.js。'}
check('#12 模型開發者：改 model.js → 放行', guard(mrule, {'file_path': str(PJ / 'models' / 'box' / 'model.js')})[0] == 0)
check('#12 模型開發者：相對路徑 model.js → 放行', guard(mrule, {'file_path': 'model.js'})[0] == 0)
rc_, err_ = guard(mrule, {'file_path': str(PJ / 'models' / 'box' / 'spec.md')}, tool='Edit')
check('#12 模型開發者：改 spec.md → 擋下，並告訴它能改什麼', rc_ == 2 and '只能改 model.js' in err_, (rc_, err_))
check('#12 模型開發者：用 ../ 繞路改 spec.json → 擋下', guard(mrule, {'file_path': '../box/../box/spec.json'})[0] == 2)
grule = {'allow_dirs': [str(PJ / 'game')], 'deny_files': [str(PJ / 'game' / 'shots.json')], 'hint': '只能改遊戲資料夾。'}
check('#12 遊戲開發者：改遊戲資料夾裡的新檔案 → 放行', guard(grule, {'file_path': str(PJ / 'game' / 'src' / 'new.js')})[0] == 0)
check('#12 遊戲開發者：改鏡頭清單 → 擋下', guard(grule, {'file_path': str(PJ / 'game' / 'shots.json')})[0] == 2)
check('#12 遊戲開發者：改驗收清單 → 擋下', guard(grule, {'file_path': str(PJ / 'checks' / 'visual-checklist.md')})[0] == 2)
check('#12 遊戲開發者：NotebookEdit 也會檢查', guard(grule, {'notebook_path': str(PJ / 'checks' / 'x.ipynb')}, tool='NotebookEdit')[0] == 2)
check('#12 沒有規則 → 擋下（拿不準就不放行）', guard(None, {'file_path': str(PJ / 'game' / 'a.js')})[0] == 2)
check('#12 讀不懂輸入 → 擋下', guard(grule, None, raw='不是 JSON')[0] == 2)

# #14 你的檔案從範本建立、設定用範本當預設值、角色說明可用 agents.local/ 客製
check('#14 第一次執行：從 templates/ 建立你的檔案，內容跟範本一樣',
      sorted(O.CREATED) == sorted(set(O.USER_FILES) - {'flow.config.json'})
      and all((PJ / f).read_bytes() == (SB / 'templates' / f).read_bytes() for f in O.CREATED), O.CREATED)
(PJ / 'TASK.md').write_text('# 我的任務', 'utf-8')
check('#14 已經存在的檔案不會被範本覆蓋', O.ensure_user_files(PJ) == [] and (PJ / 'TASK.md').read_text('utf-8') == '# 我的任務')
shutil.copyfile(SB / 'templates' / 'TASK.md', PJ / 'TASK.md')
check('#14 設定少了某項 → 用範本的預設值補上（含巢狀項目）',
      O.CFG['model_viewport'] == TPL_CFG['model_viewport'] and 'model_code' in O.CFG['models'] and O.CFG['claude_bin'] == cfg['claude_bin'], O.CFG)
check('#14 設定合併：你的值蓋過預設值，dict 逐層合併',
      O.merge_config({'a': 1, 'm': {'x': 1, 'y': 2}, 'l': [1]}, {'m': {'y': 3}, 'b': 2, 'l': []}) == {'a': 1, 'm': {'x': 1, 'y': 3}, 'b': 2, 'l': []})
(PJ / 'agents.local').mkdir()
(PJ / 'agents.local' / 'reviewer-code.md').write_text('# 我的程式審查者', 'utf-8')
check('#14 agents.local/ 有同名檔案 → 用你的客製版', O.agent_md('reviewer-code.md') == '# 我的程式審查者')
check('#14 agents.local/ 沒有 → 用工具內建的 agents/', O.agent_md('dev.md') == (SB / 'agents' / 'dev.md').read_text('utf-8'))
rm(PJ / 'agents.local')
rd_ = PJ / 'models' / '_released' / 'axe'; rd_.mkdir(parents=True)
(rd_ / 'model.js').write_text('x', 'utf-8'); (rd_ / 'release.json').write_text(json.dumps({'version': 1, 'sha256': 'x'}), 'utf-8')
O.CFG['uses_models'] = ['axe']; ctx = O.models_context(); O.CFG['uses_models'] = []
rm(rd_)
check('#16 告訴遊戲開發者的模型引用路徑：從遊戲資料夾算起（../models/_released/…）', '../models/_released/axe/model.js' in ctx, ctx[:300])

# #13 參考圖：挑哪些檔案、超過張數或大小就略過、換圖指紋會變
rd = TMP / 'refs'; rd.mkdir()
for n in ('ref-b.jpg', 'ref-c.webp', 'ref-d.jpeg', 'ref-e.png', 'ref.png', 'notref.png', 'ref.txt', 'reopen.md'):
    (rd / n).write_bytes(b'x')
(rd / 'ref-big.png').write_bytes(b'\0' * (O.REF_MAX_BYTES + 1))
(rd / 'ref-empty.png').write_bytes(b'')
use, skip = O.ref_images(rd)
check('#13 只挑 ref 開頭的圖片，ref.png 排第一，最多 4 張', [p.name for p in use] == ['ref.png', 'ref-b.jpg', 'ref-c.webp', 'ref-d.jpeg'], use)
sk = dict(skip)
check('#13 太大、空檔案、超過張數 → 略過並寫明原因', set(sk) == {'ref-big.png', 'ref-empty.png', 'ref-e.png'}
      and 'MB' in sk['ref-big.png'] and '空' in sk['ref-empty.png'] and '張' in sk['ref-e.png'], skip)
check('#13 資料夾不存在 → 沒有參考圖，不會當掉', O.ref_images(TMP / 'no-such-dir') == ([], []))
fp1 = O.model_inputs('js', rd, 'cl', use)
(rd / 'ref.png').write_bytes(b'y')
check('#13 換了參考圖 → 這一輪的輸入指紋會變（不會沿用上一輪的審查結果）', fp1 != O.model_inputs('js', rd, 'cl', use))
check('#13 沒有參考圖 → 指紋跟加入參考圖功能之前一樣', O.model_inputs('js', rd, 'cl', []) == O.fingerprint('js', '', '', 'cl'))

# #10 引用檢查
proj = PJ / 'scanproj' / 'src'; (proj / 'models').mkdir(parents=True)
(proj / 'a.js').write_text("import {\n  build\n} from '../../models/box/model.js';\nimport ok from '../../models/_released/box/model.js';\nimport local from './models/player.js';\nimport * as T from 'three';\n", 'utf-8')
(proj / 'b.ts').write_text("const m = await import('../../models/axe/model.js');\n", 'utf-8')
(proj / 'c.html').write_text('<script type="module" src="../../models/box/model.js"></script>', 'utf-8')
O.PROJECT = (PJ / 'scanproj').resolve()
probs = O.gate_models()['problems']
check('#10 跨行 import 抓得到', any('a.js:3' in p for p in probs), probs)
check('#10 .ts 動態 import 抓得到', any('b.ts:1' in p for p in probs), probs)
check('#10 HTML src 抓得到', any('c.html' in p for p in probs), probs)
check('#10 _released、遊戲自己的 models 資料夾、套件名稱不誤判', len(probs) == 3, probs)
O.PROJECT = (PJ / 'game').resolve()

a, wa = O.new_run_dir('t-'); b, wb = O.new_run_dir('t-')
check('#10 同一秒建兩次資料夾不會當掉、名稱不同', wa != wb and wa.exists() and wb.exists(), (a, b))

for n, st in (('model-box-20260101-000000', 'old'), ('model-box-20260101-000000-2', 'n2'), ('model-box-20260101-000000-10', 'n10'),
              ('model-box-2-20270101-000000', 'OTHER')):
    (PJ / 'workspace' / n).mkdir(parents=True, exist_ok=True)
    (PJ / 'workspace' / n / 'status.json').write_text(json.dumps({'state': st}), 'utf-8')
check('#9 不會讀到 box-2 的紀錄，同一秒的編號依數字排序', O.latest_model_run('box')['state'] == 'n10', O.latest_model_run('box'))
for n in ('model-box-20260101-000000', 'model-box-20260101-000000-2', 'model-box-20260101-000000-10', 'model-box-2-20270101-000000'):
    rm(PJ / 'workspace' / n)
rm(wa); rm(wb)

# ---------- 做遊戲 E2E ----------
# 遊戲資料夾有沒 commit 的修改 → 不開始
(game / 'index.html').write_text((game / 'index.html').read_text('utf-8') + '<!-- dirty -->', 'utf-8')
before_runs = set((PJ / 'workspace').iterdir())
rc, out = orch('run', '--rounds', '2', scenario='game_ok')
check('#3 有沒 commit 的修改 → 不開始並列出檔案', rc != 0 and '還沒 commit' in out and 'index.html' in out, out[-500:])
check('#3 擋下時分支還在 main、沒留下空紀錄', g('rev-parse', '--abbrev-ref', 'HEAD') == 'main' and set((PJ / 'workspace').iterdir()) == before_runs)
g('checkout', '--', 'index.html')

# 低嚴重度的 [模型:axe] 不退件；第 2 輪沒改動 → 沿用結果、判定卡住
LOG.unlink(missing_ok=True)
main_before = g('rev-parse', 'main')
guard_tmp = lambda: set(Path(tempfile.gettempdir()).glob('flow-guard-*.json'))
guard_tmp_before = guard_tmp()
rc, out = orch('run', '--rounds', '4', scenario='game_low')
st, ws = last_game_status()
check('#2 低嚴重度與其他段落的 [模型:…] 不會退件', st['state'] != 'blocked_on_model' and st['history'][0]['model_defects'] == [], st)
check('#8 第 2 輪沒改動 → 沿用上一輪結果', len(st['history']) >= 2 and st['history'][1].get('skipped'), st['history'])
calls = [json.loads(l) for l in LOG.read_text('utf-8').splitlines()]
check('#8 沒改動的那一輪沒有呼叫審查者', sum(1 for c in calls if c['mode'] != 'acceptEdits') == 2, calls)
check('#7 判定卡住並寫明原因', st['state'] == 'stalled' and '沒有任何改動' in st.get('stall_reason', ''), st)
check('#6 視覺審查者的讀檔權限是 Read(./**)', any(c['tools'] == 'Read' and c['allowed'] == ['Read(./**)'] for c in calls), calls)
devc = [c for c in calls if c['mode'] == 'acceptEdits']
revc = [c for c in calls if c['mode'] != 'acceptEdits']
grules = [json.loads(c['rules']) for c in devc if c['rules']]
check('#12 遊戲開發者有掛改檔守門員：可改遊戲資料夾、鏡頭清單不能改',
      devc and len(grules) == len(devc) and all('guard.py' in (c['hook'] or '') for c in devc) and
      all(Path(r['allow_dirs'][0]).resolve() == (PJ / 'game').resolve() and Path(r['deny_files'][0]).resolve() == (game / 'shots.json').resolve() for r in grules), devc)
check('#12 審查者沒有掛守門員', revc and all(c['hook'] is None and c['rules'] is None for c in revc), revc)
check('#12 暫存的 --settings 設定檔用完就刪掉', guard_tmp() == guard_tmp_before, guard_tmp() - guard_tmp_before)
check('#3 結束後切回 main', g('rev-parse', '--abbrev-ref', 'HEAD') == 'main', g('rev-parse', '--abbrev-ref', 'HEAD'))
check('#3 main 沒有被動到', g('rev-parse', 'main') == main_before)
check('#3 AI 的修改在 auto 分支', 'round 1' in g('log', '--oneline', st['branch']) and '<!-- round 1 -->' in g('show', st['branch'] + ':index.html'))
check('#10 瀏覽器關卡有截圖（等畫完幾幀再拍）', (ws / 'shots' / 'r01' / '01.png').exists())
check('報告寫出原本分支與 auto 分支', 'main' in (ws / 'REPORT.md').read_text('utf-8') and st['branch'] in (ws / 'REPORT.md').read_text('utf-8'))

# 第二次做遊戲：從 main 開，不從上一次的 auto 分支開
first_branch = st['branch']
rc, out = orch('run', '--rounds', '1', scenario='game_ok')
st2, _ = last_game_status()
check('#3 第二次的新分支從 main 開出來', g('merge-base', st2['branch'], first_branch) == main_before and st2['state'] == 'approved', st2)

# 審查者格式寫錯 → 重叫一次
LOG.unlink(missing_ok=True)
rc, out = orch('run', '--rounds', '1', scenario='game_badfmt')
st6, _ = last_game_status()
vis = [c for c in (json.loads(l) for l in LOG.read_text('utf-8').splitlines()) if c['tools'] == 'Read']
check('#11 審查者格式寫錯 → 重叫一次，第二次寫對就照常通過', st6['state'] == 'approved' and len(vis) == 2 and vis[1]['retry'], (st6, vis))
LOG.unlink(missing_ok=True)
rc, out = orch('run', '--rounds', '1', scenario='game_badfmt_always')
st7, ws7 = last_game_status()
vis = [c for c in (json.loads(l) for l in LOG.read_text('utf-8').splitlines()) if c['tools'] == 'Read']
fb7 = (ws7 / 'messages' / 'r01-feedback.md').read_text('utf-8')
check('#11 重叫後還是寫錯 → 判沒通過（只重叫一次）', st7['history'][0]['visual'] == 'NO_VERDICT' and not st7['history'][0]['approved'] and len(vis) == 2, (st7, vis))
check('#11 回饋裡寫明審查者格式不對', '審查者的輸出格式不對' in fb7, fb7[-400:])

# 開發者偷改驗收清單、鏡頭清單、偷加檔案 → 還原並警告
cl_before = (PJ / 'checks' / 'visual-checklist.md').read_text('utf-8')
shots_before = (game / 'shots.json').read_text('utf-8')
rc, out = orch('run', '--rounds', '2', scenario='game_tamper')
st3, ws3 = last_game_status()
fb = (ws3 / 'messages' / 'r01-feedback.md').read_text('utf-8')
check('#5 驗收清單被偷改 → 還原', (PJ / 'checks' / 'visual-checklist.md').read_text('utf-8') == cl_before)
check('#5 遊戲資料夾裡的鏡頭清單被偷改 → 還原', (game / 'shots.json').read_text('utf-8') == shots_before)
check('#5 回饋裡有還原警告與新增檔案警告', 'checks/visual-checklist.md' in fb and 'cheat-new.md' in fb, fb[:600])
check('#5 偷改的那一輪不算通過', not st3['history'][0]['approved'])
(PJ / 'checks' / 'cheat-new.md').unlink(missing_ok=True)

# 高嚴重度的 [模型:axe] → 退件；不存在的模型只提醒
rc, out = orch('run', '--rounds', '2', scenario='game_high')
st4, ws4 = last_game_status()
check('#2 高嚴重度的 [模型:axe] → 退件並寫進 reopen.md', st4['state'] == 'blocked_on_model' and (PJ / 'models' / 'axe' / 'reopen.md').exists(), st4)
check('#2 不存在的模型只提醒、不退件', st4['history'][0].get('unknown_model_tags') == ['斧頭'] and '斧頭' in (ws4 / 'REPORT.md').read_text('utf-8'), st4)
(PJ / 'models' / 'axe' / 'reopen.md').unlink(missing_ok=True)

# 上次被強制停止、停在 auto 分支 → 這次先收尾、切回 main 再開始
g('checkout', '-b', 'auto/interrupted')
(game / 'index.html').write_text((game / 'index.html').read_text('utf-8') + '<!-- left over -->', 'utf-8')
(PJ / 'workspace' / 'game-branch.json').write_text(json.dumps({'project': str(game.resolve()), 'base': 'main', 'branch': 'auto/interrupted'}), 'utf-8')
rc, out = orch('run', '--rounds', '1', scenario='game_ok')
st5, _ = last_game_status()
check('#3 上次沒收尾：留下的修改存進 auto/interrupted', '<!-- left over -->' in g('show', 'auto/interrupted:index.html'))
check('#3 上次沒收尾：切回 main 再開始，結束後也在 main', st5['base'] == 'main' and g('rev-parse', '--abbrev-ref', 'HEAD') == 'main', (st5.get('base'), out[-400:]))

# ---------- 做模型 E2E（乾跑）----------
rc, out = orch('model', 'box', '--dry-run', '--rounds', '4')
mruns = sorted((PJ / 'workspace').glob('model-box-*'))
mst = json.loads((mruns[-1] / 'status.json').read_text('utf-8'))
check('#8 做模型：第 2 輪沒改動 → 沿用結果', len(mst['history']) >= 2 and mst['history'][1].get('skipped'), mst)
check('#7 做模型：判定卡住並寫明原因', mst['state'] == 'stalled' and mst.get('stall_reason'), mst)
mres = json.loads((mruns[-1] / 'shots' / 'r01' / 'gate-result.json').read_text('utf-8'))
print('     （模型關卡實際結果：', 'WebGPU 可用、截圖', len(mres['shots']), '張' if mres['shots'] else f"沒有截圖，錯誤：{mres['errors'][:1]}", '）')

# 做模型（不乾跑，用假的 claude）：模型開發者的守門規則只准改這個模型的 model.js
LOG.unlink(missing_ok=True)
rc, out = orch('model', 'box', '--rounds', '1')
calls = [json.loads(l) for l in LOG.read_text('utf-8').splitlines()]
mr = json.loads(calls[0]['rules']) if calls and calls[0]['rules'] else {}
check('#12 模型開發者有掛改檔守門員，只准改 model.js',
      'guard.py' in (calls[0]['hook'] or '') and [Path(p).resolve() for p in mr.get('allow_files', [])] == [(PJ / 'models' / 'box' / 'model.js').resolve()]
      and not mr.get('allow_dirs'), calls)
REF_SECTION = '# 參考圖\n這個資料夾裡有人類提供的參考圖：'     # 協調器插進開發者提示詞的段落開頭（角色說明裡也有「# 參考圖」字樣，不能只比對標題）
check('#13 沒有參考圖 → 開發者的提示詞沒有參考圖段落', calls and REF_SECTION not in calls[0]['prompt'], calls[0]['prompt'][-600:] if calls else calls)

# 做模型（有參考圖）：開發者被告知要看參考圖；參考圖放進審查者隔離資料夾的 ref/，跟截圖分開
LOG.unlink(missing_ok=True)
rc, out = orch('model', 'refcar', '--rounds', '1')
calls = [json.loads(l) for l in LOG.read_text('utf-8').splitlines()]
devc = [c for c in calls if c['mode'] == 'acceptEdits']
visc = [c for c in calls if c['tools'] == 'Read']
check('#13 有參考圖 → 開發者的提示詞列出 ref.png 並要它用 Read 看', devc and REF_SECTION + 'ref.png' in devc[0]['prompt'], out[-800:])
check('#16 開發者的提示詞給了能直接執行的建置指令（完整路徑）',
      devc and '# 自己檢查用的建置指令' in devc[0]['prompt'] and str(SB / 'lab' / 'build_lab.mjs') + '" .' in devc[0]['prompt'], devc[0]['prompt'][-900:] if devc else '')
check('#13 參考圖放進審查者隔離資料夾的 ref/，截圖照常在', visc and 'ref/ref.png' in visc[0]['files'] and '1-iso.png' in visc[0]['files'], visc[0]['files'] if visc else out[-800:])
check('#13 審查者的提示詞把參考圖和截圖分開列出', visc and '- ref/ref.png' in visc[0]['prompt'] and '參考圖' in visc[0]['prompt'], visc[0]['prompt'][-600:] if visc else '')
check('#13 開發者看過參考圖後，參考圖沒被動到', (PJ / 'models' / 'refcar' / 'ref.png').read_bytes() == png1x1())
rstats = json.loads((sorted((PJ / 'workspace').glob('model-refcar-*'))[-1] / 'shots' / 'r01' / 'gate-result.json').read_text('utf-8'))['stats']
check('#15 小模型：地板維持每格 1 公尺', rstats and rstats.get('grid_cell') == 1, rstats)
check('#15 審查者的 shots.md 開頭寫明地板每格幾公尺', visc and '地板方格每格 1 公尺' in (visc[0]['shots_md'] or ''), visc[0]['shots_md'] if visc else '')

# 實際比例的大模型（300×180 公尺）：地板放大、每格 10 公尺，截圖沒被遠裁切面切掉
LOG.unlink(missing_ok=True)
rc, out = orch('model', 'bigtrack', '--rounds', '1')
bres = json.loads((sorted((PJ / 'workspace').glob('model-bigtrack-*'))[-1] / 'shots' / 'r01' / 'gate-result.json').read_text('utf-8'))
check('#15 大模型：地板改成每格 10 公尺', bres['stats'] and bres['stats'].get('grid_cell') == 10, bres)
visc = [c for c in (json.loads(l) for l in LOG.read_text('utf-8').splitlines()) if c['tools'] == 'Read']
check('#15 大模型：審查者被告知每格 10 公尺', visc and '地板方格每格 10 公尺' in (visc[0]['shots_md'] or ''), visc[0]['shots_md'] if visc else out[-600:])
def non_bg_ratio(png):
    """截圖裡「跟背景色明顯不同」的像素比例（用瀏覽器解 PNG，不另外裝影像套件）"""
    import base64
    from playwright.sync_api import sync_playwright
    url = 'data:image/png;base64,' + base64.b64encode(Path(png).read_bytes()).decode()
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        r = br.new_page().evaluate("""(url) => new Promise((ok) => { const im = new Image(); im.onload = () => {
            const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
            const g = c.getContext('2d'); g.drawImage(im, 0, 0); const d = g.getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 0; i < d.length; i += 4)
              if (Math.max(Math.abs(d[i] - 0xb9), Math.abs(d[i + 1] - 0xc2), Math.abs(d[i + 2] - 0xcc)) > 30) n++;
            ok(n / (c.width * c.height)); }; im.src = url; })""", url)
        br.close()
    return r


ratio = non_bg_ratio(sorted((PJ / 'workspace').glob('model-bigtrack-*'))[-1] / 'shots' / 'r01' / '1-top.png')
check('#15 大模型：俯視截圖拍得到賽道（不是只有背景色）', ratio > 0.2, ratio)

# ---------- GUI：檢視器頁面隔離 ----------
lab = PJ / 'workspace' / 'lab'; lab.mkdir(parents=True, exist_ok=True)
(lab / 't.html').write_text("""<!doctype html><html><body><script>
(async () => { const r = {origin: self.origin, secure: isSecureContext, gpu: !!navigator.gpu, adapter: false, api: 'not-tried'};
  try { r.adapter = !!(navigator.gpu && await navigator.gpu.requestAdapter()); } catch (e) {}
  try { const t = new URLSearchParams(location.search).get('t');
        const x = await fetch('/api/overview', {headers: {'X-Token': t}}); r.api = 'reached ' + x.status; } catch (e) { r.api = 'blocked'; }
  window.__r = r; })();
</script></body></html>""", 'utf-8')
srv = subprocess.Popen([PY, str(SB / 'gui' / 'server.py'), '--no-browser', '--port', '8799'], cwd=SB, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=ENV)
try:
    port = int(re.search(r'127\.0\.0\.1:(\d+)', srv.stdout.readline()).group(1))
    html = urllib.request.urlopen(f'http://127.0.0.1:{port}/').read().decode('utf-8')
    token = re.search(r"const TOKEN = '([^']+)'", html).group(1)
    hdr = urllib.request.urlopen(f'http://127.0.0.1:{port}/ws/lab/t.html?t={token}').headers.get('Content-Security-Policy')
    check('#4 檢視器頁面有 sandbox 標頭', hdr == 'sandbox allow-scripts', hdr)

    def http_status(path, token_q=True):
        try:
            r = urllib.request.urlopen(f'http://127.0.0.1:{port}{path}' + (f'?t={token}' if token_q else ''))
            return r.status, r.headers.get('Content-Type')
        except urllib.error.HTTPError as e:
            return e.code, None
    ov = json.loads(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/overview', headers={'X-Token': token})).read())
    refm = {m['name']: m['refs'] for m in ov['models']}
    check('#13 介面總覽列出模型的參考圖', refm.get('refcar') == [{'file': 'ref.png', 'skip': None}] and refm.get('box') == [], refm)
    check('#13 介面拿得到參考圖（image/png）', http_status('/ref/refcar/ref.png') == (200, 'image/png'), http_status('/ref/refcar/ref.png'))
    check('#13 介面拿不到模型資料夾裡的其他檔案', http_status('/ref/refcar/spec.md')[0] == 404 and http_status('/ref/refcar/..%2Fbox%2Fmodel.js')[0] == 404)
    check('#13 沒帶 token 拿不到參考圖', http_status('/ref/refcar/ref.png', token_q=False)[0] == 403)

    def api(path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers={'X-Token': token, 'Content-Type': 'application/json'},
                                     data=json.dumps(body).encode('utf-8') if body is not None else None)
        try:
            r = urllib.request.urlopen(req)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None
    code_, f_ = api('/api/file?path=agents/dev.md')
    check('#14 介面看得到工具內建的 agents/，但標示不能寫', code_ == 200 and f_['writable'] is False, (code_, f_))
    dev_before = (SB / 'agents' / 'dev.md').read_text('utf-8')
    check('#14 介面改不了工具內建的 agents/', api('/api/file', {'path': 'agents/dev.md', 'content': 'x'})[0] == 403
          and (SB / 'agents' / 'dev.md').read_text('utf-8') == dev_before)
    code_, _ = api('/api/file', {'path': 'agents.local/dev.md', 'content': '# 我的開發者'})
    check('#14 介面可以寫 agents.local/ 的客製版，並列在清單裡',
          code_ == 200 and (PJ / 'agents.local' / 'dev.md').read_text('utf-8') == '# 我的開發者' and api('/api/lists')[1]['agents_local'] == ['dev.md'])
    code_, c_ = api('/api/config')
    check('#14 介面的設定頁拿到的是合併後的設定（少的項目有預設值）', code_ == 200 and c_.get('model_viewport') == TPL_CFG['model_viewport'], c_)
    rm(PJ / 'agents.local')
    code_, ov_ = api('/api/overview')
    check('#16 介面總覽帶出目前專案與專案清單', code_ == 200 and ov_['project'] == 't' and ov_['projects'] == ['t'], ov_ and ov_.get('projects'))
    code_, r_ = api('/api/project/new', {'name': 't2', 'game': 'g2'})
    check('#16 介面新增專案：建好並切換過去', code_ == 200 and (SB / 'projects' / '.current').read_text('utf-8') == 't2'
          and (SB / 'projects' / 't2' / 'g2' / 'shots.json').exists(), r_)
    check('#16 切到新專案後，畫面上的模型清單是新專案的（空的）', api('/api/overview')[1]['models'] == [])
    check('#16 介面新增專案：名稱不合法 → 拒絕', api('/api/project/new', {'name': '../x'})[0] == 400 and not (SB / 'x').exists())
    code_, _ = api('/api/project/use', {'name': 't'})
    check('#16 介面切換回原本的專案', code_ == 200 and api('/api/overview')[1]['project'] == 't')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, channel='chromium', args=['--enable-unsafe-webgpu'])
        pg = br.new_page(); pg.goto(f'http://127.0.0.1:{port}/ws/lab/t.html?t={token}')
        pg.wait_for_function('window.__r', timeout=20000); r = pg.evaluate('window.__r')
        pg2 = br.new_page(); pg2.goto(f'http://127.0.0.1:{port}/')
        ok_api = pg2.evaluate(f"fetch('/api/overview', {{headers: {{'X-Token': '{token}'}}}}).then(x => x.status)")
        br.close()
    check('#4 隔離後的頁面呼叫不到介面 API', r['api'] == 'blocked' and r['origin'] == 'null', r)
    check('#4 對照組：介面本身照常能呼叫 API', ok_api == 200, ok_api)
    check('#4 隔離後的頁面仍是安全環境、WebGPU 仍可用（檢視器照常能跑）', r['secure'] and r['gpu'], r)
    print('     （隔離頁面拿不拿得到顯示卡：', r['adapter'], '）')
finally:
    srv.terminate(); srv.wait()

# ---------- #16 剛 clone 下來：只有工具本身，還沒有任何專案 ----------
fresh = TMP / 'fresh'; fresh.mkdir()
shutil.copy(REAL / 'orchestrator.py', fresh / 'orchestrator.py')
shutil.copytree(REAL / 'templates', fresh / 'templates')


def fo(*a):
    r = subprocess.run([PY, str(fresh / 'orchestrator.py'), *a], cwd=fresh, capture_output=True, text=True, encoding='utf-8', errors='replace', env=ENV)
    return r.returncode, r.stdout + r.stderr


rc, out = fo('status')
check('#16 還沒有專案 → 指令不會當掉，告訴你怎麼建立', rc != 0 and 'new-project' in out and 'Traceback' not in out, out)
rc, out = fo('new-project', 'demo', '--game', 'forest-rpg')
fp = fresh / 'projects' / 'demo'
fcfg = json.loads((fp / 'flow.config.json').read_text('utf-8')) if (fp / 'flow.config.json').exists() else {}
check('#16 新增專案：設定、任務、清單從範本建立',
      rc == 0 and all((fp / f).exists() for f in O.USER_FILES) and (fp / 'TASK.md').read_bytes() == (REAL / 'templates' / 'TASK.md').read_bytes(), out)
check('#16 新增專案：遊戲資料夾用你取的名字，設定跟著指過去',
      fcfg.get('project_dir') == 'forest-rpg' and fcfg.get('shots_spec') == 'forest-rpg/shots.json', fcfg)
check('#16 新增專案：遊戲資料夾有 shots.json 範本、已 git init、有 models/_released/',
      (fp / 'forest-rpg' / 'shots.json').read_bytes() == (REAL / 'templates' / 'shots.json').read_bytes()
      and (fp / 'forest-rpg' / '.git').is_dir() and (fp / 'models' / '_released').is_dir(), out)
check('#16 新增專案後就是目前專案，status 照常能跑',
      (fresh / 'projects' / '.current').read_text('utf-8') == 'demo' and fo('status')[0] == 0, fo('status'))
rc, out = fo('new-project', 'demo')
check('#16 專案已經存在 → 拒絕，不覆蓋', rc != 0 and '已經存在' in out and (fp / 'forest-rpg' / 'shots.json').exists(), out)
rc, out = fo('new-project', '../evil')
check('#16 專案名稱不合法（含路徑）→ 拒絕', rc != 0 and not (fresh / 'evil').exists(), out)
fo('new-project', 'demo2')
rc, out = fo('status', '--project', 'demo')
check('#16 --project 指定別的專案，不改變目前專案', rc == 0 and '專案：demo' in out and (fresh / 'projects' / '.current').read_text('utf-8') == 'demo2', out)
rc, out = fo('use', 'demo')
check('#16 use 切換目前專案', rc == 0 and (fresh / 'projects' / '.current').read_text('utf-8') == 'demo', out)
rc, out = fo('status', '--project', 'nope')
check('#16 指定不存在的專案 → 拒絕並列出現有專案', rc != 0 and 'demo' in out and 'demo2' in out, out)
(fp / 'human-feedback.md').unlink()
rc, out = fo('status')
check('#16 專案缺了檔案 → 從範本補上並告訴你', rc == 0 and (fp / 'human-feedback.md').exists() and 'human-feedback.md' in out, out)

failed = sum(not ok for _, ok in results)
print(f"\n共 {len(results)} 項，通過 {len(results) - failed} 項，失敗 {failed} 項")
os.chdir(REAL)                     # 先離開副本資料夾，Windows 才刪得掉
if failed:
    print('有失敗項目，測試副本保留在：', TMP)
else:
    rm(TMP)
sys.exit(1 if failed else 0)

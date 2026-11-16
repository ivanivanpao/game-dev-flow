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
SB = TMP / 'sb'                                             # 專案副本
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
    r = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *a], cwd=SB / 'game', capture_output=True, text=True, encoding='utf-8')
    return r.stdout.strip()


# ---------- 建副本 ----------
rm(SB); SB.mkdir(parents=True)
for f in ('orchestrator.py', 'package.json', 'TASK.md', 'prompt.md', 'human-feedback.md'):
    shutil.copy(REAL / f, SB / f)
for d in ('gates', 'lab', 'agents', 'checks', 'gui', 'node_modules'):
    shutil.copytree(REAL / d, SB / d)
(SB / 'models' / '_released').mkdir(parents=True)
(SB / 'models' / 'axe').mkdir()
(SB / 'models' / 'axe' / 'spec.json').write_text('{"name":"axe","checks":{},"views":[]}', 'utf-8')
# 測試模型：尺寸要求故意做不到，關卡一定不過
(SB / 'models' / 'box').mkdir()
(SB / 'models' / 'box' / 'model.js').write_text(
    "export const meta = { name: 'box' };\nexport function build(THREE) { const g = new THREE.Group();\n"
    "g.add(new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), new THREE.MeshStandardMaterial({ color: 0x8899aa }))); return g; }\n", 'utf-8')
(SB / 'models' / 'box' / 'spec.md').write_text('# 方塊', 'utf-8')
(SB / 'models' / 'box' / 'spec.json').write_text(json.dumps({'name': 'box', 'checks': {'size': [[5, 6], None, None]},
    'views': [{'name': '1-iso', 'dir': [1, 0.6, 1], 'dist': 'fit', 'describe': '方塊'}]}), 'utf-8')
(SB / 'models' / 'box' / 'checklist.md').write_text('# 清單', 'utf-8')
# 假的 claude
fake = SB / 'fake'; fake.mkdir()
shutil.copy(Path(__file__).resolve().parent / 'fake_claude.py', fake / 'fake_claude.py')
(fake / 'claude.cmd').write_text(f'@"{PY}" "%~dp0fake_claude.py" %*\r\n', 'mbcs')   # cmd 用系統編碼讀批次檔，路徑有中文要這樣存
# 極小的假遊戲（有自己的 git）
game = SB / 'game'; game.mkdir()
(game / 'index.html').write_text('<!doctype html><html><body><canvas></canvas><script>window.__game={ready:true}</script></body></html>', 'utf-8')
(game / 'build.js').write_text("const fs=require('fs');fs.mkdirSync('dist',{recursive:true});fs.copyFileSync('index.html','dist/index.html');", 'utf-8')
(game / 'shots.json').write_text(json.dumps({'ready': 'window.__game && window.__game.ready', 'settle_ms': 100,
    'shots': [{'name': '01', 'wait_ms': 50, 'describe': '畫面'}]}), 'utf-8')
(game / '.gitignore').write_text('dist/\n', 'utf-8')
g('init', '-b', 'main'); g('add', '-A'); g('commit', '-m', 'init')
cfg = json.loads((REAL / 'flow.config.json').read_text('utf-8'))
cfg.update({'project_dir': 'game', 'build_cmd': 'node build.js', 'entry': 'dist/index.html', 'shots_spec': 'game/shots.json',
            'claude_bin': str(fake / 'claude.cmd'), 'require_webgpu': False, 'uses_models': [], 'stall_rounds': 2,
            'perf': {'target_avg_ms': None, 'target_p95_ms': None}, 'agent_timeout_sec': 120})
(SB / 'flow.config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), 'utf-8')
ENV = dict(os.environ, FAKE_LOG=str(LOG), FAKE_SB=str(SB), PYTHONIOENCODING='utf-8')


def orch(*a, scenario=''):
    r = subprocess.run([PY, str(SB / 'orchestrator.py'), *a], cwd=SB, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=dict(ENV, FAKE_SCENARIO=scenario), timeout=600)
    return r.returncode, r.stdout + r.stderr


def last_game_status():
    runs = sorted(p for p in (SB / 'workspace').iterdir() if p.is_dir() and re.match(r'\d{8}-\d{6}', p.name))
    return json.loads((runs[-1] / 'status.json').read_text('utf-8')), runs[-1]


# ---------- 單元測試（匯入副本的 orchestrator）----------
sys.path.insert(0, str(SB)); os.chdir(SB)
import orchestrator as O

t = O.model_defects('## 逐條對照驗收清單\n1. [模型:axe] 符合\n## 缺陷清單\n1. [低][模型:axe] 小問題\n2. [高][模型:axe] 像鋤頭\n3. [中][遊戲] 光線\n## 我最不確定的 3 件事\n- [高][模型:pick] 不確定\n')
check('#2 只算缺陷清單裡的高/中', t == {'axe': ['2. [高][模型:axe] 像鋤頭']}, t)
t = O.model_defects('## 問題清單\n1. [中] [模型: axe] 程式審查者的格式\n')
check('#2 程式審查者的問題清單也會判斷', list(t) == ['axe'], t)

# #10 引用檢查
proj = SB / 'scanproj' / 'src'; (proj / 'models').mkdir(parents=True)
(proj / 'a.js').write_text("import {\n  build\n} from '../../models/box/model.js';\nimport ok from '../../models/_released/box/model.js';\nimport local from './models/player.js';\nimport * as T from 'three';\n", 'utf-8')
(proj / 'b.ts').write_text("const m = await import('../../models/axe/model.js');\n", 'utf-8')
(proj / 'c.html').write_text('<script type="module" src="../../models/box/model.js"></script>', 'utf-8')
O.PROJECT = (SB / 'scanproj').resolve()
probs = O.gate_models()['problems']
check('#10 跨行 import 抓得到', any('a.js:3' in p for p in probs), probs)
check('#10 .ts 動態 import 抓得到', any('b.ts:1' in p for p in probs), probs)
check('#10 HTML src 抓得到', any('c.html' in p for p in probs), probs)
check('#10 _released、遊戲自己的 models 資料夾、套件名稱不誤判', len(probs) == 3, probs)
O.PROJECT = (SB / 'game').resolve()

a, wa = O.new_run_dir('t-'); b, wb = O.new_run_dir('t-')
check('#10 同一秒建兩次資料夾不會當掉、名稱不同', wa != wb and wa.exists() and wb.exists(), (a, b))

for n, st in (('model-box-20260101-000000', 'old'), ('model-box-20260101-000000-2', 'n2'), ('model-box-20260101-000000-10', 'n10'),
              ('model-box-2-20270101-000000', 'OTHER')):
    (SB / 'workspace' / n).mkdir(parents=True, exist_ok=True)
    (SB / 'workspace' / n / 'status.json').write_text(json.dumps({'state': st}), 'utf-8')
check('#9 不會讀到 box-2 的紀錄，同一秒的編號依數字排序', O.latest_model_run('box')['state'] == 'n10', O.latest_model_run('box'))
for n in ('model-box-20260101-000000', 'model-box-20260101-000000-2', 'model-box-20260101-000000-10', 'model-box-2-20270101-000000'):
    rm(SB / 'workspace' / n)
rm(wa); rm(wb)

# ---------- 做遊戲 E2E ----------
# 遊戲資料夾有沒 commit 的修改 → 不開始
(game / 'index.html').write_text((game / 'index.html').read_text('utf-8') + '<!-- dirty -->', 'utf-8')
before_runs = set((SB / 'workspace').iterdir())
rc, out = orch('run', '--rounds', '2', scenario='game_ok')
check('#3 有沒 commit 的修改 → 不開始並列出檔案', rc != 0 and '還沒 commit' in out and 'index.html' in out, out[-500:])
check('#3 擋下時分支還在 main、沒留下空紀錄', g('rev-parse', '--abbrev-ref', 'HEAD') == 'main' and set((SB / 'workspace').iterdir()) == before_runs)
g('checkout', '--', 'index.html')

# 低嚴重度的 [模型:axe] 不退件；第 2 輪沒改動 → 沿用結果、判定卡住
LOG.unlink(missing_ok=True)
main_before = g('rev-parse', 'main')
rc, out = orch('run', '--rounds', '4', scenario='game_low')
st, ws = last_game_status()
check('#2 低嚴重度與其他段落的 [模型:…] 不會退件', st['state'] != 'blocked_on_model' and st['history'][0]['model_defects'] == [], st)
check('#8 第 2 輪沒改動 → 沿用上一輪結果', len(st['history']) >= 2 and st['history'][1].get('skipped'), st['history'])
calls = [json.loads(l) for l in LOG.read_text('utf-8').splitlines()]
check('#8 沒改動的那一輪沒有呼叫審查者', sum(1 for c in calls if c['mode'] != 'acceptEdits') == 2, calls)
check('#7 判定卡住並寫明原因', st['state'] == 'stalled' and '沒有任何改動' in st.get('stall_reason', ''), st)
check('#6 視覺審查者的讀檔權限是 Read(./**)', any(c['tools'] == 'Read' and c['allowed'] == ['Read(./**)'] for c in calls), calls)
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

# 開發者偷改驗收清單、鏡頭清單、偷加檔案 → 還原並警告
cl_before = (SB / 'checks' / 'visual-checklist.md').read_text('utf-8')
shots_before = (game / 'shots.json').read_text('utf-8')
rc, out = orch('run', '--rounds', '2', scenario='game_tamper')
st3, ws3 = last_game_status()
fb = (ws3 / 'messages' / 'r01-feedback.md').read_text('utf-8')
check('#5 驗收清單被偷改 → 還原', (SB / 'checks' / 'visual-checklist.md').read_text('utf-8') == cl_before)
check('#5 遊戲資料夾裡的鏡頭清單被偷改 → 還原', (game / 'shots.json').read_text('utf-8') == shots_before)
check('#5 回饋裡有還原警告與新增檔案警告', 'checks/visual-checklist.md' in fb and 'cheat-new.md' in fb, fb[:600])
check('#5 偷改的那一輪不算通過', not st3['history'][0]['approved'])
(SB / 'checks' / 'cheat-new.md').unlink(missing_ok=True)

# 高嚴重度的 [模型:axe] → 退件；不存在的模型只提醒
rc, out = orch('run', '--rounds', '2', scenario='game_high')
st4, ws4 = last_game_status()
check('#2 高嚴重度的 [模型:axe] → 退件並寫進 reopen.md', st4['state'] == 'blocked_on_model' and (SB / 'models' / 'axe' / 'reopen.md').exists(), st4)
check('#2 不存在的模型只提醒、不退件', st4['history'][0].get('unknown_model_tags') == ['斧頭'] and '斧頭' in (ws4 / 'REPORT.md').read_text('utf-8'), st4)
(SB / 'models' / 'axe' / 'reopen.md').unlink(missing_ok=True)

# 上次被強制停止、停在 auto 分支 → 這次先收尾、切回 main 再開始
g('checkout', '-b', 'auto/interrupted')
(game / 'index.html').write_text((game / 'index.html').read_text('utf-8') + '<!-- left over -->', 'utf-8')
(SB / 'workspace' / 'game-branch.json').write_text(json.dumps({'project': str(game.resolve()), 'base': 'main', 'branch': 'auto/interrupted'}), 'utf-8')
rc, out = orch('run', '--rounds', '1', scenario='game_ok')
st5, _ = last_game_status()
check('#3 上次沒收尾：留下的修改存進 auto/interrupted', '<!-- left over -->' in g('show', 'auto/interrupted:index.html'))
check('#3 上次沒收尾：切回 main 再開始，結束後也在 main', st5['base'] == 'main' and g('rev-parse', '--abbrev-ref', 'HEAD') == 'main', (st5.get('base'), out[-400:]))

# ---------- 做模型 E2E（乾跑）----------
rc, out = orch('model', 'box', '--dry-run', '--rounds', '4')
mruns = sorted((SB / 'workspace').glob('model-box-*'))
mst = json.loads((mruns[-1] / 'status.json').read_text('utf-8'))
check('#8 做模型：第 2 輪沒改動 → 沿用結果', len(mst['history']) >= 2 and mst['history'][1].get('skipped'), mst)
check('#7 做模型：判定卡住並寫明原因', mst['state'] == 'stalled' and mst.get('stall_reason'), mst)
mres = json.loads((mruns[-1] / 'shots' / 'r01' / 'gate-result.json').read_text('utf-8'))
print('     （模型關卡實際結果：', 'WebGPU 可用、截圖', len(mres['shots']), '張' if mres['shots'] else f"沒有截圖，錯誤：{mres['errors'][:1]}", '）')

# ---------- GUI：檢視器頁面隔離 ----------
lab = SB / 'workspace' / 'lab'; lab.mkdir(parents=True, exist_ok=True)
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

# ---------- 重新下載專案（沒有 models/ 資料夾）時，status 不會當掉 ----------
fresh = TMP / 'fresh'; fresh.mkdir()
shutil.copy(REAL / 'orchestrator.py', fresh / 'orchestrator.py')
shutil.copy(SB / 'flow.config.json', fresh / 'flow.config.json')
r = subprocess.run([PY, str(fresh / 'orchestrator.py'), 'status'], cwd=fresh, capture_output=True, text=True, encoding='utf-8', errors='replace', env=ENV)
check('沒有 models/ 資料夾時 status 不會當掉，並自動建立 models/_released/', r.returncode == 0 and (fresh / 'models' / '_released').is_dir(), r.stdout + r.stderr)

failed = sum(not ok for _, ok in results)
print(f"\n共 {len(results)} 項，通過 {len(results) - failed} 項，失敗 {failed} 項")
os.chdir(REAL)                     # 先離開副本資料夾，Windows 才刪得掉
if failed:
    print('有失敗項目，測試副本保留在：', TMP)
else:
    rm(TMP)
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""
圖形化操作介面（本機網頁伺服器，只用 Python 標準函式庫）

用法：.venv/Scripts/python.exe gui/server.py   （或雙擊專案根目錄的 gui.bat）
啟動後會自動用瀏覽器打開 http://127.0.0.1:<埠號>/

安全設計：
  - 只綁定 127.0.0.1，區網其他電腦連不進來
  - 檢查 Host 標頭，擋掉 DNS rebinding（惡意網站把自己的網域指到 127.0.0.1）
  - 每次啟動產生一組隨機 token，嵌在頁面裡；所有 API 都要帶 token，其他網站的頁面拿不到它
  - 只能讀寫白名單內的檔案（你該寫的規格、任務、清單、設定、agents.local/ 的客製角色說明）；
    model.js 等 AI 管的檔案、工具內建的 agents/ 只能看
  - 按鈕只會執行 orchestrator.py 的既有指令，參數都經過檢查。
    但設定檔裡的 build_cmd、extra_checks、notify_cmd 本來就是「會被執行的指令」，能改設定就等於能決定執行什麼
  - workspace 裡的 HTML（模型檢視器，裡面是 AI 寫的程式）加上 sandbox 隔離，碰不到這個介面的 API
"""
import argparse, datetime, importlib, importlib.util, json, mimetypes, os, re, secrets, shutil, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.dont_write_bytecode = True                 # 匯入 orchestrator 時不要產生 __pycache__ 資料夾

ROOT = Path(__file__).resolve().parent.parent
GUI = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TOKEN = secrets.token_urlsafe(24)
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$')     # 模型名稱：英數字、底線、連字號
ALLOWED_HOSTS = set()                                          # 啟動後依實際埠號填入


# ---------- 目前專案 ----------
# 介面所有頁面都針對「目前專案」（projects/.current）；切換專案就是改這個檔案，跟指令 use 一樣
def orch(reload=False):
    """匯入協調器並切到目前專案。reload：重讀程式與設定（總覽用，才會拿到最新狀態）"""
    import orchestrator as O
    if reload:
        O = importlib.reload(O)
    cur = O.current_project()
    if cur:
        O.set_project(cur)
    return O


def pdir():
    """目前專案的資料夾；還沒有專案就丟出錯誤（訊息會顯示在介面上）"""
    O = orch()
    if not O.current_project():
        raise ValueError('還沒有專案，請先在上方按「新增專案」')
    return O.P


def ws_dir():
    """目前專案的執行紀錄資料夾"""
    return pdir() / 'workspace'


# ---------- 設定與路徑 ----------
def cfg():
    """目前專案實際使用的設定（範本預設值＋你的設定），跟協調器讀到的一樣"""
    pdir()
    return orch().CFG


def models_dir():
    pdir()
    return orch().MODELS


def model_names():
    try:
        d = models_dir()
    except ValueError:                       # 還沒有專案
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith('_')) if d.exists() else []


def inside(p, base):
    try:
        Path(p).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def shots_spec_path():
    """遊戲的鏡頭清單（位置由設定決定）；必須在專案資料夾內且是 .json"""
    try:
        s, p0 = cfg().get('shots_spec'), pdir()
    except Exception:
        return None
    if not s:
        return None
    p = (p0 / s).resolve()
    return p if inside(p, p0) and p.suffix == '.json' else None


MODEL_EDITABLE = ('spec.md', 'spec.json', 'checklist.md', 'human-feedback.md')
MODEL_READONLY = ('model.js', 'reopen.md')


def resolve_file(rel, write=False):
    """把網頁傳來的相對路徑轉成實際路徑；不在白名單就回傳 None。
    agents/ 是工具內建的（在工具資料夾），其他檔案都在目前專案的資料夾裡"""
    rel = (rel or '').replace('\\', '/').strip('/')
    if not rel or '..' in rel.split('/'):
        return None
    parts = rel.split('/')
    if parts[0] == 'agents':
        base = ROOT
    else:
        try:
            base = pdir()
        except ValueError:
            return None
    p = (base / rel).resolve()
    if not inside(p, base):
        return None
    ok_write = (
        rel in ('prompt.md', 'TASK.md', 'human-feedback.md', 'flow.config.json')
        or (len(parts) == 2 and parts[0] in ('checks', 'agents.local') and parts[1].endswith('.md'))
        or (len(parts) == 3 and parts[0] == 'models' and NAME_RE.match(parts[1]) and parts[2] in MODEL_EDITABLE)
        or (shots_spec_path() is not None and p == shots_spec_path())
    )
    ok_read = (ok_write or (len(parts) == 3 and parts[0] == 'models' and NAME_RE.match(parts[1]) and parts[2] in MODEL_READONLY)
               or (len(parts) == 2 and parts[0] == 'agents' and parts[1].endswith('.md')))     # 工具內建的角色說明：只能看，客製請放 agents.local/
    if write:
        return p if ok_write else None
    return p if ok_read else None


def write_text(p, text):
    """保留網頁送來的換行（\\n），不轉成 Windows 的 \\r\\n，避免整份檔案在 git 裡變成大量差異"""
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def has_placeholder(p):
    """檔案裡還有【……】代表範本還沒填"""
    try:
        return '【' in p.read_text('utf-8')
    except Exception:
        return False


# ---------- 狀態總覽 ----------
def overview():
    out = {'env': {
        'python': sys.executable,
        'venv': '.venv' in Path(sys.executable).parts,
        'playwright': importlib.util.find_spec('playwright') is not None,
        'claude': shutil.which('claude') is not None,
        'node_modules': (ROOT / 'node_modules' / 'three').exists(),
    }}
    try:
        O = orch(reload=True)              # 每次重讀，才會拿到最新的設定與狀態
    except Exception as e:
        out['error'] = f'讀取設定失敗（專案的 flow.config.json 格式錯誤？）：{e}'
        return out
    out['projects'] = O.project_names()
    out['project'] = O.current_project()
    if not out['project']:
        return out                         # 還沒有專案：介面只顯示「新增專案」
    c = O.CFG
    uses = c.get('uses_models', [])
    models = []
    for name in model_names():
        d = O.MODELS / name
        st, rel = O.latest_model_run(name), O.released_info(name)
        r = None
        if rel:
            rf = O.RELEASED / name / 'model.js'
            r = {'version': rel['version'], 'dry_run': bool(rel.get('dry_run')),
                 'tampered': not (rf.exists() and O.sha256(rf) == rel['sha256']),
                 'newer': not ((d / 'model.js').exists() and O.sha256(d / 'model.js') == rel['sha256'])}
        use, skip = O.ref_images(d)
        refs = [{'file': p.name, 'skip': None} for p in use] + [{'file': f, 'skip': why} for f, why in skip]
        models.append({'name': name, 'in_uses': name in uses, 'refs': sorted(refs, key=lambda r: r['file']),
                       'run': {'state': st['state'], 'run_id': st['run_id'], 'dry_run': bool(st.get('dry_run')), 'rounds': len(st.get('history', []))} if st else None,
                       'released': r, 'reopen': (d / 'reopen.md').exists(), 'has_model_js': (d / 'model.js').exists(),
                       'todo': [f for f in ('spec.md', 'spec.json') if has_placeholder(d / f)]})
    missing = [m for m in uses if not O.released_info(m)]
    proj = O.PROJECT
    sp = shots_spec_path()
    out.update({
        'models': models, 'uses_models': uses,
        'game': {'ready': not missing, 'missing': missing, 'project_dir': c.get('project_dir'), 'project_exists': proj.exists(),
                 'shots_spec': c.get('shots_spec'), 'shots_exists': bool(sp and sp.exists())},
        'todo': [f for f in ('prompt.md', 'TASK.md') if has_placeholder(O.P / f)] + (['shots.json'] if sp and has_placeholder(sp) else []),
    })
    return out


# ---------- 背景工作（一次只跑一個：model / run / gates）----------
JOB = {'proc': None, 'cmd': None, 'log': [], 'started': None, 'ended': None, 'rc': None}
LOCK = threading.Lock()


def job_running():
    return JOB['proc'] is not None and JOB['proc'].poll() is None


def child_env():
    return dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')


def start_job(args, label):
    with LOCK:
        if job_running():
            raise RuntimeError('已經有工作在跑：' + JOB['cmd'])
        full = with_project(args)
        p = subprocess.Popen([sys.executable, str(ROOT / 'orchestrator.py'), *full], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', env=child_env())
        JOB.update(proc=p, cmd=f'{label}（專案 {full[-1]}）', log=[], started=datetime.datetime.now().isoformat(timespec='seconds'), ended=None, rc=None)

    def pump():
        for line in p.stdout:
            JOB['log'].append(line.rstrip('\n'))
        p.wait()
        JOB['rc'] = p.returncode
        JOB['ended'] = datetime.datetime.now().isoformat(timespec='seconds')
    threading.Thread(target=pump, daemon=True).start()


def stop_job():
    p = JOB['proc']
    if not job_running():
        return False
    if os.name == 'nt':
        # /T：連同它啟動的子程序（claude、node、瀏覽器）一起結束；/F：強制結束
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
    else:
        p.terminate()
    JOB['log'].append('（已由介面手動停止）')
    return True


def with_project(args):
    """協調器指令明確加上目前專案（不靠 projects/.current，避免執行途中被切換）"""
    pdir()
    return [*args, '--project', orch().current_project()]


def run_quick(args, timeout=300):
    """短指令（release、unrelease、add-check）直接等它跑完"""
    r = subprocess.run([sys.executable, str(ROOT / 'orchestrator.py'), *with_project(args)], cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=child_env(), timeout=timeout)
    return {'rc': r.returncode, 'output': (r.stdout or '') + (r.stderr or '')}


def rounds_arg(v):
    if v in (None, '', 0):
        return []
    n = int(v)
    if not 1 <= n <= 50:
        raise ValueError('輪數要在 1 到 50 之間')
    return ['--rounds', str(n)]


def do_cmd(b):
    cmd = b.get('cmd')
    name = b.get('name')
    if cmd in ('model', 'lab', 'release', 'unrelease'):
        if not (isinstance(name, str) and NAME_RE.match(name) and name in model_names()):
            raise ValueError('模型名稱不正確')
    if cmd == 'model':
        args = ['model', name] + rounds_arg(b.get('rounds')) + (['--dry-run'] if b.get('dry') else [])
        start_job(args, ' '.join(args)); return {'started': True}
    if cmd == 'run':
        args = ['run'] + rounds_arg(b.get('rounds')) + (['--dry-run'] if b.get('dry') else [])
        start_job(args, ' '.join(args)); return {'started': True}
    if cmd == 'gates':
        start_job(['gates'], 'gates'); return {'started': True}
    if cmd in ('release', 'unrelease'):
        if job_running():
            raise RuntimeError('有工作正在跑，等它結束再發布或撤回')
        return run_quick([cmd, name])
    if cmd == 'lab':
        # 直接呼叫建置腳本，不用 orchestrator 的 lab 指令（那個會再自己開一個 file:// 分頁）
        out = ws_dir() / 'lab' / f'{name}.html'
        r = subprocess.run(['node', str(ROOT / 'lab' / 'build_lab.mjs'), str(models_dir() / name), str(out)], cwd=ROOT, capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=300)
        return {'rc': r.returncode, 'output': (r.stdout or '') + (r.stderr or ''), 'url': f'/ws/lab/{name}.html?t={TOKEN}'}
    if cmd == 'add-check':
        text = (b.get('text') or '').strip()
        if not text or len(text) > 500 or '\n' in text:
            raise ValueError('規則要是一行文字（500 字以內）')
        m = b.get('model')
        args = ['add-check', text]
        if m:
            if not (NAME_RE.match(m) and m in model_names()):
                raise ValueError('模型名稱不正確')
            args += ['--model', m]
        return run_quick(args)
    raise ValueError('不支援的指令')


# ---------- 新增模型（產生待填的規格範本）----------
SPEC_MD = """# 模型規格：{title}（{name}）

【一句話：這是什麼東西、大概多大。例如：一把單手長度約 1 公尺的鐵鎚。】

**用途**：【在遊戲裡怎麼被使用，例如：玩家由上往下敲。】

**必須長得像**
- 【整體輪廓：由哪些零件組成。】
- 【最容易做錯、一定要對的特徵。寫成看圖就能判斷的句子。】

**座標約定（遊戲依賴它）**
- 【原點在哪裡（例如手握處）；長軸沿哪個方向（例如 -z）。】
- 【正面、尖端、刃口朝哪個方向。】

**不可以**：【例如：零件懸空、握柄過粗、比例失真。】

**參考圖（選填）**：（沒有參考圖就留著這行。有的話，把圖片放進這個資料夾，檔名用 ref 開頭，例如 ref.png、ref-side.jpg；
png／jpg／webp，最多 4 張、每張 5MB 以內。再把這行改成要參考圖的哪些地方、哪些不用照做，例如：照 ref.png 的車頭形狀和紅色，輪子細節不用那麼多。）
"""

SPEC_JSON = {
    "name": None,
    "checks": {
        "size": [None, None, None],
        "max_triangles": 2000,
        "max_materials": 6,
        "max_meshes": 12
    },
    "views": [
        {"name": "1-side", "dir": [1, 0, 0], "dist": "fit", "describe": "【側面（從 +x 看）應該看到什麼】"},
        {"name": "2-top", "dir": [0, 1, 0], "up": [0, 0, -1], "dist": "fit", "describe": "【俯視（從 +y 往下看，-z 朝上）應該看到什麼】"},
        {"name": "3-front", "dir": [0, 0, 1], "dist": "fit", "describe": "【正面（從 +z 看）應該看到什麼】"},
        {"name": "4-iso", "dir": [1, 0.6, 1], "dist": "fit", "describe": "【斜 45 度整體應該看到什麼】"}
    ]
}


def new_model(b):
    name = (b.get('name') or '').strip()
    title = (b.get('title') or '').strip() or name
    if not NAME_RE.match(name):
        raise ValueError('名稱只能用英文字母、數字、底線、連字號（例如 hammer）')
    d = models_dir() / name
    if d.exists():
        raise ValueError(f'models/{name} 已經存在')
    d.mkdir(parents=True)
    write_text(d / 'spec.md', SPEC_MD.format(name=name, title=title))
    spec = json.loads(json.dumps(SPEC_JSON)); spec['name'] = name
    write_text(d / 'spec.json', json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    write_text(d / 'checklist.md', f'# {title}的專屬驗收清單（累積式）\n')
    if b.get('add_to_uses'):
        c = cfg()
        if name not in c.setdefault('uses_models', []):
            c['uses_models'].append(name)
            write_text(pdir() / 'flow.config.json', json.dumps(c, ensure_ascii=False, indent=2) + '\n')
    return {'created': name}


# ---------- 執行紀錄 ----------
def list_runs():
    try:
        ws = ws_dir()
    except ValueError:                       # 還沒有專案
        return []
    if not ws.exists():
        return []
    runs = []
    for d in ws.iterdir():
        if not d.is_dir() or d.name == 'lab':
            continue
        st = {}
        if (d / 'status.json').exists():
            try:
                st = json.loads((d / 'status.json').read_text('utf-8'))
            except Exception:
                pass
        kind = 'model' if d.name.startswith('model-') else ('gates' if d.name == 'gates-only' else 'game')
        runs.append({'id': d.name, 'kind': kind, 'state': st.get('state'), 'dry_run': st.get('dry_run'),
                     'rounds': len(st.get('history', [])), 'mtime': d.stat().st_mtime})
    return sorted(runs, key=lambda r: -r['mtime'])


def run_files(run_id):
    ws = ws_dir()
    d = (ws / run_id).resolve()
    if not inside(d, ws) or not d.is_dir():
        raise ValueError('找不到這筆紀錄')
    files = [str(p.relative_to(d)).replace('\\', '/') for p in sorted(d.rglob('*'))
             if p.is_file() and p.suffix in ('.md', '.json', '.png') and p.name != 'status.json']
    return {'id': run_id, 'files': files}


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                 # 不在終端機印每個請求

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra_headers=None):
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, need_token=True, token_in_query=False):
        if self.headers.get('Host') not in ALLOWED_HOSTS:
            self._send(403, {'error': 'Host 不允許'}); return False
        if need_token:
            t = parse_qs(urlparse(self.path).query).get('t', [''])[0] if token_in_query else self.headers.get('X-Token')
            if not secrets.compare_digest(t or '', TOKEN):
                self._send(403, {'error': 'token 錯誤，請用啟動時開啟的網址重新整理'}); return False
        return True

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n).decode('utf-8') or '{}') if n else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == '/favicon.ico':                 # 瀏覽器會自動要網站小圖示；沒有就回空的，避免 console 出現 403
            self.send_response(204); self.end_headers(); return
        if u.path == '/':
            if not self._guard(need_token=False):
                return
            html = (GUI / 'index.html').read_text('utf-8').replace('__TOKEN__', TOKEN)
            return self._send(200, html, 'text/html; charset=utf-8')
        if u.path.startswith('/ws/'):
            return self._serve_ws(unquote(u.path[4:]))
        if u.path.startswith('/ref/'):
            return self._serve_ref(unquote(u.path[5:]))
        if not self._guard():
            return
        try:
            if u.path == '/api/overview':
                return self._send(200, overview())
            if u.path == '/api/file':
                p = resolve_file(q.get('path'))
                if not p:
                    return self._send(403, {'error': '這個檔案不能從介面開啟'})
                return self._send(200, {'path': q.get('path'), 'exists': p.exists(), 'content': p.read_text('utf-8') if p.exists() else '',
                                        'writable': resolve_file(q.get('path'), write=True) is not None})
            if u.path == '/api/config':
                return self._send(200, cfg())
            if u.path == '/api/job':
                since = int(q.get('since', 0))
                return self._send(200, {'running': job_running(), 'cmd': JOB['cmd'], 'rc': JOB['rc'], 'started': JOB['started'],
                                        'ended': JOB['ended'], 'total': len(JOB['log']), 'lines': JOB['log'][since:]})
            if u.path == '/api/runs':
                return self._send(200, list_runs())
            if u.path == '/api/run':
                return self._send(200, run_files(q.get('id', '')))
            if u.path == '/api/lists':
                return self._send(200, {'agents': sorted(p.name for p in (ROOT / 'agents').glob('*.md')),
                                        'agents_local': sorted(p.name for p in (pdir() / 'agents.local').glob('*.md')),
                                        'checks': sorted(p.name for p in (pdir() / 'checks').glob('*.md'))})
            self._send(404, {'error': '沒有這個路徑'})
        except Exception as e:
            self._send(400, {'error': str(e)})

    def _serve_ws(self, rel):
        """唯讀提供目前專案 workspace 裡的報告、截圖、檢視器（圖片與分頁無法帶標頭，所以 token 放網址）"""
        if not self._guard(token_in_query=True):
            return
        try:
            ws = ws_dir()
        except ValueError:
            return self._send(404, {'error': '找不到'})
        p = (ws / rel).resolve()
        if not inside(p, ws) or not p.is_file() or p.suffix not in ('.png', '.md', '.json', '.html'):
            return self._send(404, {'error': '找不到'})
        ctype = {'.md': 'text/plain; charset=utf-8', '.json': 'application/json; charset=utf-8',
                 '.html': 'text/html; charset=utf-8'}.get(p.suffix) or mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        extra = None
        if p.suffix == '.html':
            # 檢視器頁面裡跑的是 AI 寫的 model.js。加上 sandbox，瀏覽器會把它當成「另一個網站」：
            # 程式照樣能跑（allow-scripts），但碰不到這個介面的 API，就算從網址拿到 token 也沒用
            extra = {'Content-Security-Policy': 'sandbox allow-scripts'}
        self._send(200, p.read_bytes(), ctype, extra)

    def _serve_ref(self, rel):
        """唯讀提供模型的參考圖（/ref/<模型>/<檔名>，給模型頁預覽用；圖片無法帶標頭，所以 token 放網址）。
        只認協調器認得的參考圖檔名（ref 開頭的 png／jpg／webp），模型資料夾裡的其他檔案一律拿不到"""
        if not self._guard(token_in_query=True):
            return
        parts = rel.split('/')
        if len(parts) != 2 or not NAME_RE.match(parts[0]) or parts[0] not in model_names():
            return self._send(404, {'error': '找不到'})
        d = models_dir() / parts[0]
        try:
            use, skip = orch().ref_images(d)
        except Exception as e:
            return self._send(400, {'error': f'讀取設定失敗：{e}'})
        if parts[1] not in [p.name for p in use] + [f for f, _ in skip]:
            return self._send(404, {'error': '找不到'})
        p = d / parts[1]
        ctype = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}[p.suffix.lower()]
        self._send(200, p.read_bytes(), ctype)

    def do_POST(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        try:
            b = self._body()
            if u.path == '/api/file':
                p = resolve_file(b.get('path'), write=True)
                if not p:
                    return self._send(403, {'error': '這個檔案不能從介面修改'})
                text = b.get('content', '')
                if p.suffix == '.json':
                    try:
                        json.loads(text)
                    except json.JSONDecodeError as e:
                        return self._send(400, {'error': f'JSON 格式錯誤，沒有存檔：第 {e.lineno} 行第 {e.colno} 欄 {e.msg}'})
                write_text(p, text)
                return self._send(200, {'saved': True})
            if u.path == '/api/config':
                c = b.get('config')
                if not isinstance(c, dict):
                    raise ValueError('設定格式錯誤')
                write_text(pdir() / 'flow.config.json', json.dumps(c, ensure_ascii=False, indent=2) + '\n')
                return self._send(200, {'saved': True})
            if u.path == '/api/cmd':
                return self._send(200, do_cmd(b))
            if u.path == '/api/job/stop':
                return self._send(200, {'stopped': stop_job()})
            if u.path == '/api/newmodel':
                return self._send(200, new_model(b))
            if u.path in ('/api/project/use', '/api/project/new'):
                # 工作在跑的時候換專案，畫面會跟正在跑的工作對不上；等它結束再換
                if job_running():
                    raise RuntimeError('有工作正在跑，等它結束再切換或新增專案')
                O = orch()
                if u.path == '/api/project/use':
                    O.use_project(str(b.get('name') or ''))
                    return self._send(200, {'project': b.get('name')})
                p, (git_ok, git_out) = O.new_project(str(b.get('name') or '').strip(), str(b.get('game') or 'game').strip())
                return self._send(200, {'project': p.name, 'git_ok': git_ok, 'git_output': git_out[-300:]})
            self._send(404, {'error': '沒有這個路徑'})
        except Exception as e:
            self._send(400, {'error': str(e)})


def main():
    ap = argparse.ArgumentParser(description='遊戲開發流程的圖形化介面')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true', help='不要自動開瀏覽器')
    a = ap.parse_args()
    srv = None
    for port in range(a.port, a.port + 20):           # 埠號被占用就往後找
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', port), H)
            break
        except OSError:
            continue
    if not srv:
        sys.exit('找不到可用的埠號')
    port = srv.server_address[1]
    ALLOWED_HOSTS.update({f'127.0.0.1:{port}', f'localhost:{port}'})
    url = f'http://127.0.0.1:{port}/'
    print(f'介面已啟動：{url}\n關閉介面：在這個視窗按 Ctrl+C', flush=True)
    try:
        O = orch()                                # 切到目前專案時，會從 templates/ 補上專案缺的檔案
        if not O.current_project():
            print('還沒有專案：請在介面上方按「新增專案」', flush=True)
        else:
            print(f'目前專案：{O.current_project()}', flush=True)
            if O.CREATED:
                print('已從 templates/ 補上這個專案缺的檔案：' + '、'.join(O.CREATED), flush=True)
    except Exception as e:
        print(f'讀取專案設定失敗（flow.config.json 格式錯誤？）：{e}', flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if job_running():
            stop_job()
            print('已停止還在跑的工作。')
        srv.server_close()


if __name__ == '__main__':
    main()

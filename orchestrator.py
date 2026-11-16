#!/usr/bin/env python3
"""
遊戲開發自動流程：協調器（只用 Python 標準函式庫）

兩段式流程：
  第一段「模型工坊」：每個模型獨立開發、測試，通過並經你確認後才「發布」
  第二段「遊戲整合」：遊戲只能用已發布的模型；遊戲關卡發現模型本身的問題，只能退回第一段

指令：
  model <名稱> [--rounds N] [--dry-run]   跑某個模型的開發迴圈（模型工坊）
  lab <名稱>                              建出檢視器 HTML 讓你自己轉著看（不呼叫 AI）
  release <名稱>                          你確認後發布模型（必須剛通過迴圈、且檔案沒被改過）
  unrelease <名稱>                        撤回發布（遊戲階段會因此擋下）
  status                                  看每個模型的狀態與遊戲是否可以開工
  run [--rounds N] [--dry-run]            做遊戲：AI 反覆修改遊戲程式（在 auto/<時間> 分支上改，結束後切回原本的分支）
  gates                                   只跑遊戲的客觀關卡（不呼叫 AI）
  add-check "一句話" [--model 名稱]       把一條規則加進驗收清單（遊戲用或某模型專屬）

共同設計：agent 之間不直接對話，只透過檔案；先過客觀關卡再給審查者看；審查者「挑錯」；
視覺審查者在隔離資料夾裡只看得到截圖與清單；停不停由這支程式的規則決定。
"""
import argparse, datetime, difflib, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / 'flow.config.json').read_text('utf-8'))
PROJECT = (ROOT / CFG['project_dir']).resolve()
MODELS = (ROOT / CFG.get('models_dir', 'models')).resolve()
RELEASED = MODELS / '_released'
VERDICT_RE = re.compile(r'VERDICT:\s*(APPROVED|CHANGES_REQUESTED)')
MODEL_TAG_RE = re.compile(r'\[模型[:：]\s*([\w\-]+)\s*\]')
DEFECT_SECTIONS = ('缺陷清單', '問題清單')            # 審查者列缺陷的段落標題（視覺審查者／程式審查者）
SERIOUS_RE = re.compile(r'\[\s*(高|中)\s*\]')          # 嚴重度「高」或「中」
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'workspace', '.claude-tmp', '__pycache__', 'dist'}   # 掃描檔案時略過的資料夾
SRC_EXT = {'.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.html', '.vue'}                          # 遊戲裡要檢查引用路徑的檔案類型
# 抓出 import … from '路徑'、export … from '路徑'、import('路徑')、require('路徑')、HTML 的 src="路徑" 裡的路徑（可以跨行）
IMPORT_RE = re.compile(r"""(?:\bimport\b[^'"`;]*?|\bexport\b[^'"`;]*?\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|\bsrc\s*=\s*)['"`]([^'"`]+)['"`]""")
GIT = ['git', '-c', 'user.name=game-flow', '-c', 'user.email=game-flow@local']


# ---------- 小工具 ----------
def log(*a):
    print(time.strftime('[%H:%M:%S]'), *a, flush=True)


def sh(cmd, cwd, timeout=900):
    """執行一行指令，回傳 (回傳碼, 輸出文字)"""
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        return 124, f'逾時（{timeout} 秒）'


def git(*args, cwd=None):
    return sh(GIT + list(args), cwd or PROJECT, 120)


def read(p, default=''):
    p = Path(p)
    return p.read_text('utf-8') if p.exists() else default


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tail(s, n=3000):
    return s if len(s) <= n else '…（前面省略）\n' + s[-n:]


def browser_args():
    return os.environ.get('FLOW_BROWSER_ARGS', CFG.get('browser_args', ''))


def rel(p):
    """顯示用：工具資料夾裡的檔案顯示相對路徑，外面的顯示完整路徑"""
    try:
        return str(Path(p).resolve().relative_to(ROOT)).replace('\\', '/')
    except ValueError:
        return str(p)


def inside(p, base):
    try:
        Path(p).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def walk_files(base, exclude=()):
    """列出 base 底下所有檔案（略過 SKIP_DIRS，以及 exclude 裡的檔案或資料夾）"""
    base, exclude = Path(base), {Path(e).resolve() for e in exclude}
    if not base.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        d = Path(dirpath).resolve()
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and (d / n) not in exclude]
        out += [d / f for f in filenames if (d / f) not in exclude]
    return out


class Protect:
    """保護「AI 開發者不該動的檔案」：開發者上工前記下內容，做完後比對。
    範圍是整個工具資料夾（驗收清單、規格、設定、任務、關卡程式、AI 指令、已發布的模型…），
    只排除開發者本來就該改的地方。被改或被刪的檔案一律還原；多出來的新檔案只回報、不刪除。
    （否則開發者可以靠改驗收標準或改關卡程式來過關；它有 node 指令可用，不受工作資料夾限制）
    注意：開發者工作的這段時間，你自己在介面上改的檔案也會被還原，請等這一輪結束再改。"""
    def __init__(self, collect):
        self.collect = collect                          # 回傳「要保護的檔案清單」的函式
        self.saved = {p: p.read_bytes() for p in collect() if p.is_file()}

    def restore(self):
        """回傳 (被還原的檔案, 多出來的新檔案)"""
        changed = []
        for p, b in self.saved.items():
            if not p.exists() or p.read_bytes() != b:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b); changed.append(rel(p))
        added = [rel(p) for p in self.collect() if p not in self.saved]
        return changed, added


def fingerprint(*parts):
    """把好幾段文字合成一個指紋，用來判斷「這一輪跟上一輪是不是完全一樣」"""
    h = hashlib.sha1()
    for s in parts:
        h.update((s or '').encode('utf-8')); h.update(b'\0')
    return h.hexdigest()


def new_run_dir(prefix):
    """建立這次執行的資料夾 workspace/<prefix><時間>；同一秒內已經有同名的，就在後面加 -2、-3…"""
    base = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    run_id, n = base, 1
    while True:
        ws = ROOT / 'workspace' / f'{prefix}{run_id}'
        try:
            ws.mkdir(parents=True)
            return run_id, ws
        except FileExistsError:
            n += 1; run_id = f'{base}-{n}'


# ---------- 呼叫 Claude Code（無頭模式）----------
def agent_model(slot):
    """依角色代號查該用哪個模型：先看 models.<代號>，沒填就用 model，再沒填就是 None（Claude Code 預設）"""
    return (CFG.get('models') or {}).get(slot) or CFG.get('model')


def run_agent(role, prompt, cwd, tools, allowed, timeout, dry, slot):
    """
    role    : dev / visual / code
    prompt  : 完整提示詞（用 stdin 傳入，避免命令列長度限制）
    tools   : --tools 的值，限制這個 agent 能用的工具集合
    allowed : --allowedTools 的清單，這些工具不需要詢問就能用
    slot    : 角色代號（model_dev / model_visual / model_code / game_dev / game_visual / game_code），用來查要用哪個模型
    """
    log(f'  {slot} 使用模型：' + (agent_model(slot) or 'Claude Code 預設'))
    if dry:
        return {'dev': '## 這一輪做了什麼\n- （dry-run：沒有真的呼叫 AI）\n',
                'visual': '（dry-run）\nVERDICT: APPROVED\n',
                'code': '（dry-run）\nVERDICT: APPROVED\n'}[role]
    exe = shutil.which(CFG['claude_bin'])
    if not exe:
        sys.exit(f"找不到指令 {CFG['claude_bin']}。請先安裝 Claude Code，或在 flow.config.json 的 claude_bin 填完整路徑。")
    cmd = [exe, '-p', '--output-format', 'text', '--tools', tools]            # -p：無頭模式，讀 stdin 的提示詞後輸出結果就結束
    if allowed:
        cmd += ['--allowedTools'] + allowed                                     # 這些工具免詢問（無頭模式沒人可以按「允許」）
    cmd += ['--permission-mode', 'acceptEdits' if role == 'dev' else 'dontAsk']  # 審查者 dontAsk：沒在允許清單的一律拒絕
    if agent_model(slot):
        cmd += ['--model', agent_model(slot)]
    if CFG.get('max_budget_usd_per_call'):
        cmd += ['--max-budget-usd', str(CFG['max_budget_usd_per_call'])]        # 單次呼叫的花費上限
    try:
        r = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return f'（{role} 逾時 {timeout} 秒，沒有輸出）\n'
    if r.returncode != 0:
        return f'（{role} 執行失敗，回傳碼 {r.returncode}）\n{tail(r.stderr or r.stdout)}\n'
    return r.stdout


def verdict(text):
    m = VERDICT_RE.findall(text or '')
    return m[-1] if m else 'NO_VERDICT'          # 沒有明確標記就當作沒通過（fail closed）


def model_defects(text):
    """從審查意見挑出「要退回模型工坊」的缺陷，回傳 {模型名稱: [缺陷那一行, …]}。
    只看「缺陷清單／問題清單」那一段，而且只算嚴重度標「高」或「中」的項目。
    其他段落（逐條對照、最不確定的事）或標「低」的項目就算寫了 [模型:…] 也不算，
    避免審查者隨口提一句就把已發布的模型撤回。"""
    found, in_list = {}, False
    for line in (text or '').splitlines():
        s = line.strip()
        if s.startswith('#'):                                    # 遇到新的標題：判斷是不是缺陷清單那一段
            in_list = any(k in s for k in DEFECT_SECTIONS)
            continue
        if in_list and SERIOUS_RE.search(s):
            for m in MODEL_TAG_RE.findall(s):
                found.setdefault(m, []).append(s)
    return found


def visual_review(role_md, shots_dir, shots, extra_files, checklist, dry, slot):
    """視覺審查者：在系統暫存區的隔離資料夾裡，只放截圖、說明文字與清單（沒有程式碼、沒有開發者自評）"""
    sandbox = Path(tempfile.mkdtemp(prefix='flow-visual-'))
    try:
        lines = ['# 每張截圖應該呈現什麼', '']
        for s in shots:
            shutil.copy(Path(shots_dir) / s['file'], sandbox / s['file'])
            lines += [f"## {s['file']}", s['describe'], '']
        (sandbox / 'shots.md').write_text('\n'.join(lines), 'utf-8')
        (sandbox / 'checklist.md').write_text(checklist, 'utf-8')
        names = ['shots.md', 'checklist.md']
        for fname, text in extra_files.items():
            (sandbox / fname).write_text(text, 'utf-8'); names.append(fname)
        prompt = (read(ROOT / 'agents' / role_md) + '\n\n---\n你的工作資料夾裡有這些文字檔：' + '、'.join(names) +
                  '\n以及這些截圖：\n' + '\n'.join('- ' + s['file'] for s in shots) +
                  '\n請用 Read 工具逐一開啟每一個檔案（圖片與文字都要看），然後依格式輸出。')
        # 讀檔權限只開放「自己的隔離資料夾」：Read(./**) 只涵蓋工作資料夾，資料夾外的讀取在 dontAsk 模式下會被拒絕
        # （只寫 Read 等於允許讀電腦上任何檔案，審查者就有機會讀到專案程式碼）
        return run_agent('visual', prompt, sandbox, 'Read', ['Read(./**)'], CFG['agent_timeout_sec'], dry, slot)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def code_review(diff, task_text, cwd, dry, slot):
    limit = CFG['max_diff_chars']
    cut = ''
    if len(diff) > limit:                      # 太長要截斷時，明確告訴審查者後面還有內容，不要讓它以為已經看完
        cut = f'\n（注意：diff 太長，以上只顯示前 {limit} 字，後面省略了 {len(diff) - limit} 字。需要的話請用 Read 工具開啟完整檔案。）\n'
    prompt = (read(ROOT / 'agents' / 'reviewer-code.md') + '\n\n---\n# 本次任務與完成標準\n' + task_text +
              '\n\n# 本輪程式碼差異（diff）\n```diff\n' + diff[:limit] + '\n```\n' + cut)
    return run_agent('code', prompt, cwd, 'Read,Grep,Glob', ['Read', 'Grep', 'Glob'], CFG['agent_timeout_sec'], dry, slot)


# =====================================================================
# 第一段：模型工坊
# =====================================================================
def model_dir(name):
    d = MODELS / name
    if not d.is_dir() or not (d / 'spec.json').exists():
        sys.exit(f'找不到模型 {name}（需要 {d}/spec.json 與 spec.md）。')
    return d


def build_lab(name, out):
    rc, o = sh(['node', str(ROOT / 'lab' / 'build_lab.mjs'), name, str(out)], ROOT, 300)
    return rc == 0, tail(o, 1500)


def gate_model(name, shots_dir):
    d = model_dir(name)
    if not (d / 'model.js').exists():
        return {'ok': False, 'errors': ['model.js 還不存在'], 'failed_checks': [], 'passed_checks': [], 'stats': None, 'shots': []}
    html = Path(shots_dir) / 'lab.html'
    Path(shots_dir).mkdir(parents=True, exist_ok=True)
    ok, o = build_lab(name, html)
    if not ok:
        return {'ok': False, 'errors': ['檢視器建置失敗（model.js 語法錯誤？沒執行過 npm install？）：' + o], 'failed_checks': [], 'passed_checks': [], 'stats': None, 'shots': []}
    cmd = [sys.executable, str(ROOT / 'gates' / 'model_gate.py'), '--html', str(html), '--spec', str(d / 'spec.json'),
           '--out', str(shots_dir), '--viewport', CFG.get('model_viewport', '900x700')]
    if browser_args():
        cmd += ['--browser-args=' + browser_args()]
    if CFG.get('headed'):
        cmd += ['--headed']
    rc, o = sh(cmd, ROOT, 600)
    f = Path(shots_dir) / 'gate-result.json'
    if rc != 0 or not f.exists():
        return {'ok': False, 'errors': ['模型關卡當掉：' + tail(o, 1500)], 'failed_checks': [], 'passed_checks': [], 'stats': None, 'shots': []}
    return json.loads(f.read_text('utf-8'))


def cmd_model(args):
    name, dry = args.name, args.dry_run
    d = model_dir(name)
    rounds = args.rounds or CFG.get('model_max_rounds', 5)
    run_id, ws = new_run_dir(f'model-{name}-')
    (ws / 'messages').mkdir(); (ws / 'snapshots').mkdir()
    status = {'kind': 'model', 'name': name, 'run_id': run_id, 'state': 'running', 'round': 0, 'history': [], 'model_sha': None, 'dry_run': bool(dry)}

    def save():
        (ws / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), 'utf-8')

    mj = d / 'model.js'
    if mj.exists():
        shutil.copy(mj, ws / 'snapshots' / 'r00-model.js')
    cl = lambda: read(ROOT / 'checks' / 'model-checklist.md') + '\n\n' + read(d / 'checklist.md')
    feedback = ''
    reopen = d / 'reopen.md'
    if reopen.exists():
        feedback = '## 遊戲整合階段退件（來自驗收的缺陷）\n' + reopen.read_text('utf-8')
    prev = None                     # 上一輪的結果（判斷「沒改動就沿用」與「卡住」用）
    no_change = same_fail = 0       # 連續「沒任何改動」的輪數、連續「客觀檢查失敗項目完全相同」的輪數
    for rnd in range(1, rounds + 1):
        status['round'] = rnd; save()
        log(f'===== 模型 {name}：第 {rnd} 輪 =====')
        entry = {'round': rnd}
        human = read(d / 'human-feedback.md')
        before = read(mj)

        log('模型開發者工作中…')
        prompt = (read(ROOT / 'agents' / 'model-dev.md') + f'\n\n---\n# 模型名稱\n{name}\n\n# spec.md（模型規格）\n' + read(d / 'spec.md') +
                  '\n\n# spec.json（客觀檢查條件與截圖角度）\n```json\n' + read(d / 'spec.json') + '\n```\n\n# 驗收清單\n' + cl() +
                  '\n\n# 目前的 model.js\n```js\n' + (before or '（還沒有，請從頭建立）') + '\n```\n\n---\n# 上一輪回饋\n' + (feedback or '（第 1 輪，沒有回饋）') +
                  '\n\n---\n# 人類意見\n' + human + f'\n\n現在是第 {rnd} 輪（最多 {rounds} 輪）。')
        # 開發者上工前才記下要保護的檔案（除了這個模型的 model.js，整個工具資料夾都不准動）；
        # 每一輪重新記，你在兩輪之間改的規格、清單、意見才不會被誤還原
        protected = Protect(lambda: walk_files(ROOT, exclude=[mj]))
        dev_out = run_agent('dev', prompt, d, 'default', ['Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash(node *)'], CFG['agent_timeout_sec'], dry, 'model_dev')
        reverted, added = protected.restore()
        (ws / 'messages' / f'r{rnd:02d}-dev.md').write_text(dev_out, 'utf-8')
        after = read(mj)
        shutil.copy(mj, ws / 'snapshots' / f'r{rnd:02d}-model.js') if mj.exists() else None
        diff = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'model.js(前)', 'model.js(後)'))
        entry['changed'] = bool(diff.strip())

        # 這一輪的「輸入」：model.js、規格、驗收清單。跟上一輪完全一樣，重跑檢查與審查的結果也會一樣
        inputs = fingerprint(after, read(d / 'spec.md'), read(d / 'spec.json'), cl())
        if prev and inputs == prev['inputs'] and not reverted:
            log('這一輪沒有任何改動：沿用上一輪的檢查與審查結果，不再呼叫審查者（省 AI 用量）')
            g, vis_out, code_out = prev['g'], prev['vis'], prev['code']
            entry.update({k: prev['entry'][k] for k in ('gate', 'visual', 'code') if k in prev['entry']})
            entry['skipped'] = True
        else:
            log('模型關卡：建置檢視器、幾何檢查、多角度截圖…')
            g = gate_model(name, ws / 'shots' / f'r{rnd:02d}')
            entry['gate'] = g['ok']

            vis_out = code_out = ''
            if g['ok']:
                if CFG.get('model_reviewers', {}).get('visual', True):
                    log('視覺審查者（只看截圖）…')
                    vis_out = visual_review('reviewer-model-visual.md', ws / 'shots' / f'r{rnd:02d}', g['shots'],
                                            {'spec.md': read(d / 'spec.md')}, cl(), dry, 'model_visual')
                    (ws / 'messages' / f'r{rnd:02d}-review-visual.md').write_text(vis_out, 'utf-8')
                    entry['visual'] = verdict(vis_out)
                if CFG.get('model_reviewers', {}).get('code', False):
                    log('程式審查者…')
                    code_out = code_review(diff, read(d / 'spec.md'), d, dry, 'model_code')
                    (ws / 'messages' / f'r{rnd:02d}-review-code.md').write_text(code_out, 'utf-8')
                    entry['code'] = verdict(code_out)

        fb = [f'# 模型 {name} 第 {rnd} 輪回饋', '']
        if entry.get('skipped'):
            fb += ['- 這一輪你沒有做任何修改，所以沒有重新檢查。下面是上一輪的結果，問題都還在。', '']
        if reverted:
            fb += [f"- 警告：你改了不能改的檔案（{'、'.join(reverted)}），已還原。只能改 model.js。", '']
        if added:
            fb += [f"- 警告：你新增了檔案（{'、'.join(added)}）。只能改 model.js，不要新增其他檔案。", '']
        fb.append('## 客觀關卡')
        fb += [f'- 失敗：{e}' for e in g['errors']]
        fb += [f'- 檢查不過：{c}' for c in g['failed_checks']]
        fb += [f'- 檢查通過：{c}' for c in g['passed_checks']]
        if g['stats']:
            fb.append(f"- 實測：尺寸 {g['stats']['size']}、包圍盒 {g['stats']['bbox_min']} ~ {g['stats']['bbox_max']}、三角形 {g['stats']['triangles']}")
        if vis_out:
            fb += ['', '## 視覺審查者意見', vis_out]
        if code_out:
            fb += ['', '## 程式審查者意見', code_out]
        feedback = '\n'.join(fb)
        (ws / 'messages' / f'r{rnd:02d}-feedback.md').write_text(feedback, 'utf-8')

        need = ['visual'] if CFG.get('model_reviewers', {}).get('visual', True) else []
        need += ['code'] if CFG.get('model_reviewers', {}).get('code', False) else []
        approved = g['ok'] and all(entry.get(k) == 'APPROVED' for k in need) and not reverted
        entry['approved'] = bool(approved)
        status['history'].append(entry); save()
        log('本輪結果：', json.dumps(entry, ensure_ascii=False))
        if approved:
            status['state'] = 'approved'; status['model_sha'] = sha256(mj); break
        # 卡住偵測：比對「實際內容」，不比對 AI 評語（AI 每次用字都不同，比評語永遠不會判定卡住）
        fails = sorted(re.sub(r'[\\/]r\d{2}[\\/]', '/', x) for x in g['errors'] + g['failed_checks'])   # 去掉路徑裡的輪數
        no_change = no_change + 1 if entry.get('skipped') else 0
        same_fail = same_fail + 1 if (prev and fails and fails == prev['fails']) else 0
        prev = {'inputs': inputs, 'g': g, 'vis': vis_out, 'code': code_out, 'entry': entry, 'fails': fails}
        if rnd > 1 and max(no_change, same_fail) + 1 >= CFG['stall_rounds']:
            status['state'] = 'stalled'
            status['stall_reason'] = (f'連續 {no_change + 1} 輪沒有任何改動' if no_change >= same_fail
                                      else f'連續 {same_fail + 1} 輪客觀檢查失敗的項目完全相同')
            break
    else:
        status['state'] = 'max_rounds'
    if status['state'] == 'running':
        status['state'] = 'max_rounds'
    if status['state'] == 'approved' and reopen.exists():
        reopen.rename(d / f'reopen.done.{run_id}.md')       # 退件已處理
    save()
    names = {'approved': '通過，等你確認後發布', 'max_rounds': '達到輪數上限，仍未通過', 'stalled': '卡住'}
    L = [f"# 模型 {name}：{names.get(status['state'])}", '', f'執行編號：{run_id}', '']
    if status.get('stall_reason'):
        L += [f"卡住的原因：{status['stall_reason']}", '']
    L += ['| 輪 | 有改動 | 關卡 | 視覺 | 程式 | 通過 |', '|---|---|---|---|---|---|']
    for h in status['history']:
        chg = '無（沿用上一輪結果）' if h.get('skipped') else ('有' if h.get('changed') else '無')
        L.append(f"| {h['round']} | {chg} | {'過' if h.get('gate') else '不過'} | {h.get('visual', '—')} | {h.get('code', '—')} | {'過' if h['approved'] else '不過'} |")
    L += ['', '## 你現在要做的（人類檢查點）', '', f'1. 看截圖：`workspace/model-{name}-{run_id}/shots/`',
          f'2. 自己轉著看：`python orchestrator.py lab {name}`（建出檢視器並開啟，可拖曳旋轉、滾輪縮放）',
          f'3. 滿意：`python orchestrator.py release {name}`（發布後遊戲才能用）',
          f'4. 不滿意：把意見寫進 `models/{name}/human-feedback.md`，重要的規則用 `python orchestrator.py add-check "…" --model {name}` 存起來，再跑一次']
    (ws / 'REPORT.md').write_text('\n'.join(L), 'utf-8')
    print('\n' + '=' * 60 + f'\n模型 {name} 流程結束：{status["state"]}｜報告：{ws / "REPORT.md"}\n' + '=' * 60)
    notify(status['state'], ws / 'REPORT.md')


def latest_model_run(name):
    """找這個模型最近一次的工坊紀錄。資料夾名稱要完全符合 model-<名稱>-<日期>-<時間>[-編號]，
    否則找 axe 的紀錄時，會連 axe-2 的紀錄一起抓進來"""
    pat = re.compile(r'model-' + re.escape(name) + r'-(\d{8}-\d{6})(?:-(\d+))?$')
    runs = []
    for p in (ROOT / 'workspace').glob(f'model-{name}-*'):
        m = pat.match(p.name)
        if m and (p / 'status.json').exists():
            runs.append(((m.group(1), int(m.group(2) or 1)), p))     # 依時間、再依同一秒內的編號排序
    return json.loads(read(max(runs)[1] / 'status.json')) if runs else None


def released_info(name):
    f = RELEASED / name / 'release.json'
    return json.loads(f.read_text('utf-8')) if f.exists() else None


def cmd_release(args):
    name = args.name
    d = model_dir(name)
    st = latest_model_run(name)
    if not st or st['state'] != 'approved':
        sys.exit(f'模型 {name} 最近一次工坊迴圈沒有通過，不能發布。先跑：python orchestrator.py model {name}')
    if st.get('dry_run') and not args.allow_dry_run:
        sys.exit('最近一次迴圈是 dry-run（沒有真的呼叫 AI，審查是假的），不能當成真的通過。請拿掉 --dry-run 再跑；只是測試流程才加 --allow-dry-run。')
    cur = sha256(d / 'model.js')
    if cur != st['model_sha']:
        sys.exit('model.js 在通過審查之後又被改動了，通過紀錄已失效。請重新跑一次 model 迴圈。')
    prev = released_info(name)
    ver = (prev['version'] + 1) if prev else 1
    out = RELEASED / name; out.mkdir(parents=True, exist_ok=True)
    shutil.copy(d / 'model.js', out / 'model.js')
    last_shots = sorted((ROOT / 'workspace' / f"model-{name}-{st['run_id']}" / 'shots').glob('r*'))
    stats = json.loads(read(last_shots[-1] / 'gate-result.json'))['stats'] if last_shots else None
    info = {'name': name, 'version': ver, 'sha256': cur, 'spec_sha256': sha256(d / 'spec.json'),
            'released_at': datetime.datetime.now().isoformat(timespec='seconds'), 'approved_run': st['run_id'], 'dry_run': bool(st.get('dry_run')), 'stats': stats}
    (out / 'release.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), 'utf-8')
    with (out / 'history.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(info, ensure_ascii=False) + '\n')
    log(f'已發布 {name} v{ver} → {out / "model.js"}')


def cmd_unrelease(args):
    f = RELEASED / args.name / 'release.json'
    if not f.exists():
        sys.exit('這個模型目前沒有發布。')
    f.rename(f.with_name(f'release.revoked.{datetime.datetime.now():%Y%m%d-%H%M%S}.json'))
    log(f'已撤回 {args.name} 的發布；遊戲階段會被擋下，直到重新發布。')


def cmd_lab(args):
    model_dir(args.name)
    out = ROOT / 'workspace' / 'lab' / f'{args.name}.html'
    ok, o = build_lab(args.name, out)
    if not ok:
        sys.exit(o)
    log('檢視器：', out)
    webbrowser.open(out.as_uri())


def cmd_status(args):
    log('模型狀態')
    for d in sorted(p for p in MODELS.iterdir() if p.is_dir() and not p.name.startswith('_')):
        st, rel = latest_model_run(d.name), released_info(d.name)
        run = st['state'] if st else '未跑過'
        relt = '未發布'
        if rel:
            same = (RELEASED / d.name / 'model.js').exists() and sha256(RELEASED / d.name / 'model.js') == rel['sha256']
            cur = (d / 'model.js').exists() and sha256(d / 'model.js') == rel['sha256']
            relt = f"v{rel['version']}" + ('（dry-run 假通過）' if rel.get('dry_run') else '') + ('' if same else '（發布檔被改動！）') + ('' if cur else '（工坊版本比發布版新）')
        print(f'  {d.name:12s} 最近一次工坊迴圈：{run:10s} 發布：{relt}')
    miss = [m for m in CFG.get('uses_models', []) if not released_info(m)]
    print('遊戲用到的模型：', CFG.get('uses_models', []))
    print('遊戲整合階段：', '可以開工' if not miss else f'被擋住（尚未發布：{miss}）')


# =====================================================================
# 第二段：遊戲整合
# =====================================================================
def gate_models():
    """遊戲只能用「已發布且沒被動過」的模型，也不能直接引用工坊裡的未發布版本"""
    bad = []
    for m in CFG.get('uses_models', []):
        rel = released_info(m)
        f = RELEASED / m / 'model.js'
        if not rel:
            bad.append(f'模型 {m} 尚未發布（或已撤回）'); continue
        if not f.exists() or sha256(f) != rel['sha256']:
            bad.append(f'模型 {m} 的發布檔與紀錄不符（被改動過）')
    # 掃遊戲裡所有程式檔的引用路徑（import / export from / import() / require，可以跨行），
    # 把相對路徑換算成實際位置：落在模型工坊裡、但不在 _released 底下，就是偷用未發布的版本
    for p in walk_files(PROJECT):
        if p.suffix.lower() not in SRC_EXT:
            continue
        text = read(p)
        for m in IMPORT_RE.finditer(text):
            spec = m.group(1).split('?')[0].split('#')[0]
            if not spec.startswith(('./', '../')):
                continue                                   # 套件名稱（例如 three）不是檔案路徑，不用檢查
            target = (p.parent / spec).resolve()
            if inside(target, MODELS) and not inside(target, RELEASED):
                ln = text.count('\n', 0, m.start(1)) + 1
                bad.append(f'{p.relative_to(PROJECT)}:{ln} 直接引用了未發布的模型（{spec}）；只能引用 models/_released/ 裡的版本')
    return {'ok': not bad, 'problems': bad}


def gate_build():
    rc, out = sh(CFG['build_cmd'], PROJECT, 600)
    return {'ok': rc == 0, 'output': tail(out, 2500)}


def gate_browser(shots_dir):
    entry = PROJECT / CFG['entry']
    if not entry.exists():
        return {'ok': False, 'errors': [f'找不到建置產物 {entry}'], 'shots': [], 'perf': None, 'violations': [], 'warnings': []}
    cmd = [sys.executable, str(ROOT / 'gates' / 'browser_gate.py'), '--entry', str(entry),
           '--spec', str((ROOT / CFG['shots_spec']).resolve()), '--out', str(shots_dir), '--viewport', CFG['viewport']]
    if CFG.get('require_webgpu', True):
        cmd += ['--require-webgpu']
    if browser_args():
        cmd += ['--browser-args=' + browser_args()]
    if CFG.get('headed'):
        cmd += ['--headed']
    rc, out = sh(cmd, ROOT, 900)
    f = Path(shots_dir) / 'gate-result.json'
    if rc != 0 or not f.exists():
        return {'ok': False, 'errors': ['瀏覽器關卡本身當掉：' + tail(out, 1500)], 'shots': [], 'perf': None, 'violations': [], 'warnings': []}
    return json.loads(f.read_text('utf-8'))


def gate_perf(browser):
    p, t = browser.get('perf'), CFG.get('perf', {})
    if not p or (t.get('target_avg_ms') is None and t.get('target_p95_ms') is None):
        return {'ok': True, 'note': '沒設定效能目標，略過'}
    bad = []
    if t.get('target_avg_ms') is not None and p['avg_ms'] > t['target_avg_ms']:
        bad.append(f"平均 {p['avg_ms']} ms > 目標 {t['target_avg_ms']} ms")
    if t.get('target_p95_ms') is not None and p['p95_ms'] > t['target_p95_ms']:
        bad.append(f"p95 {p['p95_ms']} ms > 目標 {t['target_p95_ms']} ms")
    note = '（注意：無頭瀏覽器多半用軟體繪圖，數字不代表你的顯示卡）' if p.get('headless') else ''
    return {'ok': not bad, 'note': '；'.join(bad) + note if bad else '達標' + note}


def gate_extra():
    return [{'cmd': c, **dict(zip(('rc', 'output'), sh(c, PROJECT, 600)))} for c in CFG.get('extra_checks', [])]


def models_context():
    """給遊戲開發者與視覺審查者看：目前可用的已發布模型與規格"""
    parts = []
    for m in CFG.get('uses_models', []):
        rel = released_info(m)
        if not rel:
            continue
        parts.append(f"## 模型 {m}（已發布 v{rel['version']}，引用路徑：models/_released/{m}/model.js）\n" + read(MODELS / m / 'spec.md') +
                     f"\n尺寸/統計：{json.dumps(rel.get('stats'), ensure_ascii=False)}")
    return '\n\n'.join(parts) or '（沒有已發布的模型）'


# ---------- 遊戲資料夾的 git 分支 ----------
# 分支就像遊戲的「存檔槽」：你的程式在原本的分支（通常是 main）。做遊戲時，工具另開一個 auto/<時間> 分支給 AI 改，
# 結束後切回你原本的分支。AI 的修改只會在 auto/<時間> 裡，你滿意才自己合併。
BRANCH_NOTE = ROOT / 'workspace' / 'game-branch.json'     # 紙條：這次是從哪個分支開始的（中途被強制停止時，下次靠它切回去）


def git_ok(*args):
    """執行 git，只回傳標準輸出；失敗就丟出錯誤（不能假裝成功、繼續讓 AI 改）"""
    try:
        r = subprocess.run(GIT + list(args), cwd=PROJECT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} 逾時")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗：\n{tail((r.stderr or '') + (r.stdout or ''), 800)}")
    return (r.stdout or '').strip()


def git_start(run_id):
    """開工前的 git 準備，回傳 (原本的分支, AI 用的新分支)。任何一步不對就丟出錯誤，AI 不會開工。"""
    if not (PROJECT / '.git').exists():
        # 遊戲資料夾還沒用 git：幫它建一個，並把目前的檔案存成第一個版本
        if not (PROJECT / '.gitignore').exists():
            (PROJECT / '.gitignore').write_text('node_modules/\ndist/\n', 'utf-8')
        git_ok('init', '-b', 'main'); git_ok('add', '-A'); git_ok('commit', '-m', 'baseline', '--allow-empty')
        log('遊戲資料夾原本沒有 git，已建立 git 並把目前的檔案存成第一個版本（分支 main）')
    try:
        git_ok('rev-parse', '--verify', 'HEAD')
    except RuntimeError:
        raise RuntimeError(f'遊戲資料夾的 git 還沒有任何 commit。請先 commit 一次（git -C "{PROJECT}" add -A，再 git -C "{PROJECT}" commit -m 初始版本）再開始。')
    cur = git_ok('rev-parse', '--abbrev-ref', 'HEAD')
    if cur.startswith('auto/'):
        # 上一次被中途強制停止（例如按了介面的「停止」），沒切回原本的分支：先收尾
        try:
            note = json.loads(read(BRANCH_NOTE, '{}') or '{}')
        except json.JSONDecodeError:
            note = {}
        if note.get('project') != str(PROJECT) or not note.get('base'):
            raise RuntimeError(f'遊戲資料夾停在 {cur} 分支（上次中途被停止），但找不到原本是哪個分支的紀錄。'
                               f'請自己切回你的分支（例如 git -C "{PROJECT}" checkout main）再開始。')
        if git_ok('status', '--porcelain'):
            git_ok('add', '-A'); git_ok('commit', '-m', '中途停止時留下的修改')
        git_ok('checkout', note['base'])
        log(f"上次中途被停止，停在 {cur}：已把留下的修改存進 {cur}，並切回 {note['base']}")
        cur = note['base']
    if cur == 'HEAD':
        raise RuntimeError(f'遊戲資料夾目前不在任何分支上（detached HEAD）。請先切到你的分支（例如 git -C "{PROJECT}" checkout main）再開始。')
    dirty = git_ok('status', '--porcelain')
    if dirty:
        raise RuntimeError('遊戲資料夾有還沒 commit 的修改。為了不動到你的分支，這次不開始。\n'
                           '請先 commit（或還原）下面這些檔案，再按一次開始：\n' + tail(dirty, 1500))
    branch = f'auto/{run_id}'
    BRANCH_NOTE.parent.mkdir(parents=True, exist_ok=True)
    BRANCH_NOTE.write_text(json.dumps({'project': str(PROJECT), 'base': cur, 'branch': branch}, ensure_ascii=False), 'utf-8')
    git_ok('checkout', '-b', branch)
    return cur, branch


def git_finish(base, branch):
    """結束時：把還沒存的修改存進 AI 的分支，再切回原本的分支。回傳要寫進報告的說明"""
    try:
        if git_ok('status', '--porcelain'):
            git_ok('add', '-A'); git_ok('commit', '-m', '結束時留下的修改')
        git_ok('checkout', base)
        BRANCH_NOTE.unlink(missing_ok=True)
        log(f'遊戲資料夾已切回 {base}；AI 的修改在 {branch} 分支')
        return f'遊戲資料夾已切回 `{base}`；AI 的修改都在 `{branch}` 分支。'
    except RuntimeError as e:
        log('切回原本的分支失敗：', e)
        return f'**切回 `{base}` 失敗**，遊戲資料夾還停在 `{branch}`。請自己切回：`git -C "{PROJECT}" checkout {base}`\n\n```\n{e}\n```'


def cmd_run(args):
    dry = args.dry_run
    rounds = args.rounds or CFG['max_rounds']
    if not PROJECT.is_dir():
        sys.exit(f'找不到遊戲資料夾 {PROJECT}。請到介面的「設定」確認遊戲資料夾（flow.config.json 的 project_dir）。')
    pre = gate_models()
    if not pre['ok']:
        print('遊戲整合階段被擋下（模型還沒準備好）：')
        [print('  -', p) for p in pre['problems']]
        print('先到模型工坊完成：python orchestrator.py status')
        sys.exit(3)
    run_id, ws = new_run_dir('')
    try:
        base, branch = git_start(run_id)
    except RuntimeError as e:
        shutil.rmtree(ws, ignore_errors=True)          # 剛建的空資料夾，沒開工就不留紀錄
        sys.exit(str(e))
    log('原本的分支：', base, '｜AI 用的分支：', branch)
    (ws / 'messages').mkdir()
    (ROOT / 'workspace' / 'LATEST').write_text(run_id, 'utf-8')
    status = {'kind': 'game', 'run_id': run_id, 'state': 'running', 'round': 0, 'history': [], 'base': base, 'branch': branch}

    def save():
        (ws / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), 'utf-8')

    shots = (ROOT / CFG['shots_spec']).resolve()

    def protect_list():
        """遊戲開發者只能改遊戲資料夾；工具資料夾全部保護，遊戲資料夾裡的鏡頭清單（驗收標準）也保護"""
        files = walk_files(ROOT, exclude=[PROJECT])
        return files + [shots] if inside(shots, PROJECT) and shots.is_file() else files

    feedback = ''
    prev = None                     # 上一輪的結果（判斷「沒改動就沿用」與「卡住」用）
    no_change = same_fail = 0       # 連續「沒任何改動」的輪數、連續「客觀檢查失敗項目完全相同」的輪數
    checklist = lambda: read(ROOT / 'checks' / 'visual-checklist.md')
    try:
        for rnd in range(1, rounds + 1):
            status['round'] = rnd; save()
            log(f'===== 遊戲第 {rnd} 輪 =====')
            entry = {'round': rnd}
            human = read(ROOT / 'human-feedback.md')
            before = git_ok('rev-parse', 'HEAD')

            log('開發者工作中…')
            dev_prompt = (read(ROOT / 'agents' / 'dev.md') + '\n\n---\n# TASK.md\n' + read(ROOT / 'TASK.md') +
                          '\n\n---\n# prompt.md（設計文件）\n' + read(ROOT / 'prompt.md') +
                          '\n\n---\n# 可用的已發布模型（只能用，不能改）\n' + models_context() +
                          f"\n\n---\n# 專案建置指令\n{CFG['build_cmd']}\n\n---\n# 上一輪回饋\n" + (feedback or '（第 1 輪，沒有回饋）') +
                          '\n\n---\n# 人類意見\n' + human + f'\n\n現在是第 {rnd} 輪（最多 {rounds} 輪）。')
            protected = Protect(protect_list)          # 開發者上工前才記下，你在兩輪之間改的檔案不會被誤還原
            dev_out = run_agent('dev', dev_prompt, PROJECT, 'default', CFG['dev_allowed_tools'], CFG['agent_timeout_sec'], dry, 'game_dev')
            reverted, added = protected.restore()
            (ws / 'messages' / f'r{rnd:02d}-dev.md').write_text(dev_out, 'utf-8')
            git_ok('add', '-A'); git_ok('commit', '-m', f'round {rnd}', '--allow-empty')
            diff = git_ok('diff', before, 'HEAD')
            entry['changed'] = bool(diff)

            # 這一輪的「輸入」：遊戲程式、驗收清單、鏡頭清單、任務、已發布模型。跟上一輪完全一樣，重跑結果也會一樣
            inputs = fingerprint(git_ok('rev-parse', 'HEAD^{tree}'), checklist(), read(shots), read(ROOT / 'TASK.md'), models_context())
            shots_dir = ws / 'shots' / f'r{rnd:02d}'
            if prev and inputs == prev['inputs'] and not reverted:
                log('這一輪沒有任何改動：沿用上一輪的檢查與審查結果，不再呼叫審查者（省 AI 用量）')
                gm, gb, gbr, gp, gx, vis_out, code_out = prev['res']
                entry.update({k: prev['entry'][k] for k in ('models', 'build', 'browser', 'perf', 'extra', 'visual', 'code') if k in prev['entry']})
                entry['skipped'] = True
            else:
                log('關卡：模型是否已發布且未被動過…')
                gm = gate_models(); entry['models'] = gm['ok']
                log('關卡：建置…')
                gb = gate_build(); entry['build'] = gb['ok']
                gbr, gp, gx = None, None, []
                if gb['ok']:
                    log('關卡：瀏覽器（錯誤檢查、截圖、幀時間）…')
                    gbr = gate_browser(shots_dir); entry['browser'] = gbr['ok']
                    gp = gate_perf(gbr); entry['perf'] = gp['ok']
                    gx = gate_extra(); entry['extra'] = all(x['rc'] == 0 for x in gx)
                vis_out = code_out = ''
                if gm['ok'] and gb['ok'] and gbr and gbr['ok'] and gp['ok'] and all(x['rc'] == 0 for x in gx) and not reverted:
                    if CFG['reviewers'].get('visual'):
                        log('視覺審查者（只看截圖）…')
                        vis_out = visual_review('reviewer-visual.md', shots_dir, gbr['shots'], {'models.md': models_context()}, checklist(), dry, 'game_visual')
                        (ws / 'messages' / f'r{rnd:02d}-review-visual.md').write_text(vis_out, 'utf-8')
                        entry['visual'] = verdict(vis_out)
                    if CFG['reviewers'].get('code'):
                        log('程式審查者（看 diff）…')
                        code_out = code_review(diff, read(ROOT / 'TASK.md'), PROJECT, dry, 'game_code')
                        (ws / 'messages' / f'r{rnd:02d}-review-code.md').write_text(code_out, 'utf-8')
                        entry['code'] = verdict(code_out)
            gates_ok = bool(gm['ok'] and gb['ok'] and gbr and gbr['ok'] and gp['ok'] and all(x['rc'] == 0 for x in gx) and not reverted)

            # 缺陷分流：缺陷清單裡標了 [模型:名稱]、且嚴重度高或中的，退回模型工坊，不交給遊戲開發者
            model_issues, unknown_tags = {}, {}
            for out in (vis_out, code_out):
                for m, lines in model_defects(out).items():
                    target = model_issues if (MODELS / m / 'spec.json').exists() else unknown_tags
                    target.setdefault(m, []).extend(lines)
            entry['model_defects'] = sorted(model_issues)
            if unknown_tags:
                entry['unknown_model_tags'] = sorted(unknown_tags)
                log('注意：審查者把缺陷標成不存在的模型，沒有退件：', '、'.join(sorted(unknown_tags)))

            fb = [f'# 第 {rnd} 輪回饋', '']
            if entry.get('skipped'):
                fb += ['- 這一輪你沒有做任何修改，所以沒有重新檢查。下面是上一輪的結果，問題都還在。', '']
            if reverted:
                fb += [f"- 警告：你改了遊戲資料夾以外、或驗收標準的檔案（{'、'.join(reverted)}），已還原。只能改遊戲資料夾裡的程式。", '']
            if added:
                fb += [f"- 警告：你在遊戲資料夾以外新增了檔案（{'、'.join(added)}）。只能改遊戲資料夾裡的程式。", '']
            if unknown_tags:
                fb += [f"- 審查者把缺陷標成不存在的模型（{'、'.join(sorted(unknown_tags))}），沒有退回模型工坊；這些缺陷請當成遊戲的問題處理。", '']
            fb.append('## 客觀關卡')
            fb.append(f"- 模型檢查：{'通過' if gm['ok'] else '失敗'}")
            fb += ['  - ' + p for p in gm['problems']]
            fb.append(f"- 建置：{'通過' if gb['ok'] else '失敗'}")
            if not gb['ok']:
                fb.append('```\n' + gb['output'] + '\n```')
            if gbr:
                fb.append(f"- 瀏覽器檢查：{'通過' if gbr['ok'] else '失敗'}")
                fb += ['  - ' + e for e in gbr['errors'] + gbr['violations']]
                fb.append(f"- 效能：{gp['note']}" + (f" ｜ 實測 {gbr['perf']}" if gbr.get('perf') else ''))
                for x in gx:
                    fb.append(f"- 自訂檢查 `{x['cmd']}`：{'通過' if x['rc'] == 0 else '失敗'}" + ('' if x['rc'] == 0 else '\n```\n' + tail(x['output'], 1200) + '\n```'))
            if vis_out:
                fb += ['', '## 視覺審查者意見（標 [模型:…] 的是模型本身的問題，不用你修；其中嚴重度高、中的已退回模型工坊）', vis_out]
            if code_out:
                fb += ['', '## 程式審查者意見', code_out]
            feedback = '\n'.join(fb)
            (ws / 'messages' / f'r{rnd:02d}-feedback.md').write_text(feedback, 'utf-8')

            approved = gates_ok and not model_issues and all(entry.get(k) == 'APPROVED' for k in ('visual', 'code') if CFG['reviewers'].get(k))
            entry['approved'] = bool(approved)
            status['history'].append(entry); save()
            log('本輪結果：', json.dumps(entry, ensure_ascii=False))
            if approved:
                status['state'] = 'approved'; break
            if model_issues:                    # 模型有問題：遊戲開發者修不了，停下來退件
                for m, lines in model_issues.items():
                    with (MODELS / m / 'reopen.md').open('a', encoding='utf-8') as f:
                        f.write(f'\n## 來自遊戲整合 {run_id} 第 {rnd} 輪\n' + '\n'.join('- ' + l.lstrip('-* ').strip() for l in lines) + '\n')
                    rf = RELEASED / m / 'release.json'          # 撤回發布：模型修好並重新發布之前，遊戲階段不能再開工
                    if rf.exists():
                        rf.rename(rf.with_name(f'release.revoked.{run_id}.json'))
                status['state'] = 'blocked_on_model'; status['blocked_models'] = sorted(model_issues); break
            # 卡住偵測：比對「實際內容」，不比對 AI 評語（AI 每次用字都不同，比評語永遠不會判定卡住）
            fails = sorted(gm['problems'] + ([] if gb['ok'] else ['建置失敗：' + re.sub(r'\d+', '', gb['output'])]) +
                           ((gbr['errors'] + gbr['violations']) if gbr else []) +
                           [f"自訂檢查失敗：{x['cmd']}" for x in gx if x['rc'] != 0])
            no_change = no_change + 1 if entry.get('skipped') else 0
            same_fail = same_fail + 1 if (prev and fails and fails == prev['fails']) else 0
            prev = {'inputs': inputs, 'res': (gm, gb, gbr, gp, gx, vis_out, code_out), 'entry': entry, 'fails': fails}
            if rnd > 1 and max(no_change, same_fail) + 1 >= CFG['stall_rounds']:
                status['state'] = 'stalled'
                status['stall_reason'] = (f'連續 {no_change + 1} 輪沒有任何改動' if no_change >= same_fail
                                          else f'連續 {same_fail + 1} 輪客觀檢查失敗的項目完全相同')
                break
        else:
            status['state'] = 'max_rounds'
    except RuntimeError as e:                   # git 出錯：停下來，不讓 AI 在不對的分支上繼續改
        status['state'] = 'error'; status['error'] = str(e)
        log('出錯停止：', e)
    finally:
        status['git'] = git_finish(base, branch)  # 不管怎麼結束（包括 Ctrl+C），都切回原本的分支
        if status['state'] == 'running':
            status['state'] = 'error'; status.setdefault('error', '流程被中斷')
        save()
    write_report(ws, status)
    print('\n' + '=' * 60 + f'\n遊戲流程結束：{status["state"]}｜報告：{ws / "REPORT.md"}\n' + '=' * 60)
    notify(status['state'], ws / 'REPORT.md')


def write_report(ws, status):
    names = {'approved': '通過（全部關卡與審查者都同意）', 'max_rounds': '達到輪數上限，仍未通過', 'stalled': '卡住',
             'blocked_on_model': '被模型擋住（模型有問題，已退回模型工坊）', 'error': '出錯停止'}
    base, branch = status['base'], status['branch']
    L = [f"# 這次做遊戲的報告：{names.get(status['state'], status['state'])}", '',
         f"- 執行編號：{status['run_id']}", f'- 原本的分支：`{base}`', f'- AI 修改用的分支：`{branch}`', '',
         status.get('git', ''), '']
    if status.get('stall_reason'):
        L += [f"卡住的原因：{status['stall_reason']}", '']
    if status.get('error'):
        L += ['出錯的原因：', '```', status['error'], '```', '']
    L += ['| 輪 | 有改動 | 模型 | 建置 | 瀏覽器 | 效能 | 視覺 | 程式 | 退回的模型 | 通過 |', '|---|---|---|---|---|---|---|---|---|---|']
    for h in status['history']:
        c = lambda k: {True: '過', False: '不過', None: '—'}.get(h.get(k), h.get(k, '—'))
        chg = '無（沿用上一輪結果）' if h.get('skipped') else ('有' if h.get('changed') else '無（開發者沒改任何檔案）')
        L.append(f"| {h['round']} | {chg} | {c('models')} | {c('build')} | {c('browser')} | {c('perf')} | {h.get('visual', '—')} | {h.get('code', '—')} | {'、'.join(h.get('model_defects', [])) or '—'} | {c('approved')} |")
    unknown = sorted({m for h in status['history'] for m in h.get('unknown_model_tags', [])})
    if unknown:
        L += ['', f"注意：審查者把缺陷標成不存在的模型（{'、'.join(unknown)}），沒有退件。請看 messages/ 裡的審查意見，自己判斷要不要手動退件。"]
    if status['state'] == 'blocked_on_model':
        L += ['', '## 模型被退件', '', *[f'- `{m}`：缺陷已寫進 `models/{m}/reopen.md`，下次跑 `python orchestrator.py model {m}` 時開發者會看到' for m in status.get('blocked_models', [])],
              '', '修好、通過、你確認並重新 release 之後，再做一次遊戲。']
    L += ['', '## 你現在要做的（人類檢查點）', '',
          f'1. 看每一輪的意見：`workspace/{status["run_id"]}/messages/`', f'2. 看截圖：`workspace/{status["run_id"]}/shots/`',
          f'3. 親自玩一次：先切到 AI 的分支 `git -C "{PROJECT}" checkout {branch}`，再打開建置產物',
          f'4. 看改了什麼：`git -C "{PROJECT}" diff {base}..{branch}`',
          f'5. 滿意才合併：`git -C "{PROJECT}" checkout {base}` 然後 `git -C "{PROJECT}" merge {branch}`',
          '6. 不滿意：把意見寫進 human-feedback.md（重要的規則用 add-check 存進驗收清單），再做一次']
    (ws / 'REPORT.md').write_text('\n'.join(L), 'utf-8')


def notify(state, report):
    if CFG.get('notify_cmd'):
        sh(CFG['notify_cmd'].replace('{state}', state).replace('{report}', str(report)), ROOT, 30)


def cmd_gates(args):
    ws = ROOT / 'workspace' / 'gates-only'
    shutil.rmtree(ws, ignore_errors=True); ws.mkdir(parents=True)
    gm = gate_models(); log('模型檢查：', '通過' if gm['ok'] else '失敗'); [print('  -', p) for p in gm['problems']]
    gb = gate_build(); log('建置：', '通過' if gb['ok'] else '失敗'); print(gb['output'] if not gb['ok'] else '')
    if not gb['ok']:
        return 1
    br = gate_browser(ws)
    log('瀏覽器檢查：', '通過' if br['ok'] else '失敗'); [print('  -', e) for e in br['errors'] + br['violations']]
    log('截圖：', [s['file'] for s in br['shots']]); log('幀時間：', br['perf']); log('效能關卡：', gate_perf(br)['note'])
    for x in gate_extra():
        log('自訂檢查', x['cmd'], '通過' if x['rc'] == 0 else '失敗')
    log('輸出資料夾：', ws)
    return 0 if (br['ok'] and gm['ok']) else 1


def cmd_add_check(args):
    p = (MODELS / args.model / 'checklist.md') if args.model else (ROOT / 'checks' / 'visual-checklist.md')
    if args.model:
        model_dir(args.model)
    with p.open('a', encoding='utf-8') as f:
        f.write(f"\n- {args.text}（{datetime.date.today()} 加入）")
    log('已加入驗收清單：', p.relative_to(ROOT), '←', args.text)


def main():
    ap = argparse.ArgumentParser(description='遊戲開發自動流程協調器（模型工坊 → 發布 → 遊戲整合）')
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('model'); m.add_argument('name'); m.add_argument('--rounds', type=int); m.add_argument('--dry-run', action='store_true')
    for k in ('lab', 'unrelease'):
        sub.add_parser(k).add_argument('name')
    rl = sub.add_parser('release'); rl.add_argument('name'); rl.add_argument('--allow-dry-run', action='store_true')
    sub.add_parser('status')
    r = sub.add_parser('run'); r.add_argument('--rounds', type=int); r.add_argument('--dry-run', action='store_true')
    sub.add_parser('gates')
    c = sub.add_parser('add-check'); c.add_argument('text'); c.add_argument('--model')
    a = ap.parse_args()
    sys.exit({'model': cmd_model, 'lab': cmd_lab, 'release': cmd_release, 'unrelease': cmd_unrelease, 'status': cmd_status,
              'run': cmd_run, 'gates': cmd_gates, 'add-check': cmd_add_check}[a.cmd](a) or 0)


if __name__ == '__main__':
    main()

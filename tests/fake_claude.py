# 假的 claude：不呼叫任何 AI，依環境變數 FAKE_SCENARIO 回傳固定內容，並記錄收到的參數
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
prompt = sys.stdin.read()
mode = args[args.index('--permission-mode') + 1]
tools = args[args.index('--tools') + 1]
allowed = []
if '--allowedTools' in args:
    i = args.index('--allowedTools') + 1
    while i < len(args) and not args[i].startswith('--'):
        allowed.append(args[i]); i += 1
hook = None                                      # --settings 設定檔裡掛的 hook 指令（沒有就是 None）
if '--settings' in args:
    s = json.loads(Path(args[args.index('--settings') + 1]).read_text('utf-8'))
    hook = s['hooks']['PreToolUse'][0]['hooks'][0]['command']
retry = '【協調器：你上一次的輸出格式不對】' in prompt
# 審查者的隔離資料夾裡有哪些檔案（開發者的工作資料夾可能很大，例如有 .git，不列）
files = sorted(p.relative_to(Path.cwd()).as_posix() for p in Path.cwd().rglob('*') if p.is_file()) if tools == 'Read' else None
with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as f:
    f.write(json.dumps({'mode': mode, 'tools': tools, 'allowed': allowed, 'cwd': os.getcwd(), 'hook': hook,
                        'rules': os.environ.get('FLOW_GUARD_RULES'), 'retry': retry, 'prompt': prompt, 'files': files}, ensure_ascii=False) + '\n')


def review(verdict, defects, body='## 我最不確定的 3 件事\n- （假的審查者）'):
    print(body + '\n\n```json\n' + json.dumps({'verdict': verdict, 'defects': defects}, ensure_ascii=False) + '\n```')


sc = os.environ.get('FAKE_SCENARIO', '')
sb = Path(os.environ['FAKE_SB'])
if mode == 'acceptEdits':                       # 開發者
    m = re.search(r'現在是第 (\d+) 輪', prompt)
    rnd = int(m.group(1)) if m else 1
    if rnd == 1 and sc.startswith('game'):
        p = Path.cwd() / 'index.html'
        p.write_text(p.read_text('utf-8') + f'\n<!-- round {rnd} -->\n', 'utf-8')
    if rnd == 1 and sc == 'game_tamper':        # 偷改驗收清單、偷加檔案、偷改鏡頭清單（模擬用 Bash 寫檔，守門員擋不到，要靠事後還原）
        c = sb / 'checks' / 'visual-checklist.md'
        c.write_text(c.read_text('utf-8') + '\n- CHEAT\n', 'utf-8')
        (sb / 'checks' / 'cheat-new.md').write_text('cheat', 'utf-8')
        s = Path.cwd() / 'shots.json'
        s.write_text('{"shots": []}', 'utf-8')
    print('## 這一輪做了什麼\n- （假的開發者）')
elif tools == 'Read':                            # 視覺審查者
    if sc == 'game_low':
        review('CHANGES_REQUESTED', [{'severity': '低', 'target': '模型', 'model': 'axe', 'where': '01 左上', 'problem': '刀口稍鈍'},
                                     {'severity': '中', 'target': '遊戲', 'where': '01', 'problem': '光線太暗'}],
               '## 逐條對照驗收清單\n1. 斧頭 [模型:axe] 看起來正常：符合\n\n## 我最不確定的 3 件事\n- [高][模型:axe] 的握柄')
    elif sc == 'game_high':
        review('CHANGES_REQUESTED', [{'severity': '高', 'target': '模型', 'model': 'axe', 'where': '01 中間', 'problem': '斧頭像鋤頭'},
                                     {'severity': '中', 'target': '模型', 'model': '斧頭', 'where': '01', 'problem': '不存在的模型'}])
    elif sc == 'game_badfmt' and not retry:      # 第一次格式寫錯（缺 verdict），重叫後才寫對
        print('## 缺陷清單\n（無）\n\n```json\n{"defects": []}\n```')
    elif sc == 'game_badfmt_always':             # 怎麼叫都寫錯：只有舊格式的 VERDICT 行，沒有 JSON
        print('## 缺陷清單\n（無）\n\nVERDICT: APPROVED')
    else:
        review('APPROVED', [])
else:                                            # 程式審查者
    review('APPROVED', [])

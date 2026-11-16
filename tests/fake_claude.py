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
with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as f:
    f.write(json.dumps({'mode': mode, 'tools': tools, 'allowed': allowed, 'cwd': os.getcwd()}, ensure_ascii=False) + '\n')

sc = os.environ.get('FAKE_SCENARIO', '')
sb = Path(os.environ['FAKE_SB'])
if mode == 'acceptEdits':                       # 開發者
    m = re.search(r'現在是第 (\d+) 輪', prompt)
    rnd = int(m.group(1)) if m else 1
    if rnd == 1 and sc.startswith('game'):
        p = Path.cwd() / 'index.html'
        p.write_text(p.read_text('utf-8') + f'\n<!-- round {rnd} -->\n', 'utf-8')
    if rnd == 1 and sc == 'game_tamper':        # 偷改驗收清單、偷加檔案、偷改鏡頭清單
        c = sb / 'checks' / 'visual-checklist.md'
        c.write_text(c.read_text('utf-8') + '\n- CHEAT\n', 'utf-8')
        (sb / 'checks' / 'cheat-new.md').write_text('cheat', 'utf-8')
        s = Path.cwd() / 'shots.json'
        s.write_text('{"shots": []}', 'utf-8')
    print('## 這一輪做了什麼\n- （假的開發者）')
elif tools == 'Read':                            # 視覺審查者
    if sc == 'game_low':
        print('## 逐條對照驗收清單\n1. 斧頭 [模型:axe] 看起來正常：符合\n\n## 缺陷清單\n1. [低][模型:axe] 01 左上：刀口稍鈍\n2. [中][遊戲] 01：光線太暗\n\n'
              '## 我最不確定的 3 件事\n- [模型:axe] 的握柄\n\nVERDICT: CHANGES_REQUESTED')
    elif sc == 'game_high':
        print('## 缺陷清單\n1. [高][模型:axe] 01 中間：斧頭像鋤頭\n2. [中][模型:斧頭] 01：不存在的模型\n\nVERDICT: CHANGES_REQUESTED')
    else:
        print('## 缺陷清單\n（無）\n\nVERDICT: APPROVED')
else:                                            # 程式審查者
    print('## 問題清單\n（無）\n\nVERDICT: APPROVED')

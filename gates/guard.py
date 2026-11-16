"""
改檔守門員（Claude Code 的 PreToolUse hook）

AI 開發者每次要用 Edit／Write 這類工具改檔「之前」，Claude Code 會先執行這支程式，問「這個檔案可不可以改」。
  - 結束碼 0：放行
  - 結束碼 2：擋下；印到 stderr 的文字會直接回給 AI，它當場就知道要換做法

規則由協調器（orchestrator.py 的 run_agent）放在環境變數 FLOW_GUARD_RULES（JSON 字串）：
  allow_files：可以改的檔案（完整路徑）
  allow_dirs ：這些資料夾裡的檔案都可以改
  deny_files ：就算在 allow_dirs 裡也不能改（例如遊戲資料夾裡的鏡頭清單）
  hint       ：被擋下時告訴 AI 它能改什麼

注意：
  - 這支程式自己出錯（沒有規則、讀不懂輸入）時一律擋下（拿不準就不放行）。
  - 它只擋得到 Claude 的改檔工具，擋不到 Bash（例如 node 指令直接寫檔），
    所以協調器「做完後比對、還原」的機制（Protect）仍然保留，當最後一道防線。
"""
import json, os, sys


def norm(p, base=None):
    """轉成可以直接比較的完整路徑：相對路徑以 base 為起點、解開 .. 與捷徑、Windows 不分大小寫"""
    if base and not os.path.isabs(p):
        p = os.path.join(base, p)
    return os.path.normcase(os.path.realpath(p))


def inside(p, d):
    """p 是否在資料夾 d 裡面（兩者都要先經過 norm）"""
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:                   # 不同磁碟機（例如 C: 與 D:）沒有共同路徑
        return False


def allowed(path, rules):
    if path in {norm(f) for f in rules.get('deny_files', [])}:
        return False
    if path in {norm(f) for f in rules.get('allow_files', [])}:
        return True
    return any(inside(path, norm(d)) for d in rules.get('allow_dirs', []))


def block(msg):
    print(msg, file=sys.stderr)
    return 2


def main():
    sys.stderr.reconfigure(encoding='utf-8')            # 錯誤訊息有中文；Windows 預設編碼不是 UTF-8
    try:
        rules = json.loads(os.environ['FLOW_GUARD_RULES'])
        data = json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))   # Claude Code 從標準輸入傳 JSON 進來（utf-8-sig：開頭有 BOM 也讀得懂）
        ti = data.get('tool_input') or {}
        raw = ti.get('file_path') or ti.get('notebook_path')         # Edit／Write 用 file_path，NotebookEdit 用 notebook_path
        if not raw:
            return block(f"看不出這次要改哪個檔案（工具：{data.get('tool_name')}），先擋下。{rules.get('hint', '')}")
        if allowed(norm(raw, data.get('cwd')), rules):
            return 0
        return block(f"不能改這個檔案：{raw}。這是驗收標準或工具本身，就算改了也會被還原。{rules.get('hint', '')}")
    except Exception as e:                               # 守門員自己出錯：擋下，不放行
        return block(f'改檔檢查程式出錯，這次修改先擋下：{type(e).__name__}: {e}')


if __name__ == '__main__':
    sys.exit(main())

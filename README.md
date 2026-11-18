# game-dev-flow

兩段式遊戲開發自動流程：模型先在獨立工坊做完、通過客觀檢查與 AI 審查、人工確認發布後，遊戲才能使用。用 Claude Code 無頭模式協調多個 agent。

這是一套「怎麼開發遊戲」的流程工具，不是遊戲內容。

> **核心規則**：模型要先在獨立環境做完、測試沒問題、你確認並「發布」，遊戲才能使用。遊戲裡發現模型本身有問題，不准在遊戲裡硬補，要退回模型工坊。

## 流程概覽

```
【第一段：模型工坊】自動重複，直到通過
  AI 寫 model.js → 客觀檢查＋多角度截圖 → 只看截圖的 AI 審查
    │
    ▼
【人工確認】你用檢視器轉著看，滿意才「發布」
    │
    ▼
【第二段：遊戲整合】自動重複，AI 只在 auto/<時間> 分支上改
  AI 改遊戲程式 → 建置＋瀏覽器檢查＋截圖 → 視覺審查＋程式審查
    │
    ├─ 模型本身有問題 → 撤回發布，退回第一段
    ▼
【你】親自玩，滿意才合併回你的分支
```

完整流程圖與設計理由見 [docs/workflow.md](docs/workflow.md)。

## 特色

- **停止條件寫在程式裡**：過不過關由協調器判斷，不交給 AI 自己說「完成了」。
- **客觀關卡先過，再給 AI 審查**：尺寸、三角形數、console 錯誤、外部素材、幀時間、WebGPU 後端都由程式檢查。
- **只看畫面的審查者**：視覺審查者在隔離資料夾裡只看得到截圖與清單，看不到程式碼和開發者的自評；結論以固定格式的 JSON 回報。
- **AI 改不了驗收標準**：改檔前用 hook 擋下，做完再比對還原，兩層保護。
- **不動你的分支**：做遊戲時 AI 只在 `auto/<時間>` 分支上改，滿意才由你合併。
- **累積式驗收清單**：你每抓到一個「看起來不對」的錯就存一條，之後每一輪都會被檢查。
- **只准 WebGPU**：用 Three.js 的 WebGPURenderer，拿不到 WebGPU 就報錯停止，不退回 WebGL。檢視器與關卡都會確認實際用的是 WebGPU 後端。

## 快速開始

### 需要

- Node.js、Python（測試環境是 3.10）、Git
- [Claude Code](https://claude.com/claude-code)（終端機打 `claude --version` 有反應），用你的訂閱登入
- 支援 WebGPU 的顯示卡

### 安裝

在專案根目錄執行（只做一次）：

```
npm install                                                   # 安裝 three 與 esbuild
python -m venv .venv                                          # 建立專案專用的 Python 虛擬環境
.venv/Scripts/python.exe -m pip install -r requirements.txt   # 安裝 Playwright（瀏覽器自動化）
.venv/Scripts/python.exe -m playwright install chromium       # 下載關卡用的 Chromium
```

> 電腦裡如果設了環境變數 `ANTHROPIC_API_KEY`，`claude -p` 會改用它，變成按 API 用量計費。用訂閱就不要設。詳見 [docs/claude-code.md](docs/claude-code.md)。

### 開始用

雙擊 `gui.bat` 開啟圖形化介面（`http://127.0.0.1:8765/`）；習慣 PowerShell 的話，對 `gui.ps1` 按右鍵「用 PowerShell 執行」，或在終端機執行 `.\gui.ps1`：
1. 按上方「＋ 新增專案」：一個遊戲一個專案，專案名稱和遊戲資料夾名稱由你取。
2. 在「① 模型工坊」按「新增模型」，填完規格後按「開始做模型」。

也可以用指令：

```
.venv/Scripts/python.exe orchestrator.py new-project game-example       # 建立專案（並設為目前專案）
.venv/Scripts/python.exe orchestrator.py status                          # 看目前狀態
.venv/Scripts/python.exe orchestrator.py model axe --dry-run --rounds 1  # 先乾跑一輪（不消耗額度）
.venv/Scripts/python.exe orchestrator.py model axe                       # 做模型
.venv/Scripts/python.exe orchestrator.py lab axe                         # 開檢視器自己看
.venv/Scripts/python.exe orchestrator.py release axe                     # 滿意就發布
.venv/Scripts/python.exe orchestrator.py run                             # 做遊戲
```

指令都針對「目前專案」；要操作別的專案，在指令後面加 `--project <專案名>`，或用 `use <專案名>` 切換。

你的東西都在 `projects/<專案名>/` 裡，一個遊戲一個資料夾：

```
projects/game-example/
├── flow.config.json、TASK.md、prompt.md、human-feedback.md、checks/   ← 從 templates/ 建立
├── agents.local/      （選用）這個遊戲客製的 AI 角色指令
├── models/            這個遊戲的模型
├── game/              你的遊戲程式（名稱由你取，有自己的 git）
└── workspace/         執行紀錄
```

`projects/` 整個不在工具的版控裡。

### 更新

```
git pull
```

只會更新工具本身，`projects/` 裡的東西都不會被動到。工具新增的設定項目，你的設定檔裡沒有也會自動用預設值。想客製 AI 角色指令，放進專案的 `agents.local/`（見 [docs/conventions.md](docs/conventions.md)），不要直接改 `agents/`，否則更新時會衝突。

## 文件

| 文件 | 內容 |
|---|---|
| [docs/workflow.md](docs/workflow.md) | 為什麼分兩段、完整流程圖、名詞解釋 |
| [docs/usage.md](docs/usage.md) | 介面與所有指令的用法：做模型、發布、做遊戲、退件、跑測試 |
| [docs/rules.md](docs/rules.md) | 各角色能看能改什麼、保護機制、停止規則 |
| [docs/conventions.md](docs/conventions.md) | 資料夾結構、模型與遊戲的約定、鏡頭清單 shots.json 格式 |
| [docs/claude-code.md](docs/claude-code.md) | 協調器怎麼呼叫 Claude Code、登入與計費、審查結果格式、各角色選模型 |
| [docs/limitations.md](docs/limitations.md) | 已知限制 |

## 目前狀態

- 還沒用真的 Claude 實跑過；流程邏輯已用假的 claude 測過（`tests/run_tests.py`，109 項）。第一次實跑請先 `--rounds 1`。
- 專案裡沒有範例模型與範例遊戲，要從「新增模型」和你自己的遊戲開始。
- 審查者是 AI，會漏看；最後一關一定是你親自看、親自玩。

其他限制見 [docs/limitations.md](docs/limitations.md)。

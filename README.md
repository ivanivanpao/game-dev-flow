# 遊戲開發自動流程（兩段式：模型工坊 → 遊戲整合）

這是一套「怎麼開發遊戲」的流程（不是遊戲內容）。核心規則只有一條：

> **模型要先在獨立環境做完、測試沒問題、你確認並「發布」，遊戲才能使用。遊戲裡發現模型本身有問題，不准在遊戲裡硬補，要退回模型工坊。**

繪圖技術：**Three.js 的 WebGPURenderer，只准 WebGPU**（拿不到 WebGPU 就報錯停止，不退回 WebGL）。檢視器與關卡都會確認實際用的是 WebGPU 後端。

它把兩個來源合在一起：

| 來源 | 取了什麼 |
|---|---|
| 你的多 agent 分工設計（黑板模式） | 協調器程式叫醒各 agent、agent 只透過資料夾裡的檔案溝通、每個檔案一位作者、停止條件寫在程式裡 |
| 深淵花園的做法 | 設計文件（場景＋限制＋效能目標＋怎麼自我驗證）、**只看畫面的審查者**、量幀時間、有問題回頭修 |

再加上實戰教訓：**客觀檢查抓不到「這斧頭看起來像鋤頭」，只有人眼看得出來**。所以有「累積式驗收清單」：你每抓到一個錯就存一條，之後每一輪都會被檢查。

---

## 一、為什麼要分兩段

斧頭／鎬子／鏟子這類模型，如果跟遊戲混在一起改，會出現三個問題：

1. 看不清楚：模型被樹、石頭、UI 擋住，審查者與你都看不到細節。
2. 責任不清：畫面怪怪的，不知道是模型有問題還是遊戲擺放有問題。
3. 越補越亂：遊戲程式裡出現一堆「把斧頭縮小一點」「把刀口轉個方向」的補丁，之後換模型全部壞掉。

分開之後，**模型在乾淨的檢視器裡被檢查**（中性燈光、1 公尺格線、座標軸、1.8 公尺人形參考、多角度、特寫），通過才放行。

---

## 二、工具流程圖

圖例：**【你】** 是你要做的事；**【工具】** 是工具自動做、不用 AI 的事；**【AI】** 是呼叫 Claude 做的事。

```
【你】寫模型規格：models/<名稱>/spec.md（長什麼樣）、spec.json（尺寸範圍、截圖角度）
  │
  │  在介面按「開始做模型」（指令：python orchestrator.py model <名稱>）
  ▼
╔══ 第一段：做模型（自動重複，直到通過、跑滿輪數、或卡住）════════════
║
║  ① 【AI】模型開發者：寫或修改 model.js
║        │   （只能改 model.js；改了其他檔案會被還原）
║        ▼
║     這一輪有沒有改任何東西？
║        ├─ 沒有 → 不重新檢查，直接沿用上一輪結果（省用量），算一次「沒改動」
║        ▼ 有
║  ② 【工具】客觀檢查：建出檢視器 → 量尺寸、三角形數… → 從幾個固定角度截圖
║        ├─ 沒過 → 把錯誤寫成回饋，回到 ①
║        ▼ 過了
║  ③ 【AI】視覺審查者：只看截圖和規格，挑毛病
║        ├─ 有問題 → 把意見寫成回饋，回到 ①
║        ▼ 通過
╚══════════════════════════════════════════════════════════════════
  │
  ▼
【你】看截圖、按「開檢視器」自己轉著看
  ├─ 不滿意 → 把意見寫進 models/<名稱>/human-feedback.md，再按一次「開始做模型」
  ▼ 滿意
【你】按「發布」→ model.js 複製到 models/_released/<名稱>/，遊戲只能用這裡的版本
  │
  │  在介面按「開始做遊戲」（指令：python orchestrator.py run）
  ▼
【工具】開工前檢查（任何一項不對就不開始）：
  ・遊戲用到的模型都已發布、而且沒被動過
  ・遊戲資料夾沒有還沒 commit 的修改
  ・記下你原本的分支（例如 main），另開新分支 auto/<時間> 給 AI 改
  │
  ▼
╔══ 第二段：做遊戲（自動重複，直到通過、跑滿輪數、卡住、或被模型擋住）══
║
║  ① 【AI】遊戲開發者：修改你的遊戲程式（project_dir 指定的資料夾）
║        │   （只能改遊戲資料夾；改了驗收標準或工具的檔案會被還原）
║        ▼
║     這一輪有沒有改任何東西？
║        ├─ 沒有 → 不重新檢查，直接沿用上一輪結果，算一次「沒改動」
║        ▼ 有
║  ② 【工具】客觀檢查：模型有沒有被偷改 → 建置 → 開瀏覽器跑遊戲、抓錯誤
║        │   → 照 shots.json 擺鏡頭截圖 → 量幀時間 → 自訂檢查
║        ├─ 沒過 → 回到 ①
║        ▼ 過了
║  ③ 【AI】視覺審查者看截圖挑畫面毛病；程式審查者看這輪改了什麼、挑 bug
║        ├─ 遊戲的問題 → 回到 ①
║        ├─ 模型本身的問題（嚴重度高或中）→ 整個停下，撤回該模型的發布，
║        │                                  缺陷寫進 models/<名稱>/reopen.md，回到第一段重做
║        ▼ 通過
╚══════════════════════════════════════════════════════════════════
  │
  ▼
【工具】把遊戲資料夾切回你原本的分支（AI 的修改留在 auto/<時間>）
  │
  ▼
【你】切到 auto/<時間> 親自玩、看改了什麼；滿意才合併回你的分支
```

**分支是什麼**：git 的分支就像遊戲的「存檔槽」。你的程式存在原本的分支（通常是 `main`）；做遊戲時，工具另開一個存檔槽 `auto/<時間>`，AI 只在那裡改。你的分支不會被動到，滿意才把 AI 的進度合併回來，不滿意就不理它。

**名詞（第一次出現時說明）**
- **協調器**（orchestrator）：決定每一輪誰上場的普通程式（不是 AI）。agent 不會自己醒來，所以需要它叫。
- **關卡**（gate）：不靠 AI 判斷、結果只有過／不過的檢查。
- **審查者**：另外開一個全新的 Claude 做單一任務。它不知道開發者怎麼想，所以不會替作品找藉口。
- **無頭模式**（headless）：不開對話畫面，用 `claude -p` 傳入任務、取得結果。
- **發布**（release）：把通過的 model.js 複製到 `_released/`，並記下它的 sha256（檔案內容算出的指紋，內容只要差一個字，指紋就不同）。
- **隔離**：審查者的工作資料夾建在系統暫存區，只放截圖與清單，看不到程式碼、看不到開發者的自評。

---

## 三、誰能看什麼、能改什麼

| 角色 | 看得到 | 能改 |
|---|---|---|
| 你 | 全部 | spec、清單、human-feedback、TASK、prompt，以及「發布」這個動作 |
| 模型開發者 | 規格、驗收清單、目前的 model.js、上一輪回饋 | **只有 `models/<名稱>/model.js`** |
| 模型視覺審查者 | 只有截圖、每張圖該呈現什麼、規格、清單 | 什麼都不能改（只有 Read） |
| 遊戲開發者 | 任務、設計文件、**已發布模型的規格與 meta**、上一輪回饋 | 只能改遊戲專案；**不能改模型** |
| 遊戲視覺審查者 | 截圖、清單、已發布模型的規格 | 什麼都不能改 |
| 程式審查者 | 本輪 diff、任務（唯讀） | 什麼都不能改 |
| 協調器 | 全部 | workspace/ 的訊息、狀態、報告、發布紀錄 |

溝通規則（來自你的原設計）：每個檔案只有一位作者；只新增不修改舊檔（檔名帶輪數）；每輪只讀最新一份回饋。

**驗收標準與工具本身受保護**：AI 開發者每一輪上工前，協調器會記下整個工具資料夾的檔案（驗收清單、規格、設定、任務、關卡程式、AI 指令、已發布的模型…），做完後比對，被改或被刪就還原，並在回饋裡警告；多出來的新檔案只警告、不刪除。AI 開發者能改的只有：做模型時是那個模型的 `model.js`，做遊戲時是遊戲資料夾（但 `shots.json` 鏡頭清單仍然受保護）。否則開發者可以靠改驗收標準或關卡程式來過關。
注意：AI 開發者工作的那段時間，你自己在介面上改的檔案也會被還原，請等這一輪結束再改。

---

## 四、停止規則（全部寫在 orchestrator.py，不交給 agent 判斷）

| 條件 | 結果 |
|---|---|
| 所有關卡過，且審查者都輸出 `VERDICT: APPROVED` | 通過，停 |
| 跑滿輪數上限（`model_max_rounds` / `max_rounds`） | 停，報告「仍未通過」 |
| 某一輪開發者什麼都沒改（程式、規格、清單都跟上一輪一樣） | 不重新檢查、不呼叫審查者，直接沿用上一輪結果（省用量） |
| 連續 `stall_rounds` 輪「什麼都沒改」，或「客觀檢查失敗的項目完全相同」 | 停，報告「卡住」並寫明原因（不比對 AI 評語，因為 AI 每次用字都不同） |
| 審查者沒有輸出明確的 `VERDICT:` 行 | 一律當沒通過（拿不準就不放行） |
| 任一 agent 逾時／失敗 | 該輪視為沒通過，回饋寫明原因 |
| 審查者在「缺陷清單」裡標了 `[模型:名稱]`，且嚴重度是「高」或「中」（遊戲階段） | 停，狀態 `blocked_on_model`，撤回該模型發布。其他段落或「低」的項目不算；名稱不存在的模型不退件，只在報告裡提醒你 |
| 做遊戲時 git 指令失敗 | 停，狀態 `error`，切回你原本的分支 |
| 模型的最近一次迴圈是 dry-run | 不能發布（除非你明確加 `--allow-dry-run`，只供測試流程） |

---

## 五、資料夾

```
game-dev-flow/
├── README.md
├── flow.config.json            ← 設定
├── orchestrator.py             ← 協調器（所有指令的入口）
├── gui.bat                     ← 雙擊啟動圖形化介面
├── gui/                        ← 圖形化介面（server.py 本機伺服器、index.html 網頁）
├── package.json                ← 模型檢視器需要 three 與 esbuild（根目錄 npm install）
├── requirements.txt            ← Python 套件（playwright）
├── tests/                      ← 工具本身的自動測試（run_tests.py、假的 claude：fake_claude.py）
├── prompt.md / TASK.md / human-feedback.md   ← 遊戲階段：設計文件／本輪任務／你的意見
├── lab/                        ← 模型檢視器（WebGPU；lab.js、lab.template.html、build_lab.mjs）
├── gates/
│   ├── model_gate.py           ← 模型關卡（幾何檢查＋多角度截圖）
│   └── browser_gate.py         ← 遊戲關卡（抓錯誤／截圖／幀時間）
├── agents/                     ← 各 agent 的指令
│   ├── model-dev.md、reviewer-model-visual.md      ← 模型工坊用
│   └── dev.md、reviewer-visual.md、reviewer-code.md ← 遊戲整合用
├── checks/
│   ├── model-checklist.md      ← 所有模型共通的驗收清單
│   └── visual-checklist.md     ← 遊戲的累積式驗收清單
├── models/
│   ├── axe/ pickaxe/ shovel/   ← 範例三個模型，每個都有：
│   │     model.js              ←   純函式 build(THREE)，唯一會被改的檔
│   │     spec.md               ←   模型規格（人話：長什麼樣、座標約定、不可以什麼）
│   │     spec.json             ←   客觀檢查條件＋5 個截圖角度
│   │     checklist.md          ←   這個模型專屬的累積清單
│   │     reopen.md             ←   （遊戲階段退件時自動產生）
│   └── _released/<名稱>/       ← 發布區：model.js、release.json（版本、sha256）、history.jsonl
├── examples/chop-game/         ← 範例遊戲（只引用 _released 裡的模型）
└── workspace/                  ← 執行時自動產生
    ├── model-<名稱>-<時間>/    ← 模型迴圈：messages/、snapshots/（每輪 model.js 快照）、shots/、REPORT.md
    ├── <時間>/                 ← 遊戲迴圈：messages/、shots/、REPORT.md
    └── lab/<名稱>.html         ← `lab` 指令建出的檢視器
```

---

## 六、怎麼用

### 1. 安裝（只做一次）
```
npm install                          # 在 game-dev-flow 根目錄：安裝 three 與 esbuild（檢視器與遊戲打包都會往上層找到它們）
pip install -r requirements.txt      # 安裝 Python 版 Playwright（瀏覽器自動化套件）
playwright install chromium          # 下載 Playwright 專用的 Chromium（會同時下載完整版與無頭精簡版，關卡要用的是完整版）
```
關卡用完整版 Chromium 的新版無頭模式（`channel='chromium'`）加 `--enable-unsafe-webgpu`，不開視窗也能用到真的顯示卡。預設的無頭精簡版只能拿到 SwiftShader 軟體模擬，Three.js 的 WebGPU 繪圖管線會建立失敗、截圖只剩背景色，所以不用它。
另外要先裝好 Claude Code，並確認終端機打 `claude --version` 有反應。

### 圖形化介面（不想打指令就用這個）
雙擊根目錄的 `gui.bat`（或執行 `.venv/Scripts/python.exe gui/server.py`），瀏覽器會自動打開 `http://127.0.0.1:8765/`。下面所有指令與要寫的檔案，都能在介面上完成：

| 分頁 | 能做什麼 |
|---|---|
| 總覽 | 環境是否正常、每個模型的狀態、「下一步」該做什麼（會提醒哪些檔案還有【】沒填） |
| ① 模型工坊 | 新增模型；編輯 spec.md／spec.json／checklist.md／human-feedback.md；做模型（可乾跑）；開檢視器；發布／撤回；加規則 |
| ② 遊戲整合 | 編輯 prompt.md／TASK.md／human-feedback.md／驗收清單／shots.json；做遊戲（可乾跑）；只跑關卡；加規則 |
| 執行紀錄 | 每次執行的報告、每一輪的訊息、截圖與「應該看到什麼」 |
| 設定 | 每個角色用哪個 AI 模型、輪數、審查者開關、遊戲資料夾、WebGPU、效能目標 |
| 進階 | AI 角色指令（agents/*.md）、模型通用清單、設定原始檔 |

介面只接受本機連線，只能改你該寫的檔案（model.js 等由 AI 管的檔案只能看），也只能執行協調器的既有指令。關閉介面：在 `gui.bat` 開出的黑色視窗按 Ctrl+C。

### 2. 看目前狀態
```
python orchestrator.py status
```
列出每個模型：最近一次迴圈結果、有沒有發布、發布的版本、工坊版本是否比發布版新；以及「遊戲現在能不能開工」。

### 3. 第一段：做一個模型
```
python orchestrator.py model axe --dry-run --rounds 1    # 先乾跑一輪驗證流程
python orchestrator.py model axe                         # 真的跑
```
- `model axe`：對 `models/axe/` 跑模型迴圈
- `--dry-run`：AI 的部分用假回覆，關卡是真的跑（不花錢，但**這種通過不能發布**）
- `--rounds N`：最多幾輪（不寫用 `model_max_rounds`）

結束後看 `workspace/model-axe-<時間>/REPORT.md`。

### 4. 人類檢查點：自己轉著看，再發布
```
python orchestrator.py lab axe          # 建出檢視器並用瀏覽器打開：拖曳旋轉、滾輪縮放，有格線與人形參考
python orchestrator.py release axe      # 你滿意了才發布
```
`release` 會檢查：最近一次迴圈是通過的、不是 dry-run、且 model.js 在通過之後沒有再被改動。任何一項不符就拒絕。

不滿意：把意見寫進 `models/axe/human-feedback.md`，穩定的規則存起來，再跑一次：
```
python orchestrator.py add-check "斧頭刀口要與握柄平行，不是垂直" --model axe
```
（`--model axe`：存進這個模型專屬的清單；不加就是存進遊戲的清單。寫法要「看圖就能判斷」，不要寫「要好看」。）

### 5. 第二段：做遊戲（介面的「開始做遊戲」）
```
python orchestrator.py gates               # 只跑客觀關卡，不呼叫 AI
python orchestrator.py run --dry-run --rounds 1
python orchestrator.py run                 # 做遊戲：AI 反覆修改你的遊戲程式
```
- 模型沒發布完，`run` 會直接擋下並列出缺哪些。
- 遊戲資料夾有還沒 commit 的修改，`run` 也會直接擋下並列出是哪些檔案。請先自己 commit，工具不會替你存。
- 開始時記下你原本的分支（例如 `main`），另開分支 `auto/<時間>` 給 AI 改，每輪一個 commit。**你原本的分支不會被動到。**
- 結束時（不管通過、失敗、出錯、或在命令列按 Ctrl+C）會自動切回你原本的分支。AI 的修改留在 `auto/<時間>`。
- 在介面按「停止」是強制結束，工具來不及切回去，遊戲資料夾會暫時停在 `auto/<時間>`。下次按「開始做遊戲」時，工具會先發現「上次沒收尾」，把留下的修改存進那個 `auto/` 分支，再切回你原本的分支，然後才開始。
- 結束後看 `workspace/<時間>/REPORT.md`，切到 `auto/<時間>` 親自玩，滿意才合併。

### 6. 遊戲階段發現模型有問題
審查者會在「缺陷清單」裡、嚴重度高或中的缺陷前標 `[模型:axe]`。協調器會：
1. 把缺陷寫進 `models/axe/reopen.md`
2. 自動撤回 axe 的發布（`release.revoked.*.json`）
3. 停止遊戲迴圈，報告狀態 `blocked_on_model`

接著你重跑 `model axe`（開發者第 1 輪就會看到退件內容，通過後退件檔自動改名為 `reopen.done.*`），再 `release axe`，然後才能再做一次遊戲。

撤回發布用：`python orchestrator.py unrelease axe`。

### 7. 換成你自己的模型／遊戲
- 新模型：複製 `models/axe/` 改名，寫 `spec.md`、`spec.json`（客觀檢查條件＋截圖角度）；再把名稱加進 `flow.config.json` 的 `uses_models`（遊戲用到哪些，就要先發布哪些）。
- 新遊戲：改 `flow.config.json` 的 `project_dir`、`build_cmd`、`entry`、`shots_spec`；鏡頭清單格式照 `examples/chop-game/shots.json`。
- 遊戲裡引用模型的寫法（只能引用 `_released`）：
  ```js
  import { build as buildAxe, meta as axeMeta } from '../../../models/_released/axe/model.js';
  const axe = buildAxe(THREE);        // THREE 由遊戲傳入，模型自己不 import 任何東西
  ```
  直接 import 工坊裡的 `models/axe/model.js` 會被「模型檢查」判為違規。

### 8. 改了工具之後：跑自動測試
改了 `orchestrator.py`、`gates/`、`gui/` 之後，跑一次確認之前修好的行為沒被改壞：
```
.venv/Scripts/python.exe tests/run_tests.py
```
- 會在系統暫存區建一份專案副本，用「假的 claude」（`tests/fake_claude.py`）跑做模型、做遊戲、GUI 的實際流程。**不呼叫真的 Claude、不消耗訂閱額度、不動你真正的專案和遊戲。**
- 每一項印出 `PASS` 或 `FAIL`，最後一行是總結，例如「共 35 項，通過 35 項，失敗 0 項」。大約一兩分鐘。
- 全部通過會自動刪掉副本；有失敗會保留副本並印出位置，方便查原因。
- 如果是**故意**改變某個行為（例如改了退件規則），測試也要跟著改，不然會顯示失敗。

---

## 七、模型與遊戲專案要遵守的約定

**模型（model.js）**
1. 輸出 `meta`（尺寸等數值）與 `build(THREE)`；`THREE` 由外部傳入，模型不 import 任何東西（否則遊戲裡會有兩份 three）。
2. 座標約定寫在 spec.md（例如原點在手握處、握柄沿 -z），**遊戲依賴它**，改了就是新版本、要重新發布。
3. 要給遊戲用的零件放 `group.userData`（例如斧頭的 `head`）。
4. 傳入的 `THREE` 是 `three/webgpu`。可用一般材質；不可用 `onBeforeCompile`、`ShaderMaterial`、GLSL（WebGPU 不支援，`onBeforeCompile` 會被靜默忽略）。自訂著色用節點材質加 `THREE.TSL`。

**遊戲**
1. 暴露測試介面（如 `window.__game`），讓關卡能擺場景、截圖。
2. 所有隨機用可指定種子的亂數，否則每輪截圖不同。
3. 不載入外部素材（關卡會偵測並判不通過）。
4. 建置指令一個就能跑完，結果是一個可直接開啟的 HTML。
5. 只准 WebGPU：用 `three/webgpu` 的 `WebGPURenderer`，`await renderer.init()` 後確認 `renderer.backend.isWebGPUBackend`，再設定 `window.__renderBackend = 'webgpu'`。關卡會檢查這個值（`flow.config.json` 的 `require_webgpu`，預設開啟）。瀏覽器不支援時要顯示訊息並停止，不退回 WebGL。

---

## 八、協調器怎麼呼叫 Claude Code

| 參數 | 意思 |
|---|---|
| `-p` | 無頭模式：不開對話畫面，讀完提示詞、做完就輸出並結束。提示詞用標準輸入傳，避免命令列太長 |
| `--output-format text` | 輸出純文字（協調器要存檔、找 `VERDICT:` 那行） |
| `--tools <清單>` | 限制這個 agent「有哪些工具」。視覺審查者只給 `Read`，而且允許清單寫成 `Read(./**)`，只能讀自己的隔離資料夾，讀不到專案程式碼，也改不了東西 |
| `--allowedTools <清單>` | 這些工具免詢問。無頭模式沒人可以按「允許」，沒列在這裡的會被拒絕 |
| `--permission-mode` | 開發者 `acceptEdits`（可直接改檔）；審查者 `dontAsk`（沒在允許清單的一律拒絕） |
| `--model` | 選填：指定模型。每個角色可以各自設定（見下方），沒設就用 Claude Code 預設 |
| `--max-budget-usd` | 選填：單次呼叫花費上限（`flow.config.json` 的 `max_budget_usd_per_call`） |

**每個角色各自選模型**（`flow.config.json`）：

```json
"model": null,
"models": {
  "model_dev":    "claude-opus-5-5",
  "model_visual": "claude-sonnet-5-5",
  "model_code":   null,
  "game_dev":     "claude-opus-5-5",
  "game_visual":  "claude-sonnet-5-5",
  "game_code":    "claude-sonnet-5-5"
}
```

| 代號 | 角色 |
|---|---|
| `model_dev` | 模型開發者（模型工坊） |
| `model_visual` | 模型視覺審查者 |
| `model_code` | 模型程式審查者（預設關閉，見 `model_reviewers`） |
| `game_dev` | 遊戲開發者 |
| `game_visual` | 遊戲視覺審查者 |
| `game_code` | 遊戲程式審查者 |

查找順序：`models.<代號>` → 沒填（`null`）就用 `model` → 再沒填就用 Claude Code 預設。每次呼叫時終端機會印出該角色用的是哪個模型。

---

## 九、已知限制（誠實列出）

- **「呼叫 Claude Code 的那一段」沒能用真的 Claude 實跑**：製作環境無法通過 Claude Code 驗證。我驗證過的：指令參數存在於 `claude --help`；失敗時會被視為沒通過；並且用一支「假的 claude」腳本測過整個流程邏輯——開發者偷改 spec.md 被還原、審查者永遠不過會判定卡住、遊戲階段審查者回報 `[模型:axe]` 會退件並撤回發布、模型沒發布時遊戲被擋、model.js 改過後不能發布、dry-run 通過不能發布。其餘（模型檢視器、模型關卡、建置、瀏覽器關卡、截圖、git 分支、報告）都用範例實際跑過。第一次在你電腦實跑時，請先 `--rounds 1`。
- **隨附的三個模型的「發布」是 dry-run 測試用的**（沒有真的 AI 審查），打包時我已把發布區清空；你要從 `model axe` 真跑開始。
- **幀時間**：關卡用完整版 Chromium 的無頭模式，會用到真的顯示卡，但無頭模式的畫面合成方式與正常視窗不同，數字只能當參考。要量最接近玩家的效能：`flow.config.json` 設 `"headed": true`，再設定 `perf.target_avg_ms`。
- **範例遊戲 `examples/chop-game` 還是 WebGL 寫法**，尚未改成 WebGPU（樹皮著色器用了 `onBeforeCompile` 的 GLSL，要改寫成 TSL）。在改好之前，它過不了 `require_webgpu` 檢查；要先試跑流程可暫時把 `require_webgpu` 設成 `false`。
- **審查者是 AI，會漏看**。客觀關卡與你的驗收清單才是底線；最後一關一定是你親自看、親自玩。
- **缺陷標籤靠審查者自己標**：標錯（該標 `[模型:…]` 卻標 `[遊戲]`）會讓遊戲開發者去硬補。報告裡會留下完整意見，你看到可疑處可以手動退件。
- 一次只有一個 agent 在改東西（輪流）；要平行開發請改用 git worktree，這版沒有做。

# 資料夾結構與約定

## 資料夾

```
game-dev-flow/
├── README.md
├── docs/                       ← 說明文件
├── templates/                  ← 你的檔案的範本（flow.config.json、TASK.md、prompt.md、human-feedback.md、checks/）
│                                  第一次執行時複製成下面標「你的」的檔案；設定也拿它當預設值，你的設定少了哪項就用這裡的
├── flow.config.json            ← 設定（你的）
├── orchestrator.py             ← 協調器（所有指令的入口）
├── gui.bat                     ← 雙擊啟動圖形化介面
├── gui/                        ← 圖形化介面（server.py 本機伺服器、index.html 網頁）
├── package.json                ← 模型檢視器需要 three 與 esbuild（根目錄 npm install）
├── requirements.txt            ← Python 套件（playwright）
├── tests/                      ← 工具本身的自動測試（run_tests.py、假的 claude：fake_claude.py）
├── prompt.md / TASK.md / human-feedback.md   ← 遊戲階段：設計文件／本輪任務／你的意見（你的）
├── lab/                        ← 模型檢視器（WebGPU；lab.js、lab.template.html、build_lab.mjs）
├── gates/
│   ├── model_gate.py           ← 模型關卡（幾何檢查＋多角度截圖）
│   ├── browser_gate.py         ← 遊戲關卡（抓錯誤／截圖／幀時間）
│   └── guard.py                ← 改檔守門員（hook）：AI 開發者改不該改的檔案時，在改之前就擋下
├── agents/                     ← 各 agent 的指令（工具內建，隨更新換新；介面上只能看）
│   ├── model-dev.md、reviewer-model-visual.md      ← 模型工坊用
│   └── dev.md、reviewer-visual.md、reviewer-code.md ← 遊戲整合用
├── agents.local/               ← （選用，你的）客製的 agent 指令：放同名檔案就取代 agents/ 裡的那份；
│                                  代價是工具更新那份指令時，你的不會跟著更新
├── checks/                     ← （你的）
│   ├── model-checklist.md      ← 所有模型共通的驗收清單
│   └── visual-checklist.md     ← 遊戲的累積式驗收清單
├── models/                     ← 不進這個工具的版控（.gitignore）
│   ├── <名稱>/                 ← 在介面「新增模型」建立，每個模型一個資料夾：
│   │     model.js              ←   純函式 build(THREE)，唯一會被 AI 改的檔（第 1 輪由 AI 建立）
│   │     spec.md               ←   模型規格（人話：長什麼樣、座標約定、不可以什麼）
│   │     spec.json             ←   客觀檢查條件＋截圖角度（範本預設 4 個角度）
│   │     checklist.md          ←   這個模型專屬的累積清單
│   │     human-feedback.md     ←   你對這個模型的意見（第一次在介面編輯時才建立）
│   │     ref*.png／jpg／webp    ←   （選填）參考圖：開發者與視覺審查者每輪都會看；最多 4 張、每張 5MB 以內
│   │     reopen.md             ←   （遊戲階段退件時自動產生）
│   └── _released/<名稱>/       ← 發布區：model.js、release.json（版本、sha256）、history.jsonl
├── game/                       ← 你的遊戲專案（flow.config.json 的 project_dir）；不進這個工具的版控，有自己的 git
│     shots.json                ←   鏡頭清單：每張截圖怎麼擺場景、應該看到什麼
└── workspace/                  ← 執行時自動產生
    ├── model-<名稱>-<時間>/    ← 模型迴圈：messages/、snapshots/（每輪 model.js 快照）、shots/、REPORT.md
    ├── <時間>/                 ← 遊戲迴圈：messages/、shots/、REPORT.md
    └── lab/<名稱>.html         ← `lab` 指令建出的檢視器
```

## 模型（model.js）的約定

1. 輸出 `meta`（尺寸等數值）與 `build(THREE)`；`THREE` 由外部傳入，模型不 import 任何東西（否則遊戲裡會有兩份 three）。
2. 座標約定寫在 spec.md（例如原點在手握處、握柄沿 -z），**遊戲依賴它**，改了就是新版本、要重新發布。
3. 要給遊戲用的零件放 `group.userData`（例如斧頭的 `head`）。
4. 傳入的 `THREE` 是 `three/webgpu`。可用一般材質；不可用 `onBeforeCompile`、`ShaderMaterial`、GLSL（WebGPU 不支援，`onBeforeCompile` 會被靜默忽略）。自訂著色用節點材質加 `THREE.TSL`。

## 遊戲的約定

1. 暴露測試介面（如 `window.__game`），讓關卡能擺場景、截圖。
2. 所有隨機用可指定種子的亂數，否則每輪截圖不同。
3. 不載入外部素材（關卡會偵測並判不通過）。
4. 建置指令一個就能跑完，結果是一個可直接開啟的 HTML。
5. 只准 WebGPU：用 `three/webgpu` 的 `WebGPURenderer`，`await renderer.init()` 後確認 `renderer.backend.isWebGPUBackend`，再設定 `window.__renderBackend = 'webgpu'`。關卡會檢查這個值（`flow.config.json` 的 `require_webgpu`，預設開啟）。瀏覽器不支援時要顯示訊息並停止，不退回 WebGL。

遊戲裡引用模型的寫法（只能引用 `_released`）：

```js
import { build as buildAxe, meta as axeMeta } from '../../../models/_released/axe/model.js';
const axe = buildAxe(THREE);        // THREE 由遊戲傳入，模型自己不 import 任何東西
```

直接 import 工坊裡的 `models/axe/model.js` 會被「模型檢查」判為違規。

## 鏡頭清單（shots.json）

```json
{
  "ready": "window.__game && window.__game.ready",
  "settle_ms": 1500,
  "shots": [
    { "name": "01-overview", "setup": "window.__game.setCamera([4, 3, 4], [0, 0, 0])", "wait_ms": 800,
      "describe": "整個場景：樹在左邊、玩家拿著斧頭站在中間" }
  ],
  "perf": { "seconds": 8 }
}
```

| 欄位 | 意思 |
|---|---|
| `ready` | 一段 JS，結果為真才算遊戲就緒（最多等 `ready_timeout_ms`，預設 60000） |
| `settle_ms` | 就緒後再等多久才開始截圖（預設 1500） |
| `shots[].name` | 截圖檔名 |
| `shots[].setup` | 選填：截圖前在頁面執行的 JS（擺鏡頭、觸發動作、凍結動畫…，用遊戲自己暴露的測試介面；上面的 `setCamera` 只是例子） |
| `shots[].wait_ms` | 執行 setup 後等多久再拍（預設 800） |
| `shots[].describe` | 這張圖應該看到什麼（給視覺審查者對照） |
| `perf` | 選填：量幀時間。`seconds` 量幾秒（預設 8），也可以加 `setup` |
| `ignore_console` | 選填：要忽略的 console 訊息（正規表示式清單） |

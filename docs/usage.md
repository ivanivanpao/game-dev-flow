# 使用說明

安裝步驟在 [README 的「安裝」](../README.md#安裝)。**以下指令裡的 `python`，都是指 `.venv/Scripts/python.exe`**：關卡程式用執行協調器的那個 Python 啟動，用系統的 Python 會找不到 Playwright。

安裝的補充說明：
- `npm install` 要在專案根目錄跑：檢視器與遊戲打包都會往上層資料夾找到 three 與 esbuild。
- `playwright install chromium` 會同時下載完整版與無頭精簡版的 Chromium，關卡要用的是完整版。關卡用完整版的新版無頭模式（`channel='chromium'`）加 `--enable-unsafe-webgpu`，不開視窗也能用到真的顯示卡。預設的無頭精簡版只能拿到 SwiftShader 軟體模擬，Three.js 的 WebGPU 繪圖管線會建立失敗、截圖只剩背景色，所以不用它。

## 圖形化介面（不想打指令就用這個）

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

## 1. 看目前狀態

```
python orchestrator.py status
```

列出每個模型：最近一次迴圈結果、有沒有發布、發布的版本、工坊版本是否比發布版新；以及「遊戲現在能不能開工」。

## 2. 第一段：做一個模型

先建立模型：在介面「① 模型工坊」按「新增模型」，會產生 `models/<名稱>/` 與 spec.md、spec.json、checklist.md 範本，把 spec.md、spec.json 裡【】的地方填完（總覽頁會提醒哪些還沒填）。以下用 `axe` 當例子。

```
python orchestrator.py model axe --dry-run --rounds 1    # 先乾跑一輪驗證流程
python orchestrator.py model axe                         # 真的跑
```

- `model axe`：對 `models/axe/` 跑模型迴圈
- `--dry-run`：AI 的部分用假回覆，關卡是真的跑（不消耗訂閱額度，但**這種通過不能發布**）
- `--rounds N`：最多幾輪（不寫用 `model_max_rounds`）

結束後看 `workspace/model-axe-<時間>/REPORT.md`。

## 3. 人類檢查點：自己轉著看，再發布

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

## 4. 第二段：做遊戲（介面的「開始做遊戲」）

```
python orchestrator.py gates               # 只跑客觀關卡，不呼叫 AI
python orchestrator.py run --dry-run --rounds 1
python orchestrator.py run                 # 做遊戲：AI 反覆修改你的遊戲程式
```

- 模型沒發布完，`run` 會直接擋下並列出缺哪些。
- 遊戲資料夾還沒用 git 的話，`run` 會自動 `git init`，並把目前的檔案存成第一個版本（分支 `main`）。
- 遊戲資料夾有還沒 commit 的修改，`run` 也會直接擋下並列出是哪些檔案。請先自己 commit，工具不會替你存。
- 開始時記下你原本的分支（例如 `main`），另開分支 `auto/<時間>` 給 AI 改，每輪一個 commit。**你原本的分支不會被動到。**
- 結束時（不管通過、失敗、出錯、或在命令列按 Ctrl+C）會自動切回你原本的分支。AI 的修改留在 `auto/<時間>`。
- 在介面按「停止」是強制結束，工具來不及切回去，遊戲資料夾會暫時停在 `auto/<時間>`。下次按「開始做遊戲」時，工具會先發現「上次沒收尾」，把留下的修改存進那個 `auto/` 分支，再切回你原本的分支，然後才開始。
- 結束後看 `workspace/<時間>/REPORT.md`，切到 `auto/<時間>` 親自玩，滿意才合併。

## 5. 遊戲階段發現模型有問題

審查者最後附的 JSON 裡，每筆缺陷都要標 `target`（`"遊戲"` 或 `"模型"`）。有 `"target": "模型", "model": "axe"`、而且嚴重度高或中的缺陷時，協調器會：

1. 把缺陷寫進 `models/axe/reopen.md`
2. 自動撤回 axe 的發布（`release.revoked.*.json`）
3. 停止遊戲迴圈，報告狀態 `blocked_on_model`

接著你重跑 `model axe`（開發者第 1 輪就會看到退件內容，通過後退件檔自動改名為 `reopen.done.*`），再 `release axe`，然後才能再做一次遊戲。

撤回發布用：`python orchestrator.py unrelease axe`。

## 6. 換成你自己的模型／遊戲

- 新模型：在介面「新增模型」，寫 `spec.md`、`spec.json`（客觀檢查條件＋截圖角度）。遊戲要用的模型，名稱要在 `flow.config.json` 的 `uses_models` 裡（新增時可以勾選順便加入；遊戲用到哪些，就要先發布哪些）。
- 新遊戲：改 `flow.config.json` 的 `project_dir`、`build_cmd`、`entry`、`shots_spec`（目前是 `game/`、`npm run build`、`dist/index.html`、`game/shots.json`）。
- 遊戲與模型要遵守的約定、鏡頭清單 `shots.json` 的格式，見 [conventions.md](conventions.md)。

## 7. 改了工具之後：跑自動測試

改了 `orchestrator.py`、`gates/`、`gui/` 之後，跑一次確認之前修好的行為沒被改壞：

```
.venv/Scripts/python.exe tests/run_tests.py
```

- 會在系統暫存區建一份專案副本，用「假的 claude」（`tests/fake_claude.py`）跑做模型、做遊戲、GUI 的實際流程。**不呼叫真的 Claude、不消耗訂閱額度、不動你真正的專案和遊戲。**
- 每一項印出 `PASS` 或 `FAIL`，最後一行是總結，例如「共 63 項，通過 63 項，失敗 0 項」。大約一兩分鐘。
- 全部通過會自動刪掉副本；有失敗會保留副本並印出位置，方便查原因。
- 如果是**故意**改變某個行為（例如改了退件規則），測試也要跟著改，不然會顯示失敗。

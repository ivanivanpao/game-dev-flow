# 協調器怎麼呼叫 Claude Code

## 登入與計費

協調器用 `claude -p` 呼叫 Claude Code，走的是你 Claude Code 的登入（訂閱額度），不是 API 金鑰。

注意：電腦裡如果設了環境變數 `ANTHROPIC_API_KEY`，`-p` 模式一定會改用它，變成另外按 API 用量計費。不想這樣就不要設這個變數。

## 指令參數

| 參數 | 意思 |
|---|---|
| `-p` | 無頭模式：不開對話畫面，讀完提示詞、做完就輸出並結束。提示詞用標準輸入傳，避免命令列太長 |
| `--output-format text` | 輸出純文字（協調器存檔後，從審查者輸出最後的 JSON 區塊讀出結論與缺陷） |
| `--settings <檔案>` | 只有開發者有：載入額外設定，掛上改檔守門員 hook（`gates/guard.py`）。設定檔是每次呼叫前建在系統暫存區、用完就刪；可改哪些檔案由環境變數 `FLOW_GUARD_RULES` 傳給守門員 |
| `--tools <清單>` | 限制這個 agent「有哪些工具」。視覺審查者只給 `Read`，而且允許清單寫成 `Read(./**)`，只能讀自己的隔離資料夾，讀不到專案程式碼，也改不了東西 |
| `--allowedTools <清單>` | 這些工具免詢問。無頭模式沒人可以按「允許」，沒列在這裡的會被拒絕 |
| `--permission-mode` | 開發者 `acceptEdits`（可直接改檔）；審查者 `dontAsk`（沒在允許清單的一律拒絕） |
| `--model` | 選填：指定模型。每個角色可以各自設定（見下方），沒設就用 Claude Code 預設 |
| `--max-budget-usd` | 選填：單次呼叫花費上限（`flow.config.json` 的 `max_budget_usd_per_call`）。這是照 API 價格換算的估計值，用訂閱時不是真的帳單，維持 `null` 即可 |

## 審查結果的格式

審查者先用文字寫說明，最後附一段 ```json 區塊，協調器只讀這一段：

```json
{
  "verdict": "CHANGES_REQUESTED",
  "defects": [
    {"severity": "中", "target": "模型", "model": "axe", "where": "01-overview 中間", "problem": "斧頭看起來像鋤頭"}
  ]
}
```

- `verdict`：`APPROVED` 或 `CHANGES_REQUESTED`
- `defects[].severity`：高／中／低
- `defects[].target`：`遊戲` 或 `模型`（只有遊戲階段的視覺審查者要寫；是 `模型` 時要有 `model`）
- `defects[].where`、`defects[].problem`：位置、問題

格式不對時怎麼處理，見 [rules.md 的停止規則](rules.md#停止規則)。每個角色的完整輸出要求寫在 `agents/` 裡。

## 每個角色各自選模型

在 `flow.config.json` 設定：

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

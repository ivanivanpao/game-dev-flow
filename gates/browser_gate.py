#!/usr/bin/env python3
"""
瀏覽器關卡（客觀檢查，不靠 AI 判斷）

做的事：
  1. 用 Playwright 開啟遊戲的 HTML
  2. 抓 JavaScript 錯誤、console 錯誤、載入失敗、以及「偷偷載入外部素材」的請求
  3. 照 spec（shots.json）一張一張擺好場景並截圖
  4. 量一段時間內每一幀花的毫秒數（幀時間）
結果寫成 JSON，由協調器判斷過不過關。
"""
import argparse, json, re, shlex, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

# 在頁面裡量幀時間：連續跑 secs 秒，回傳每一幀的間隔（毫秒）
PERF_JS = """(secs) => new Promise((resolve) => {
  const d = []; let last = performance.now(); const t0 = last;
  function f(n) { d.push(n - last); last = n; if (n - t0 < secs * 1000) requestAnimationFrame(f); else resolve(d); }
  requestAnimationFrame(f);
})"""


# 等瀏覽器實際畫完 n 幀：擺好場景後不能只固定等幾秒（電腦忙的時候可能還沒畫完），要確認新畫面真的畫出來了才截圖
FRAMES_JS = """(n) => new Promise((resolve) => {
  let k = 0; const f = () => (++k >= n ? resolve() : requestAnimationFrame(f)); requestAnimationFrame(f);
})"""


def percentile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entry', required=True, help='遊戲 HTML 的路徑')
    ap.add_argument('--spec', required=True, help='shots.json 的路徑')
    ap.add_argument('--out', required=True, help='截圖與結果輸出資料夾')
    ap.add_argument('--headed', action='store_true', help='開出真的瀏覽器視窗（用顯示卡，量效能比較準）')
    ap.add_argument('--browser-args', default='', help='額外的 Chromium 啟動參數（用空白分隔）')
    ap.add_argument('--viewport', default='1100x700')
    ap.add_argument('--require-webgpu', action='store_true', help='遊戲就緒後檢查 window.__renderBackend 必須是 webgpu')
    a = ap.parse_args()

    spec = json.loads(Path(a.spec).read_text('utf-8'))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w, h = [int(v) for v in a.viewport.lower().split('x')]
    ignore = [re.compile(p) for p in spec.get('ignore_console', [])]
    res = {'ok': True, 'errors': [], 'warnings': [], 'violations': [], 'shots': [], 'perf': None, 'backend': None}

    def bad(kind, msg):
        res['errors'].append(f'{kind}: {msg}'[:500])

    # WebGPU 需要：channel='chromium'（完整版 Chromium 的新版無頭模式，能用到真的顯示卡）＋ --enable-unsafe-webgpu。
    # 預設的「無頭精簡版」只能拿到 SwiftShader 軟體模擬，Three.js 的繪圖管線會建立失敗，截圖只剩背景色。
    args = shlex.split(a.browser_args)
    if '--enable-unsafe-webgpu' not in args:
        args.append('--enable-unsafe-webgpu')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not a.headed, channel='chromium', args=args)
        page = browser.new_page(viewport={'width': w, 'height': h})
        page.on('pageerror', lambda e: bad('頁面錯誤', e))
        def on_console(m):
            t = m.text
            if any(r.search(t) for r in ignore):
                return
            if m.type == 'error':
                bad('console 錯誤', t)
            elif m.type == 'warning':
                res['warnings'].append(t[:300])
        page.on('console', on_console)
        def on_request(r):
            u = r.url
            if not u.startswith(('file:', 'data:', 'blob:', 'about:')):
                res['violations'].append('載入了外部資源：' + u[:200])
        page.on('request', on_request)
        page.on('requestfailed', lambda r: bad('請求失敗', r.url))

        page.goto(Path(a.entry).resolve().as_uri())
        try:
            page.wait_for_function(spec.get('ready', 'true'), timeout=int(spec.get('ready_timeout_ms', 60000)))
        except Exception as e:
            bad('遊戲沒有在時間內就緒', spec.get('ready', 'true'))
        res['backend'] = page.evaluate('window.__renderBackend || null')
        if a.require_webgpu and res['backend'] != 'webgpu':
            bad('繪圖後端不是 WebGPU', f"window.__renderBackend = {res['backend']!r}（遊戲要用 WebGPURenderer，初始化後確認 renderer.backend.isWebGPUBackend 再設定為 'webgpu'）")
        time.sleep(spec.get('settle_ms', 1500) / 1000)

        for s in spec.get('shots', []):
            try:
                if s.get('setup'):
                    page.evaluate(s['setup'])
                time.sleep(s.get('wait_ms', 800) / 1000)      # 鏡頭清單指定的等待（例如等揮斧動畫播到一半）
                page.evaluate(FRAMES_JS, 3)                     # 再確認至少畫完 3 幀才拍
                path = out / (s['name'] + '.png')
                page.screenshot(path=str(path))
                res['shots'].append({'name': s['name'], 'file': path.name, 'describe': s.get('describe', '')})
            except Exception as e:
                bad(f"截圖 {s.get('name')} 失敗", e)

        perf = spec.get('perf')
        if perf:
            try:
                if perf.get('setup'):
                    page.evaluate(perf['setup'])
                d = page.evaluate(PERF_JS, perf.get('seconds', 8))
                d = d[5:] if len(d) > 10 else d      # 丟掉最前面幾幀（還在暖機）
                avg = sum(d) / max(1, len(d))
                res['perf'] = {'frames': len(d), 'avg_ms': round(avg, 2), 'p95_ms': round(percentile(d, 0.95), 2),
                               'max_ms': round(max(d), 2) if d else 0, 'fps': round(1000 / avg, 1) if avg else 0,
                               'headless': not a.headed}
            except Exception as e:
                bad('量測幀時間失敗', e)
        browser.close()

    res['ok'] = not res['errors'] and not res['violations']
    (out / 'gate-result.json').write_text(json.dumps(res, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'ok': res['ok'], 'errors': len(res['errors']), 'shots': len(res['shots']), 'perf': res['perf']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

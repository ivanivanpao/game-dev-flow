#!/usr/bin/env python3
"""
模型關卡（模型工坊用，客觀檢查，不靠 AI 判斷）

對「單獨放在檢視器裡的一個模型」做：
  1. 幾何統計：尺寸、包圍盒位置（驗證座標約定）、三角形數、材質數、有沒有 NaN
  2. 對照模型規格 spec.json 的 checks，逐條判定過／不過
  3. 依 spec.json 的 views 從各個角度截圖（給視覺審查者看）
  4. 抓「偷偷載入外部素材」的請求（跟遊戲的瀏覽器關卡一樣）：模型只能用程式產生，不能從網路載入圖片、模型、音效或程式庫。
     不在這裡擋，模型會帶著問題發布，到遊戲階段才被瀏覽器關卡擋下，而遊戲開發者不准改已發布的模型，只會卡住
輸出 gate-result.json
"""
import argparse, json, shlex, time
from pathlib import Path
from playwright.sync_api import sync_playwright


def check_spec(st, checks):
    """回傳 (失敗清單, 通過清單)"""
    bad, good = [], []

    def rng(label, vals, ranges):
        for ax, v, r in zip('xyz', vals, ranges):
            if r is None:
                continue
            ok = r[0] <= v <= r[1]
            (good if ok else bad).append(f'{label}.{ax} = {v}（要求 {r[0]} ~ {r[1]}）')

    if 'size' in checks: rng('尺寸', st['size'], checks['size'])
    if 'bbox_min' in checks: rng('包圍盒最小角', st['bbox_min'], checks['bbox_min'])
    if 'bbox_max' in checks: rng('包圍盒最大角', st['bbox_max'], checks['bbox_max'])
    if 'max_triangles' in checks:
        (good if st['triangles'] <= checks['max_triangles'] else bad).append(f"三角形數 {st['triangles']}（上限 {checks['max_triangles']}）")
    if 'max_materials' in checks:
        (good if st['materials'] <= checks['max_materials'] else bad).append(f"材質數 {st['materials']}（上限 {checks['max_materials']}）")
    if 'max_meshes' in checks:
        (good if st['meshes'] <= checks['max_meshes'] else bad).append(f"網格數 {st['meshes']}（上限 {checks['max_meshes']}）")
    (good if st['nan_meshes'] == 0 else bad).append(f"含 NaN 頂點的網格數 {st['nan_meshes']}（必須是 0）")
    for k, v in (checks.get('meta_equals') or {}).items():
        have = (st.get('meta') or {}).get(k)
        (good if have == v else bad).append(f'meta.{k} = {have}（要求 {v}）')
    return bad, good


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--html', required=True, help='build_lab.mjs 產生的檢視器 HTML')
    ap.add_argument('--spec', required=True, help='models/<名稱>/spec.json')
    ap.add_argument('--out', required=True)
    ap.add_argument('--headed', action='store_true')
    ap.add_argument('--browser-args', default='')
    ap.add_argument('--viewport', default='900x700')
    a = ap.parse_args()

    spec = json.loads(Path(a.spec).read_text('utf-8'))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w, h = [int(v) for v in a.viewport.lower().split('x')]
    res = {'ok': True, 'errors': [], 'failed_checks': [], 'passed_checks': [], 'stats': None, 'shots': [], 'gpu': None, 'violations': []}

    # WebGPU 需要：channel='chromium'（完整版 Chromium 的新版無頭模式，能用到真的顯示卡）＋ --enable-unsafe-webgpu。
    # 預設的「無頭精簡版」只能拿到 SwiftShader 軟體模擬，Three.js 的繪圖管線會建立失敗，截圖只剩背景色。
    args = shlex.split(a.browser_args)
    if '--enable-unsafe-webgpu' not in args:
        args.append('--enable-unsafe-webgpu')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not a.headed, channel='chromium', args=args)
        page = browser.new_page(viewport={'width': w, 'height': h})
        page.on('pageerror', lambda e: res['errors'].append(f'頁面錯誤: {e}'[:400]))
        page.on('console', lambda m: res['errors'].append('console 錯誤: ' + m.text[:300]) if m.type == 'error' and 'GPU stall' not in m.text and 'GL Driver' not in m.text else None)
        def on_request(r):
            u = r.url
            if not u.startswith(('file:', 'data:', 'blob:', 'about:')):     # 檢視器本身是單一 HTML 檔，任何網路請求都是模型發出的
                res['violations'].append('載入了外部資源：' + u[:200])
        page.on('request', on_request)
        page.goto(Path(a.html).resolve().as_uri())
        page.add_style_tag(content='#info{display:none !important}')   # 截圖不要有說明框，避免干擾判斷
        try:
            # 就緒或明確失敗都會結束等待；失敗原因由檢視器寫在 __lab.error
            page.wait_for_function('window.__lab && (window.__lab.ready || window.__lab.error)', timeout=60000)
        except Exception:
            res['errors'].append('檢視器沒有在時間內就緒（模型 build() 可能丟出例外）')
        lab = page.evaluate('window.__lab ? {ready: !!window.__lab.ready, error: window.__lab.error || null, backend: window.__lab.backend || null, adapter: window.__lab.adapter || null} : null')
        if lab and lab['error']:
            res['errors'].append('檢視器錯誤：' + lab['error'][:400])
        elif lab and lab['ready'] and lab['backend'] != 'webgpu':
            res['errors'].append(f"繪圖後端不是 WebGPU（實際：{lab['backend']}）")
        if lab and lab['ready'] and lab['backend'] == 'webgpu':
            res['gpu'] = lab['adapter']
            st = page.evaluate('window.__lab.stats()')
            res['stats'] = st
            res['failed_checks'], res['passed_checks'] = check_spec(st, spec.get('checks', {}))
            for v in spec.get('views', []):
                try:
                    page.evaluate('(v) => window.__lab.setView(v)', v)
                    # 不固定等幾秒，改成等瀏覽器實際畫完 3 幀（換好鏡頭的畫面真的畫出來了）才截圖
                    page.evaluate("(n) => new Promise((r) => { let k = 0; const f = () => (++k >= n ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); })", 3)
                    path = out / (v['name'] + '.png')
                    page.screenshot(path=str(path))
                    res['shots'].append({'name': v['name'], 'file': path.name, 'describe': v.get('describe', '')})
                except Exception as e:
                    res['errors'].append(f"截圖 {v.get('name')} 失敗: {e}"[:300])
        browser.close()

    res['ok'] = not res['errors'] and not res['violations'] and not res['failed_checks'] and res['stats'] is not None
    (out / 'gate-result.json').write_text(json.dumps(res, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'ok': res['ok'], 'failed': len(res['failed_checks']), 'violations': len(res['violations']), 'shots': len(res['shots']), 'stats': res['stats']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

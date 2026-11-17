// 模型檢視器（模型工坊）：在乾淨的環境裡單獨看一個模型，沒有遊戲、沒有地形、沒有動畫
// 繪圖一律用 WebGPU：拿不到 WebGPU 就報錯停止，不退回 WebGL（避免以為在用 WebGPU 其實不是）
import * as THREE from 'three/webgpu';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import * as MODEL from 'model-under-test';      // 建置時由 esbuild 換成 models/<name>/model.js

const canvas = document.getElementById('c');
const nameEl = document.getElementById('nm');
if (nameEl) nameEl.textContent = '模型：' + (MODEL.meta && MODEL.meta.name);

// 失敗時把原因放進 window.__lab.error，關卡會讀出來寫進回饋；同時顯示在畫面上給人看
function fail(msg) {
  window.__lab = { ready: false, error: msg };
  const el = document.getElementById('err');
  if (el) { el.textContent = msg; el.style.display = 'block'; }
  console.error(msg);
}

async function main() {
  // 先確認瀏覽器真的拿得到 WebGPU 顯示卡，再建立繪圖器
  if (!navigator.gpu) return fail('這個瀏覽器不支援 WebGPU（navigator.gpu 不存在）');
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) return fail('拿不到 WebGPU 顯示卡（requestAdapter 回傳 null）；自動化測試請用完整版 Chromium（channel=chromium）並加 --enable-unsafe-webgpu');

  const renderer = new THREE.WebGPURenderer({ canvas, antialias: true });
  renderer.setPixelRatio(1);
  await renderer.init();                                   // WebGPU 要等非同步初始化完成才能畫
  if (!renderer.backend.isWebGPUBackend) return fail('繪圖器退回了 WebGL，不是 WebGPU');

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xb9c2cc);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;

  // 中性打光：主光＋補光＋環境光，不放陰影，避免影響判斷形狀
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(3, 5, 4); scene.add(key);
  const fill = new THREE.DirectionalLight(0xdfe8ff, 0.8); fill.position.set(-4, 2, -3); scene.add(fill);

  // 參考物：方格地板（看尺寸，大小跟著模型，見下方 groundGrid）、原點座標軸（紅 x、綠 y、藍 z，看座標約定）、1.8 公尺高的人形（看比例）
  const GROUND_Y = -0.9;
  const axes = new THREE.AxesHelper(0.35); scene.add(axes);
  const human = new THREE.Group();
  const hm = new THREE.MeshStandardMaterial({ color: 0x6d7782, roughness: 1, transparent: true, opacity: 0.55 });
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.22, 1.1, 4, 10), hm); body.position.y = GROUND_Y + 0.8;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 10), hm); head.position.y = GROUND_Y + 1.68;
  human.add(body, head); human.position.set(-1.6, 0, 0.4); scene.add(human);

  const obj = MODEL.build(THREE);
  scene.add(obj);
  const box = new THREE.Box3();
  function visibleBox() {
    box.makeEmpty();
    obj.updateMatrixWorld(true);
    obj.traverse(o => { if (o.isMesh && o.visible) box.expandByObject(o, true); });
    return box;
  }
  const bboxHelper = new THREE.Box3Helper(visibleBox().clone(), 0xffa733); scene.add(bboxHelper);

  // 方格地板跟著模型大小：以原點為中心、蓋住整個模型，至少 10 公尺見方。
  // 每格 1、10、100… 公尺，格數保持在 10～100 格；小模型就是原本的 10 公尺、每格 1 公尺
  function groundGrid(b) {
    const xs = [b.min.x, b.max.x, b.min.z, b.max.z];
    const extent = !b.isEmpty() && xs.every(Number.isFinite) ? Math.max(...xs.map(Math.abs)) : 0;
    const span = 2 * Math.max(5, extent * 1.2);                          // 留 20% 邊，模型不會剛好壓在地板邊緣
    const cell = 10 ** Math.floor(Math.log10(span / 10));
    const n = Math.ceil(span / cell - 1e-9);                             // 格數（扣掉浮點誤差，10 公尺就是 10 格）
    return { cell, n };
  }
  const { cell: gridCell, n: gridN } = groundGrid(visibleBox());
  const grid = new THREE.GridHelper(gridN * gridCell, gridN, 0x55606b, 0x8b96a1); grid.position.y = GROUND_Y; scene.add(grid);
  const gridEl = document.getElementById('gr');
  if (gridEl) gridEl.textContent = gridCell;

  function stats() {
    const b = visibleBox(), size = b.getSize(new THREE.Vector3());
    let tris = 0, meshes = 0, nan = 0, hidden = 0; const mats = new Set();
    obj.traverse(o => {
      if (!o.isMesh) return;
      if (!o.visible) { hidden++; return; }
      meshes++; (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => mats.add(m.uuid));
      const g = o.geometry, p = g.attributes.position;
      tris += g.index ? g.index.count / 3 : p.count / 3;
      for (let i = 0; i < p.array.length; i++) if (!Number.isFinite(p.array[i])) { nan++; break; }
    });
    const r = v => +v.toFixed(4);
    return { size: [r(size.x), r(size.y), r(size.z)], bbox_min: [r(b.min.x), r(b.min.y), r(b.min.z)], bbox_max: [r(b.max.x), r(b.max.y), r(b.max.z)],
             triangles: Math.round(tris), meshes, materials: mats.size, hidden_meshes: hidden, nan_meshes: nan, meta: MODEL.meta,
             grid_cell: gridCell };                                     // 地板每格幾公尺（給審查者看截圖時換算尺寸）
  }

  function setView(v) {
    const b = visibleBox(), c = b.getCenter(new THREE.Vector3()), size = b.getSize(new THREE.Vector3());
    const target = v.target === 'center' || !v.target ? c : new THREE.Vector3(...v.target);
    const dist = v.dist === 'fit' || !v.dist ? Math.max(size.x, size.y, size.z) * 1.7 : v.dist;
    const dir = new THREE.Vector3(...v.dir).normalize();
    camera.position.copy(target).addScaledVector(dir, dist);
    camera.up.set(...(v.up || [0, 1, 0]));
    controls.target.copy(target); camera.lookAt(target); controls.update();
    // 可視距離隨模型大小放大（實際比例的賽道等大型模型才不會被遠裁切面切掉）；near 等比放大以保住深度精度，小模型維持 0.01～100
    const need = (camera.position.distanceTo(c) + size.length() / 2) * 1.5;
    camera.far = Number.isFinite(need) ? Math.max(100, need) : 100;
    camera.near = camera.far / 10000; camera.updateProjectionMatrix();
    axes.visible = v.axes !== false; human.visible = v.human !== false; bboxHelper.visible = !!v.bbox;
  }

  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();
  setView({ dir: [1, 0.6, 1], dist: 'fit', bbox: true });
  // 每一幀都畫（setView 只改鏡頭，畫面由這個迴圈更新；關卡截圖前會等待幾幀）
  renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });

  const info = adapter.info || {};
  window.__lab = { ready: true, stats, setView, name: MODEL.meta && MODEL.meta.name,
                   backend: 'webgpu', adapter: { vendor: info.vendor || '', architecture: info.architecture || '' } };
}

main().catch(e => fail('檢視器初始化失敗：' + (e && e.stack || e)));

// Lower-third timeline overlay injected into the app pages while recording the demo video.
(() => {
  if (!location.host.startsWith('localhost')) return;
  const css = `
  #__kv{position:fixed;left:40px;bottom:28px;z-index:2147483646;display:flex;align-items:center;gap:22px;padding:14px 24px 14px 20px;
    background:rgba(28,25,23,.9);color:#fff;border-radius:16px;font-family:Inter,"Segoe UI",system-ui,sans-serif;
    box-shadow:0 14px 40px rgba(0,0,0,.25);transform:translateY(140px);opacity:0;transition:transform .5s cubic-bezier(.2,.8,.2,1),opacity .5s}
  #__kv.on{transform:none;opacity:1}
  #__kv .n{font-size:12px;letter-spacing:.08em;color:#8fd3e2;text-transform:uppercase}
  #__kv .t{font-size:21px;font-weight:600;line-height:1.15;margin-top:2px}
  #__kv .s{font-size:13.5px;color:#d6d3d1;margin-top:3px}
  #__kv .tl{display:flex;gap:6px;margin-left:8px}
  #__kv .tl i{display:block;width:40px;height:6px;border-radius:3px;background:rgba(255,255,255,.22);transition:background .4s}
  #__kv .tl i.done{background:#8fd3e2}
  #__kv .tl i.now{background:#0b6b85;box-shadow:0 0 0 2px rgba(143,211,226,.7)}
  #__kv .txt{transition:opacity .25s}`;
  const style = document.createElement('style'); style.textContent = css;
  const box = document.createElement('div'); box.id = '__kv';
  box.innerHTML = '<div class="txt"><div class="n"></div><div class="t"></div><div class="s"></div></div><div class="tl"></div>';
  const attach = () => { document.head.appendChild(style); document.body.appendChild(box); };
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);
  window.__kv = {
    show(i, total, title, sub) {
      const tl = box.querySelector('.tl'); tl.innerHTML = '';
      for (let k = 1; k <= total; k++) { const d = document.createElement('i'); if (k < i) d.className = 'done'; if (k === i) d.className = 'now'; tl.appendChild(d); }
      const txt = box.querySelector('.txt'); txt.style.opacity = 0;
      setTimeout(() => { box.querySelector('.n').textContent = 'Step ' + i + ' of ' + total; box.querySelector('.t').textContent = title; box.querySelector('.s').textContent = sub; txt.style.opacity = 1; box.classList.add('on'); }, 220);
    },
    sub(text) { box.querySelector('.s').textContent = text; },
    hide() { box.classList.remove('on'); },
  };
})();

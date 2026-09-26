// Mode cuisine : une étape à la fois, gros texte, écran qui reste allumé.
import { $, esc, arr, num, f0 } from '../util.js';
import { pushPage, icon, toast } from '../ui.js';
import { isSec, scaleQty } from '../macros.js';
import { keepAwake } from '../native.js';

export function openCook(r, k = 1) {
  const steps = arr(r.steps).filter(Boolean);
  const done = new Set();
  const page = pushPage({
    cls: 'cook',
    live: false,
    state: { i: 0, ing: !steps.length },
    render,
    onClose: () => keepAwake(false),
  });
  keepAwake(true).then(ok => { if (!ok) toast('Écran allumé indisponible sur cet appareil.'); });

  function render(p) {
    const st = p.state;
    const n = steps.length;
    const items = arr(r.ingredients);
    const ingHTML = `<ul class="cook-ings">${items.map((it, j) => isSec(it)
      ? `<li class="sec">${esc(it.section)}</li>`
      : `<li><label class="check big"><input type="checkbox" data-j="${j}" ${done.has(j) ? 'checked' : ''}><span><b>${esc(scaleQty(it.qty, k))}</b> ${esc(it.name)}${num(it.g) > 0 && !/\bg\b/.test(it.qty || '') ? ` <small>${f0(num(it.g) * k)} g</small>` : ''}</span></label></li>`).join('')}</ul>`;
    p.el.innerHTML = `
      <div class="cook-top">
        <button type="button" class="icon-btn" data-act="close" aria-label="Quitter le mode cuisine">${icon('close')}</button>
        <p>${esc(r.title)}</p>
        <button type="button" class="btn small ${st.ing ? 'primary' : 'ghost'}" data-act="ing">Ingrédients</button>
      </div>
      ${n ? `<div class="cook-prog"><i style="width:${((st.i + 1) / n) * 100}%"></i></div>` : ''}
      <div class="cook-body">
        ${st.ing || !n ? ingHTML : `<p class="cook-n">Étape ${st.i + 1} sur ${n}</p><p class="cook-step">${esc(steps[st.i])}</p>`}
      </div>
      ${n ? `<div class="cook-nav">
        <button type="button" class="btn ghost" data-act="prev" ${st.i === 0 && !st.ing ? 'disabled' : ''}>${icon('back')}Précédent</button>
        <button type="button" class="btn primary" data-act="next">${st.ing ? 'Commencer' : st.i === n - 1 ? 'Terminé' : 'Suivant'}${st.ing || st.i < n - 1 ? icon('next') : icon('check')}</button>
      </div>` : ''}`;
  }

  page.el.addEventListener('change', e => {
    const c = e.target.closest('[data-j]');
    if (c) { if (c.checked) done.add(+c.dataset.j); else done.delete(+c.dataset.j); }
  });
  page.el.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const st = page.state;
    const a = b.dataset.act;
    if (a === 'close') page.close();
    else if (a === 'ing') { st.ing = !st.ing; page.refresh(); }
    else if (a === 'prev') { if (st.ing) st.ing = false; else st.i = Math.max(0, st.i - 1); page.refresh(); }
    else if (a === 'next') {
      if (st.ing) { st.ing = false; page.refresh(); return; }
      if (st.i >= steps.length - 1) { page.close(); toast('Bon appétit !'); return; }
      st.i++; page.refresh();
    }
  });
  // glisser à gauche / droite
  let x0 = null;
  page.el.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; }, { passive: true });
  page.el.addEventListener('touchend', e => {
    if (x0 == null || page.state.ing) return;
    const dx = e.changedTouches[0].clientX - x0;
    x0 = null;
    if (Math.abs(dx) < 70) return;
    const st = page.state;
    if (dx < 0 && st.i < steps.length - 1) st.i++;
    else if (dx > 0 && st.i > 0) st.i--;
    else return;
    page.refresh();
  }, { passive: true });
  return page;
}

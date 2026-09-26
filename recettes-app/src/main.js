// Point d'entrée : charge les données, dessine les onglets, branche le bouton retour et le partage.
import './app.css';
import { S, loadAll, onChange } from './store.js';
import { rebuildIndex } from './macros.js';
import { $, esc } from './util.js';
import { icon, refreshPages, handleBack, nav, stack, toast } from './ui.js';
import { onBack, onShared, clearShared, isNative } from './native.js';
import { renderLibrary } from './screens/library.js';
import { renderPlanner } from './screens/planner.js';
import { renderGroceries } from './screens/groceries.js';
import { renderProfile, openOnboarding, openProfileSection } from './screens/profile.js';
import { openAddSheet, handleShared } from './screens/add.js';

const TABS = [
  { id: 'recettes', label: 'Recettes', icon: 'recipes', render: renderLibrary },
  { id: 'planning', label: 'Planning', icon: 'calendar', render: renderPlanner },
  { id: 'add', label: 'Ajouter', icon: 'plus' },
  { id: 'courses', label: 'Courses', icon: 'cart', render: renderGroceries },
  { id: 'profil', label: 'Profil', icon: 'user', render: renderProfile },
];
const scrolls = {};

function shell() {
  $('#app').innerHTML = `
    <main id="view" tabindex="-1"></main>
    <nav id="nav" aria-label="Navigation">
      ${TABS.map(t => t.id === 'add'
        ? `<button type="button" class="nav-add" data-tab="add" aria-label="Ajouter une recette">${icon('plus')}</button>`
        : `<button type="button" class="nav-i" data-tab="${t.id}">${icon(t.icon)}<span>${esc(t.label)}</span></button>`).join('')}
    </nav>
    <div id="pages"></div>`;
  $('#nav').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]');
    if (!b) return;
    if (b.dataset.tab === 'add') { openAddSheet(); return; }
    setTab(b.dataset.tab);
  });
}

function renderNav() {
  document.querySelectorAll('#nav [data-tab]').forEach(b => {
    if (b.dataset.tab === nav.tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
}
function renderTab() {
  const t = TABS.find(x => x.id === nav.tab) || TABS[0];
  const view = $('#view');
  view.dataset.tab = t.id;
  t.render(view);
}
function setTab(id, sub) {
  if (!TABS.some(t => t.id === id && t.render)) return;
  while (stack.length) stack[stack.length - 1].close();
  const view = $('#view');
  if (nav.tab === id) { view.scrollTo({ top: 0, behavior: 'smooth' }); }
  scrolls[nav.tab] = view.scrollTop;
  const changed = nav.tab !== id;
  nav.tab = id;
  renderNav();
  renderTab();
  if (changed) view.scrollTop = scrolls[id] || 0;
  if (id === 'profil' && sub) openProfileSection(sub);
}
nav.setTab = setTab;

let paintTimer = null;
function schedulePaint() {
  clearTimeout(paintTimer);
  paintTimer = setTimeout(() => { rebuildIndex(); renderTab(); refreshPages(); }, 30);
}

async function boot() {
  shell();
  nav.tab = 'recettes';
  renderNav();
  $('#view').innerHTML = '<div class="boot"><span class="spinner"></span></div>';
  try {
    await loadAll();
  } catch (e) {
    console.error(e);
    $('#view').innerHTML = `<div class="empty-state"><h2>Impossible d’ouvrir tes données</h2><p>${esc(e && e.message ? e.message : String(e))}</p></div>`;
    return;
  }
  rebuildIndex();
  if (['recettes', 'planning', 'courses', 'profil'].includes(S.prefs.tab)) nav.tab = S.prefs.tab;
  renderNav();
  renderTab();
  onChange(schedulePaint);
  onBack(() => {
    if (handleBack()) return true;
    if (nav.tab !== 'recettes') { setTab('recettes'); return true; }
    return false;
  });
  if (!S.settings.onboarded) openOnboarding();
  onShared(d => { clearShared(); handleShared(d); });
  if (!isNative) window.__agp = { S, handleShared, setTab };
}

window.addEventListener('error', e => { console.error(e.error || e.message); toast('Oups, une erreur : ' + (e.message || 'inconnue')); });
window.addEventListener('unhandledrejection', e => { console.error(e.reason); toast('Oups : ' + (e.reason && e.reason.message ? e.reason.message : 'erreur')); });

boot();

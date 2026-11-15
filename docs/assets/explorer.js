// Explorer flowchart: expand/collapse all, Flow B / C highlight, search (outlines matching boxes and opens their
// parents), deep links (#id opens every ancestor box).
(function () {
const main = document.querySelector('main.explorer');
const nodes = () => [...main.querySelectorAll('details.node')];
const openTo = el => { for (let d = el; d && d !== main; d = d.parentElement) if (d.tagName === 'DETAILS') d.open = true; };

// Expand all stops above the code level: the source boxes stay closed (open them one at a time).
document.getElementById('open-all').onclick = () => nodes().forEach(d => { d.open = !d.classList.contains('c'); });
document.getElementById('close-all').onclick = () => nodes().forEach(d => { d.open = false; });

// Flow filter: dims the boxes that do not run in the chosen flow.
const flowBtns = [...document.querySelectorAll('#toolbar [data-show]')];
flowBtns.forEach(b => b.onclick = () => {
  main.dataset.show = b.dataset.show;
  flowBtns.forEach(x => x.classList.toggle('on', x === b));
});

function fromHash() {
  const id = decodeURIComponent(location.hash.slice(1));
  const el = id && document.getElementById(id);
  if (el) { openTo(el); el.scrollIntoView({ block: 'center' }); }
}
window.addEventListener('hashchange', fromHash);
fromHash();
document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="#"]');
  if (!a) return;
  const el = document.getElementById(a.getAttribute('href').slice(1));
  if (el) { openTo(el); setTimeout(() => el.scrollIntoView({ block: 'center', behavior: 'smooth' }), 0); }
});

// Search: a box is a hit when its own title or body text (not its nested boxes) contains the term.
const q = document.getElementById('q'), hits = document.getElementById('hits');
const norm = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const ownText = d => {
  if (d.classList.contains('c')) return norm(d.querySelector('summary').textContent);  // name + file, not the source
  const c = d.cloneNode(true);
  c.querySelectorAll('details.node').forEach(n => n.remove());
  return norm(c.textContent);
};
let saved = null;
q.addEventListener('input', () => {
  const term = norm(q.value.trim());
  nodes().forEach(d => d.classList.remove('hit'));
  if (!term) {
    if (saved) nodes().forEach((d, i) => { d.open = saved[i]; });
    saved = null; hits.textContent = '';
    return;
  }
  if (!saved) saved = nodes().map(d => d.open);
  nodes().forEach(d => { d.open = false; });
  const found = nodes().filter(d => ownText(d).includes(term));
  found.forEach(d => { d.classList.add('hit'); for (let p = d.parentElement; p && p !== main; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true; });
  hits.textContent = found.length ? `${found.length} box${found.length > 1 ? 'es' : ''}` : 'no match';
  if (found[0]) found[0].scrollIntoView({ block: 'center' });
});
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); }
  if (e.key === 'Escape' && document.activeElement === q) { q.value = ''; q.dispatchEvent(new Event('input')); }
});

// Keyboard: Tab / Shift+Tab move box to box (summaries are focusable). Arrows walk the tree:
// → open, or go into the first box inside · ← close, or go up to the parent · ↓ ↑ next / previous visible box.
const visible = () => [...main.querySelectorAll('details.node>summary')].filter(s => s.offsetParent !== null);
const parentNode = d => d.parentElement && d.parentElement.closest('details.node');
const focus = s => { if (s) { s.focus(); s.scrollIntoView({ block: 'nearest' }); } };
main.addEventListener('keydown', e => {
  const s = e.target.closest('details.node>summary');
  if (!s || e.target !== s || e.altKey || e.ctrlKey || e.metaKey) return;
  const d = s.parentElement, list = visible(), i = list.indexOf(s);
  if (e.key === 'ArrowRight') {
    e.preventDefault();
    if (!d.open) d.open = true;
    else focus(d.querySelector(':scope>.body details.node>summary'));
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault();
    if (d.open) d.open = false;
    else { const p = parentNode(d); if (p) focus(p.querySelector(':scope>summary')); }
  } else if (e.key === 'ArrowDown') { e.preventDefault(); focus(list[i + 1]); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); focus(list[i - 1]); }
});

// Breadcrumb: where the focused box sits in the tree.
const crumbs = document.getElementById('crumbs');
const title = d => d.querySelector(':scope>summary b').textContent.trim();
const showPath = d => {
  const path = [];
  for (let n = d; n; n = parentNode(n)) path.unshift(title(n));
  crumbs.innerHTML = path.map((t, k) => k === path.length - 1 ? `<b>${t.replace(/</g, '&lt;')}</b>` : t.replace(/</g, '&lt;')).join(' › ');
};
main.addEventListener('focusin', e => { const s = e.target.closest('details.node>summary'); if (s) showPath(s.parentElement); });
main.addEventListener('toggle', e => { if (e.target.matches('details.node') && e.target.open) showPath(e.target); }, true);
})();

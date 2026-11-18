'use strict';
// Klip review: confirm outings, then review clips in a grid. Every answer is
// saved to the owner ledger as soon as it is given.
const TOKEN = document.querySelector('meta[name="klip-token"]').content;
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

async function api(path, body) {
  const opts = { headers: { 'X-Klip-Token': TOKEN } };
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(path, opts);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function say(text, error = false) {
  const m = $('message');
  m.textContent = text;
  m.className = error ? 'error' : '';
}

const withToken = (url) => `${url}?token=${encodeURIComponent(TOKEN)}`;

function when(iso) {
  if (!iso) return 'undated';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric',
    year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ---- tabs ----------------------------------------------------------------------

let dirty = false;
function show(tab) {
  for (const name of ['outings', 'review']) {
    $(`${name}-view`).hidden = name !== tab;
    $(`tab-${name}`).setAttribute('aria-pressed', String(name === tab));
  }
  if (tab === 'outings') loadOutings();
  else loadDecks();
}
$('tab-outings').addEventListener('click', () => show('outings'));
$('tab-review').addEventListener('click', () => {
  if (dirty && !confirm('Unsaved outing changes will be lost. Continue?')) return;
  dirty = false;
  show('review');
});
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

// ---- outings -------------------------------------------------------------------

let roster = null;

async function loadOutings() {
  try {
    roster = await api('/api/roster');
    dirty = false;
    renderOutings();
  } catch (e) { say(e.message, true); }
}

function uniqueName(base) {
  const names = new Set(roster.outings.map((o) => o.outing));
  let n = 2;
  while (names.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

function summarize(o) {
  const times = o.recordings.map((r) => r.captureTime).filter(Boolean).sort();
  o.start = times[0] || null;
  o.end = times[times.length - 1] || null;
  o.minutes = Math.round(o.recordings.reduce((s, r) => s + r.minutes, 0) * 10) / 10;
}

function renderOutings() {
  const view = $('outings-view');
  view.replaceChildren();
  const unset = roster.outings.filter((o) => !o.sport).length;
  const confirmed = roster.status === 'owner_confirmed';
  view.append(
    el('p', { class: 'muted', text:
      'Each card is one outing, grouped by gaps in recording time. Set the sport, merge days ' +
      'of the same trip into one outing (they share scenery, so testing on one after training ' +
      'on another would flatter the model), split an outing that mixes two days out, then confirm.' }),
    el('div', { class: 'bar' },
      el('span', { class: `badge ${confirmed ? 'ok' : ''}`, text: confirmed ? 'Confirmed' : 'Not confirmed' }),
      el('span', { class: 'muted', text: `${roster.outings.length} outings · ${unset} without a sport` }),
      el('button', { text: 'Save draft', onclick: () => saveRoster('provisional') }),
      el('button', { class: 'primary', text: 'Confirm outings', onclick: () => saveRoster('owner_confirmed') })));
  roster.outings.forEach((o, i) => view.append(outingCard(o, i)));
}

function outingCard(o, i) {
  const sport = el('select', { 'aria-label': `Sport for ${o.outing}`,
    onchange: (e) => { o.sport = e.target.value || null; dirty = true; renderOutings(); } },
    el('option', { value: '', text: '— sport —' }),
    ...roster.sports.map((s) => el('option', { value: s, text: s, selected: o.sport === s })));
  const picks = [0, Math.floor(o.recordings.length / 2), o.recordings.length - 1]
    .filter((v, k, a) => a.indexOf(v) === k);
  const thumbs = el('div', { class: 'thumbs' }, ...picks.map((k) => el('img', {
    src: withToken(`/thumb/${o.recordings[k].sha}`), loading: 'lazy',
    alt: `Frame from ${o.recordings[k].file}` })));
  const list = el('ul', { class: 'recs', hidden: true }, ...o.recordings.map((r, k) => el('li', {},
    el('span', { class: 'file', text: r.file }),
    el('span', { class: 'muted', text: `${when(r.captureTime)} · ${r.minutes} min${r.scanned ? ' · scanned' : ''}` }),
    k > 0 ? el('button', { text: 'Split here', title: 'Start a new outing at this recording',
      onclick: () => {
        const rest = o.recordings.splice(k);
        const next = { outing: uniqueName(o.outing), sport: o.sport, recordings: rest };
        summarize(o); summarize(next);
        roster.outings.splice(i + 1, 0, next);
        dirty = true; renderOutings();
      } }) : null)));
  const scanned = o.recordings.filter((r) => r.scanned).length;
  return el('div', { class: `card ${o.sport ? '' : 'unset'}` },
    el('div', { class: 'card-head' },
      el('h3', { text: `${when(o.start)} → ${o.end ? new Date(o.end).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''}` }),
      el('span', { class: 'muted', text: `${o.recordings.length} recordings · ${o.minutes} min · ${scanned} scanned` }),
      sport,
      el('button', { text: 'Merge into previous', disabled: i === 0,
        title: 'Same trip as the outing above', onclick: () => {
          const prev = roster.outings[i - 1];
          prev.recordings.push(...o.recordings);
          prev.sport = prev.sport || o.sport;
          summarize(prev);
          roster.outings.splice(i, 1);
          dirty = true; renderOutings();
        } }),
      el('button', { text: 'Recordings', 'aria-expanded': 'false', onclick: (e) => {
        list.hidden = !list.hidden;
        e.target.setAttribute('aria-expanded', String(!list.hidden));
      } })),
    thumbs, list);
}

async function saveRoster(status) {
  try {
    const result = await api('/api/roster', { status, outings: roster.outings.map((o) => ({
      outing: o.outing, sport: o.sport, sources: o.recordings.map((r) => r.sha) })) });
    dirty = false;
    say(`Saved ${result.outings} outings (${result.status === 'owner_confirmed' ? 'confirmed' : 'draft'}).`);
    await loadOutings();
  } catch (e) { say(e.message, true); }
}

// ---- review rounds ---------------------------------------------------------------

async function loadDecks() {
  const view = $('review-view');
  try {
    const data = await api('/api/decks');
    const sport = el('select', { 'aria-label': 'Sport for the new round' },
      el('option', { value: '', text: 'all sports' }),
      ...data.sports.map((s) => el('option', { value: s, text: s })));
    const screens = el('input', { type: 'number', min: 4, max: 120, value: 30, 'aria-label': 'Screens' });
    const rows = data.decks.map((d) => el('tr', {},
      el('td', { text: d.created || '' }), el('td', { text: d.sport || 'all' }),
      el('td', { text: `${d.answered} / ${d.screens}` }),
      el('td', {}, el('button', { text: d.answered >= d.screens ? 'Review again' : 'Continue',
        onclick: () => openDeck(d.id) }))));
    view.replaceChildren(
      el('p', { class: 'muted', text:
        'Each screen shows four moments from one outing. Tick every one you would keep, star the ' +
        'best, press Enter. Answers save immediately; you can stop and continue any time.' }),
      el('div', { class: 'bar' },
        el('span', { text: `${data.scannedRecordings} scanned recordings` }), sport, screens,
        el('button', { class: 'primary', text: 'Start a new round', onclick: async () => {
          try {
            const made = await api('/api/decks', { sport: sport.value || null,
              screens: Number(screens.value) });
            say(`New round: ${made.screens} screens from ${made.outings} outings.`);
            openDeck(made.id);
          } catch (e) { say(e.message, true); }
        } })),
      rows.length ? el('table', {}, el('thead', {}, el('tr', {},
        el('th', { text: 'Started' }), el('th', { text: 'Sport' }), el('th', { text: 'Answered' }),
        el('th', { text: '' }))), el('tbody', {}, ...rows)) : el('p', { class: 'muted', text: 'No rounds yet.' }));
  } catch (e) { say(e.message, true); }
}

let deck = null, at = 0, keep = new Set(), best = null, heard = -1, rate = 1;

async function openDeck(id) {
  try {
    deck = await api(`/api/deck?id=${encodeURIComponent(id)}`);
    at = deck.screens.findIndex((s) => !deck.answers[s.index]);
    if (at < 0) at = 0;
    renderScreen();
  } catch (e) { say(e.message, true); }
}

function tiles() { return [...document.querySelectorAll('.tile')]; }

function paint() {
  tiles().forEach((t, k) => {
    t.classList.toggle('keep', keep.has(k));
    t.classList.toggle('best', best === k);
    t.setAttribute('aria-pressed', String(keep.has(k)));
    t.querySelector('.mark').textContent = best === k ? '★ best' : keep.has(k) ? 'keep' : '';
    t.querySelector('video').muted = heard !== k;
  });
  const next = $('next');
  if (next) next.textContent = keep.size ? `Next: keep ${keep.size} (Enter)` : 'Next: none worth keeping (Enter)';
  const snd = $('sound');
  if (snd) snd.textContent = `Sound: ${heard < 0 ? 'off' : heard + 1}`;
}

function renderScreen() {
  const view = $('review-view');
  const done = Object.keys(deck.answers).length;
  if (at >= deck.screens.length) {
    view.replaceChildren(el('h2', { text: 'Round complete' }),
      el('p', { text: `${done} of ${deck.screens.length} screens answered and saved.` }),
      el('button', { class: 'primary', text: 'Back to rounds', onclick: loadDecks }));
    return;
  }
  const screen = deck.screens[at];
  const prior = deck.answers[screen.index];
  keep = new Set(prior && !prior.cannotJudge ? prior.keep : []);
  best = prior && !prior.cannotJudge ? prior.best : null;
  const grid = el('div', { class: 'grid' }, ...screen.clips.map((src, k) => {
    const video = el('video', { src: withToken(src), loop: true, muted: true, playsinline: true,
      preload: 'auto' });
    video.muted = true;
    video.playbackRate = rate;
    video.play().catch(() => {});
    const tile = el('div', { class: 'tile', tabindex: 0, role: 'button', 'aria-label': `Clip ${k + 1}`,
      onclick: () => toggle(k), ondblclick: () => star(k) },
      el('span', { class: 'num', text: String(k + 1) }), el('span', { class: 'mark' }), video);
    return tile;
  }));
  view.replaceChildren(
    el('p', { class: 'muted', text:
      '1–4 or click: keep · Shift+1–4 or double-click: best · Enter: next (nothing ticked = none worth keeping) · ' +
      'X: can\'t judge · U: back · S: sound · F: speed' }),
    grid,
    el('div', { class: 'bar' },
      el('button', { id: 'next', class: 'primary', onclick: () => submit(false) }),
      el('button', { text: "Can't judge (X)", onclick: () => submit(true) }),
      el('button', { text: 'Back (U)', disabled: at === 0, onclick: back }),
      el('button', { id: 'sound', onclick: sound }),
      el('button', { text: `Speed ${rate}x`, onclick: (e) => {
        rate = rate === 1 ? 2 : 1;
        e.target.textContent = `Speed ${rate}x`;
        tiles().forEach((t) => { t.querySelector('video').playbackRate = rate; });
      } }),
      el('progress', { max: deck.screens.length, value: done }),
      el('span', { text: `Screen ${at + 1} of ${deck.screens.length}${prior ? ' (answered)' : ''}` }),
      el('button', { text: 'Back to rounds', onclick: loadDecks })));
  paint();
}

function toggle(k) {
  if (keep.has(k)) { keep.delete(k); if (best === k) best = null; } else keep.add(k);
  paint();
}
function star(k) {
  if (best === k) best = null; else { best = k; keep.add(k); }
  paint();
}
function sound() { heard = heard + 1 >= tiles().length ? -1 : heard + 1; paint(); }
function back() { if (at > 0) { at -= 1; renderScreen(); } }

let saving = false;
async function submit(cannot) {
  if (!deck || at >= deck.screens.length || saving) return;
  const screen = deck.screens[at];
  const answer = { deck: deck.id, index: screen.index,
    keep: cannot ? [] : [...keep].sort((a, b) => a - b), best: cannot ? null : best, cannotJudge: cannot };
  saving = true;
  try {
    await api('/api/answer', answer);
    deck.answers[screen.index] = answer;
    at += 1;
    renderScreen();
  } catch (e) {
    say(`Not saved: ${e.message}`, true);
  } finally { saving = false; }
}

document.addEventListener('keydown', (e) => {
  if ($('review-view').hidden || !deck || !document.querySelector('.tile')) return;
  if (['INPUT', 'SELECT'].includes(e.target.tagName)) return;
  if (e.target.tagName === 'BUTTON' && (e.key === ' ' || e.key === 'Enter')) return;
  const digit = e.code && e.code.startsWith('Digit') ? Number(e.code.slice(5)) - 1 : -1;
  if (digit >= 0 && digit < tiles().length) { e.preventDefault(); if (e.shiftKey) star(digit); else toggle(digit); }
  else if (e.key === 'Enter') { e.preventDefault(); submit(false); }
  else if (e.key === 'x' || e.key === 'X') submit(true);
  else if (e.key === 'u' || e.key === 'U') back();
  else if (e.key === 's' || e.key === 'S') sound();
  else if (e.key === 'f' || e.key === 'F') document.querySelector('.bar button:nth-of-type(5)')?.click();
});

show('outings');

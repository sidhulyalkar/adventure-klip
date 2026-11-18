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

// ---- trips, days and recordings -------------------------------------------------

let roster = null;
const LABEL = { unknown: 'unlabelled', accidental: 'accidental (leave out)' };
const label = (a) => LABEL[a] || a;

async function loadOutings() {
  try {
    roster = await api('/api/roster');
    dirty = false;
    renderOutings();
  } catch (e) { say(e.message, true); }
}

function changed() { dirty = true; renderOutings(); }

function uniqueName(base, taken) {
  let n = 2;
  while (taken.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}
const dayNames = () => new Set(roster.trips.flatMap((t) => t.outings.map((o) => o.outing)));
const tripNames = () => new Set(roster.trips.map((t) => t.trip));

function span(recs) {
  const times = recs.map((r) => r.captureTime).filter(Boolean).sort();
  const minutes = Math.round(recs.reduce((s, r) => s + r.minutes, 0) * 10) / 10;
  return { start: times[0] || null, end: times[times.length - 1] || null, minutes };
}
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '');
const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : 'undated');

function activitySelect(value, onchange, aria) {
  return el('select', { 'aria-label': aria, onchange },
    ...[roster.unlabelled, ...roster.activities].map((a) => el('option', { value: a, text: label(a), selected: a === value })));
}

function renderOutings() {
  const view = $('outings-view');
  view.replaceChildren();
  const recs = roster.trips.flatMap((t) => t.outings.flatMap((o) => o.recordings));
  const unlabelled = recs.filter((r) => r.activity === roster.unlabelled).length;
  const confirmed = roster.status === 'owner_confirmed';
  view.append(
    el('p', { class: 'muted', text:
      'Trips hold days; days hold recordings. Recordings were grouped by gaps in recording time ' +
      '(a new day after 6 hours, a new trip after 4 days). Sorting happens per day; a model is ' +
      'always tested on a whole trip it never trained on. Give each recording an activity: ' +
      'fill a day in one go (only unlabelled recordings change), then fix single recordings. ' +
      'Mark mistakes as accidental: they stay in their day but are left out of review and ranking.' }),
    el('div', { class: 'bar' },
      el('span', { class: `badge ${confirmed ? 'ok' : ''}`, text: confirmed ? 'Confirmed' : 'Not confirmed' }),
      el('span', { class: 'muted', text:
        `${roster.trips.length} trips · ${roster.trips.reduce((n, t) => n + t.outings.length, 0)} days · ` +
        `${recs.length} recordings · ${unlabelled} unlabelled` }),
      el('button', { text: 'Save draft', onclick: () => saveRoster('provisional') }),
      el('button', { class: 'primary', text: 'Confirm', onclick: () => saveRoster('owner_confirmed') })));
  roster.trips.forEach((t, ti) => view.append(tripCard(t, ti)));
}

function tripCard(t, ti) {
  const all = t.outings.flatMap((o) => o.recordings);
  const s = span(all);
  const place = el('input', { type: 'text', value: t.place || '', placeholder: 'Place (e.g. Moorea)',
    'aria-label': `Place for trip starting ${day(s.start)}`, maxlength: 120,
    onchange: (e) => { t.place = e.target.value.trim() || null; dirty = true; } });
  return el('section', { class: 'trip' },
    el('div', { class: 'card-head' },
      el('h2', { text: `Trip · ${day(s.start)} → ${day(s.end)}` }),
      el('span', { class: 'muted', text: `${t.outings.length} days · ${all.length} recordings · ${s.minutes} min` }),
      place,
      el('button', { text: 'Merge into previous trip', disabled: ti === 0, onclick: () => {
        roster.trips[ti - 1].outings.push(...t.outings);
        roster.trips.splice(ti, 1);
        changed();
      } })),
    ...t.outings.map((o, oi) => dayCard(t, ti, o, oi)));
}

function dayCard(t, ti, o, oi) {
  const s = span(o.recordings);
  const unlabelled = o.recordings.filter((r) => r.activity === roster.unlabelled).length;
  const fill = activitySelect(roster.unlabelled, () => {}, `Activity to fill for ${day(s.start)}`);
  const picks = [0, Math.floor(o.recordings.length / 2), o.recordings.length - 1]
    .filter((v, k, a) => a.indexOf(v) === k);
  const list = el('ul', { class: 'recs', hidden: !o.open }, ...o.recordings.map((r, k) => el('li', {},
    o.open ? el('img', { class: 'mini', src: withToken(`/thumb/${r.sha}`), loading: 'lazy', alt: `Frame from ${r.file}` }) : null,
    el('span', { class: 'file', text: r.file }),
    el('span', { class: 'muted', text: `${clock(r.captureTime)} · ${r.minutes} min${r.scanned ? ' · scanned' : ''}` }),
    activitySelect(r.activity, (e) => { r.activity = e.target.value; changed(); }, `Activity for ${r.file}`),
    k > 0 ? el('button', { text: 'New day from here', onclick: () => {
      const rest = o.recordings.splice(k);
      t.outings.splice(oi + 1, 0, { outing: uniqueName(o.outing, dayNames()), place: o.place, recordings: rest });
      changed();
    } }) : null)));
  return el('div', { class: `card ${unlabelled ? 'unset' : ''}` },
    el('div', { class: 'card-head' },
      el('h3', { text: `${day(s.start)} · ${clock(s.start)} → ${clock(s.end)}` }),
      el('span', { class: 'muted', text: `${o.recordings.length} recordings · ${s.minutes} min · ${unlabelled} unlabelled` }),
      fill,
      el('button', { text: 'Fill unlabelled', onclick: () => {
        if (fill.value === roster.unlabelled) return;
        o.recordings.forEach((r) => { if (r.activity === roster.unlabelled) r.activity = fill.value; });
        changed();
      } }),
      el('button', { text: 'Merge into previous day', disabled: oi === 0, onclick: () => {
        t.outings[oi - 1].recordings.push(...o.recordings);
        t.outings.splice(oi, 1);
        changed();
      } }),
      el('button', { text: 'New trip from here', disabled: oi === 0, onclick: () => {
        const moved = t.outings.splice(oi);
        roster.trips.splice(ti + 1, 0, { trip: uniqueName(t.trip, tripNames()), place: null, outings: moved });
        changed();
      } }),
      el('button', { text: o.open ? 'Hide recordings' : 'Recordings', 'aria-expanded': String(!!o.open),
        onclick: () => { o.open = !o.open; renderOutings(); } })),
    el('div', { class: 'thumbs' }, ...picks.map((k) => el('img', {
      src: withToken(`/thumb/${o.recordings[k].sha}`), loading: 'lazy',
      alt: `Frame from ${o.recordings[k].file}` }))),
    list);
}

async function saveRoster(status) {
  try {
    const result = await api('/api/roster', { status, trips: roster.trips.map((t) => ({
      trip: t.trip, place: t.place, outings: t.outings.map((o) => ({
        outing: o.outing, place: o.place, sources: o.recordings.map((r) => ({ sha: r.sha, activity: r.activity })) })) })) });
    dirty = false;
    say(`Saved ${result.trips} trips, ${result.outings} days` +
        `${result.unlabelled ? `, ${result.unlabelled} recordings still unlabelled` : ''}` +
        ` (${result.status === 'owner_confirmed' ? 'confirmed' : 'draft'}).`);
    await loadOutings();
  } catch (e) { say(e.message, true); }
}

// ---- review rounds ---------------------------------------------------------------

async function loadDecks() {
  const view = $('review-view');
  try {
    const data = await api('/api/decks');
    const sport = el('select', { 'aria-label': 'Activity for the new round' },
      el('option', { value: '', text: 'all activities (mixed per day)' }),
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

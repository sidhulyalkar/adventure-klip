'use strict';
// Klip review: organize trips, days and activities (saved automatically), then
// review clips in a grid. Every answer is saved as soon as it is given.
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

function show(tab) {
  for (const name of ['outings', 'review']) {
    $(`${name}-view`).hidden = name !== tab;
    $(`tab-${name}`).setAttribute('aria-pressed', String(name === tab));
  }
  if (tab === 'outings') loadLibrary();
  else loadDecks();
}
$('tab-outings').addEventListener('click', () => show('outings'));
$('tab-review').addEventListener('click', async () => { await flush(); show('review'); });
window.addEventListener('beforeunload', (e) => {
  if (pending || inflight) { e.preventDefault(); e.returnValue = ''; }
});

// ---- organizer: trips -> days -> recordings, saved automatically -------------------
//
// Click selects a recording, Shift-click a range, Cmd/Ctrl-click adds. Keys 1-8 set the
// activity of everything selected, 0 clears it. Suggestions (a guessed activity, very
// short clips) are shown but never applied unless you accept them.

let lib = null;            // server view: {status, activities, unlabelled, trips, recordings}
let pos = { t: 0, d: 0 };  // selected trip and day
let picked = new Set();    // selected recording SHAs
let anchor = null;         // for Shift-click ranges
let pending = null, inflight = false, saveError = null;
const LABEL = { unknown: 'unlabelled', accidental: 'accidental' };
const label = (a) => LABEL[a] || a;
const keyOf = (a) => lib.activities.indexOf(a) + 1;

async function loadLibrary() {
  try {
    lib = await api('/api/library');
    pos.t = Math.min(pos.t, lib.trips.length - 1);
    pos.d = Math.min(pos.d, lib.trips[pos.t].days.length - 1);
    renderLibrary();
  } catch (e) { say(e.message, true); }
}

function body(status) {
  return {
    status: status || lib.status,
    trips: lib.trips.map((t) => ({ trip: t.trip, title: t.title, place: t.place, note: t.note,
      days: t.days.map((d) => ({ outing: d.outing, title: d.title, place: d.place, note: d.note,
        recordings: d.recordings })) })),
    recordings: Object.fromEntries(Object.entries(lib.recordings).map(([sha, r]) =>
      [sha, { activity: r.activity, note: r.note, tags: r.tags }])),
  };
}

function changed({ redraw = true } = {}) {
  if (lib.status === 'owner_confirmed') lib.status = 'provisional';  // edits reopen it
  if (redraw) renderLibrary();
  clearTimeout(pending);
  pending = setTimeout(save, 700);
  saveState();
}

async function save(status) {
  clearTimeout(pending);
  pending = null;
  if (inflight) { pending = setTimeout(save, 300); return; }
  inflight = true;
  saveState();
  try {
    const result = await api('/api/library', body(status));
    lib.status = result.status;
    saveError = null;
  } catch (e) { saveError = e.message; }
  inflight = false;
  saveState();
  if (status) renderLibrary();
}

async function flush() {
  if (pending) await save();
  while (inflight) await new Promise((r) => setTimeout(r, 50));
}

function saveState() {
  if (saveError) say(`Not saved: ${saveError}`, true);
  else if (pending || inflight) say('Saving…');
  else say('All changes saved');
}

const unique = (base, taken) => { let n = 2; while (taken.has(`${base}-${n}`)) n += 1; return `${base}-${n}`; };
const dayIds = () => new Set(lib.trips.flatMap((t) => t.days.map((d) => d.outing)));
const tripIds = () => new Set(lib.trips.map((t) => t.trip));
const rec = (sha) => lib.recordings[sha];
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '');
const date = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }) : 'undated');
const minutes = (shas) => Math.round(shas.reduce((s, x) => s + rec(x).minutes, 0) * 10) / 10;
const unlabelled = (shas) => shas.filter((x) => rec(x).activity === lib.unlabelled).length;
const firstTime = (shas) => shas.map((x) => rec(x).captureTime).filter(Boolean).sort()[0];

function suggestion(r) {
  const h = r.hints || {};
  if (h.short) return { activity: 'accidental', why: 'under 3 s' };
  if (h.activity && h.activity.share >= 0.5) {
    return { activity: h.activity.proposal, why: `${Math.round(h.activity.share * 100)}% of frames` };
  }
  return null;
}

function currentDay() { return lib.trips[pos.t].days[pos.d]; }

function setActivity(activity) {
  if (!picked.size) { say('Select recordings first (click, Shift-click, A for all)'); return; }
  for (const sha of picked) rec(sha).activity = activity;
  changed();
}

function select(sha, e) {
  const order = currentDay().recordings;
  if (e && e.shiftKey && anchor && order.includes(anchor)) {
    const [a, b] = [order.indexOf(anchor), order.indexOf(sha)].sort((x, y) => x - y);
    if (!(e.metaKey || e.ctrlKey)) picked.clear();
    order.slice(a, b + 1).forEach((x) => picked.add(x));
  } else if (e && (e.metaKey || e.ctrlKey)) {
    if (picked.has(sha)) picked.delete(sha); else picked.add(sha);
    anchor = sha;
  } else {
    picked = picked.size === 1 && picked.has(sha) ? new Set() : new Set([sha]);
    anchor = sha;
  }
  renderLibrary();
}

function openDay(t, d) {
  pos = { t, d };
  picked.clear();
  anchor = null;
  renderLibrary();
}

// Structural edits. Every recording always stays in exactly one day.
function moveSelection(target) {
  const day = currentDay();
  const moving = day.recordings.filter((x) => picked.has(x));
  if (!moving.length) return;
  let dest;
  if (target === 'new-day') {
    dest = { outing: unique(day.outing, dayIds()), title: '', place: day.place, note: '', recordings: [] };
    lib.trips[pos.t].days.splice(pos.d + 1, 0, dest);
  } else {
    const [t, d] = target.split(':').map(Number);
    dest = lib.trips[t].days[d];
  }
  day.recordings = day.recordings.filter((x) => !picked.has(x));
  dest.recordings.push(...moving);
  dest.recordings.sort((a, b) => (rec(a).captureTime || '~').localeCompare(rec(b).captureTime || '~'));
  dropEmpty();
  locate(moving[0]);
  changed();
}

function dropEmpty() {
  lib.trips.forEach((t) => { t.days = t.days.filter((d) => d.recordings.length); });
  lib.trips = lib.trips.filter((t) => t.days.length);
}

function locate(sha) {
  lib.trips.forEach((t, ti) => t.days.forEach((d, di) => { if (d.recordings.includes(sha)) pos = { t: ti, d: di }; }));
}

function mergeDayIntoPrevious() {
  const trip = lib.trips[pos.t];
  if (pos.d === 0) return;
  trip.days[pos.d - 1].recordings.push(...currentDay().recordings);
  trip.days.splice(pos.d, 1);
  pos.d -= 1;
  changed();
}

function newTripFromDay() {
  const trip = lib.trips[pos.t];
  if (pos.d === 0) return;
  const moved = trip.days.splice(pos.d);
  lib.trips.splice(pos.t + 1, 0, { trip: unique(trip.trip, tripIds()), title: '', place: '', note: '', days: moved });
  pos = { t: pos.t + 1, d: 0 };
  changed();
}

function mergeTripIntoPrevious() {
  if (pos.t === 0) return;
  const prev = lib.trips[pos.t - 1];
  const at = prev.days.length + pos.d;
  prev.days.push(...lib.trips[pos.t].days);
  lib.trips.splice(pos.t, 1);
  pos = { t: pos.t - 1, d: at };
  changed();
}

function acceptSuggestions(shas) {
  let n = 0;
  for (const sha of shas) {
    const r = rec(sha), s = suggestion(r);
    if (s && r.activity === lib.unlabelled) { r.activity = s.activity; n += 1; }
  }
  say(n ? `Accepted ${n} suggestion${n > 1 ? 's' : ''}` : 'No suggestions for unlabelled recordings here');
  if (n) changed();
}

// ---- drawing --------------------------------------------------------------------

function field(value, placeholder, aria, onset, multiline = false) {
  return el(multiline ? 'textarea' : 'input', {
    type: multiline ? null : 'text', value: multiline ? null : value || '', placeholder,
    'aria-label': aria, maxlength: multiline ? 500 : 120, rows: multiline ? 2 : null,
    oninput: (e) => { onset(e.target.value); changed({ redraw: false }); },
  }, multiline ? document.createTextNode(value || '') : null);
}

function sidebar() {
  return el('nav', { class: 'side', 'aria-label': 'Trips and days' }, ...lib.trips.map((t, ti) => {
    const all = t.days.flatMap((d) => d.recordings);
    return el('section', { class: 'side-trip' },
      el('h3', { text: t.title || `Trip · ${date(firstTime(all))}` }),
      el('p', { class: 'muted small', text: `${t.place ? `${t.place} · ` : ''}${t.days.length} days · ${minutes(all)} min` }),
      el('ul', {}, ...t.days.map((d, di) => {
        const open = unlabelled(d.recordings);
        return el('li', {}, el('button', {
          class: `side-day ${ti === pos.t && di === pos.d ? 'current' : ''}`,
          'aria-current': ti === pos.t && di === pos.d ? 'true' : null,
          onclick: () => openDay(ti, di) },
        el('span', { text: d.title || date(firstTime(d.recordings)) }),
        el('span', { class: `count ${open ? 'todo' : 'done'}`,
          text: open ? `${open} to label` : '✓', 'aria-label': open ? `${open} unlabelled` : 'all labelled' })));
      })));
  }));
}

function recordingCard(sha) {
  const r = rec(sha), s = suggestion(r), h = r.hints || {};
  const isPicked = picked.has(sha);
  const badges = [
    h.slowMotion ? el('span', { class: 'tag', text: 'slow-mo' }) : null,
    h.short ? el('span', { class: 'tag warn', text: 'short' }) : null,
    r.retake ? el('span', { class: 'tag', text: 'retake' }) : null,
    r.note ? el('span', { class: 'tag', text: 'note' }) : null,
    ...(r.tags || []).slice(0, 3).map((t) => el('span', { class: 'tag', text: `#${t}` })),
  ];
  const card = el('div', { class: `rec ${isPicked ? 'picked' : ''} act-${r.activity}`, 'data-sha': sha },
    el('button', {
      class: 'rec-main', 'aria-pressed': String(isPicked),
      'aria-label': `${r.file}, ${clock(r.captureTime)}, ${r.minutes} minutes, ${label(r.activity)}`,
      onclick: (e) => select(sha, e), ondblclick: () => preview(sha) },
    el('img', { src: withToken(`/thumb/${sha}`), loading: 'lazy', alt: '' }),
    el('span', { class: 'chip', text: label(r.activity) }),
    el('span', { class: 'meta', text: `${clock(r.captureTime)} · ${r.minutes} min` })),
    el('div', { class: 'rec-foot' }, ...badges,
      s && r.activity === lib.unlabelled ? el('button', { class: 'suggest',
        title: 'A guess, not a decision: click to accept',
        text: `${label(s.activity)}? (${s.why})`,
        onclick: () => { r.activity = s.activity; changed(); } }) : null,
      el('button', { class: 'link', text: 'Play', 'aria-label': `Play ${r.file}`, onclick: () => preview(sha) })));
  return card;
}

function dayGrid(day) {
  // Recordings started within 2 minutes of the previous one form a burst.
  const groups = [];
  for (const sha of day.recordings) {
    const b = rec(sha).burst;
    if (!groups.length || groups[groups.length - 1].burst !== b) groups.push({ burst: b, shas: [] });
    groups[groups.length - 1].shas.push(sha);
  }
  return el('div', { class: 'bursts' }, ...groups.map((g) => el('section', { class: 'burst' },
    el('h4', { class: 'muted small', text: g.shas.length > 1 ?
      `Burst · ${g.shas.length} recordings · ${clock(rec(g.shas[0]).captureTime)}` :
      clock(rec(g.shas[0]).captureTime) }),
    el('div', { class: 'recs-grid' }, ...g.shas.map(recordingCard)))));
}

function details() {
  const shas = [...picked];
  const keys = el('div', { class: 'keys' },
    ...lib.activities.map((a) => el('button', { class: `act act-${a}`, onclick: () => setActivity(a),
      text: `${keyOf(a)} ${label(a)}` })),
    el('button', { class: 'act', onclick: () => setActivity(lib.unlabelled), text: '0 clear' }));
  if (!shas.length) {
    return el('aside', { class: 'details' },
      el('h3', { text: 'Nothing selected' }),
      el('p', { class: 'muted small', text:
        'Click a recording to select it, Shift-click for a range, Cmd/Ctrl-click to add, A for the whole day. ' +
        'Then press 1–8 (or click below) to set the activity. Double-click or P plays it.' }), keys);
  }
  if (shas.length > 1) {
    return el('aside', { class: 'details' }, el('h3', { text: `${shas.length} selected` }),
      el('p', { class: 'muted small', text: `${minutes(shas)} min · ${unlabelled(shas)} unlabelled` }), keys,
      el('button', { text: 'Accept their suggestions', onclick: () => acceptSuggestions(shas) }));
  }
  const r = rec(shas[0]), h = r.hints || {};
  return el('aside', { class: 'details' },
    el('h3', { text: r.file }),
    el('p', { class: 'muted small', text: [date(r.captureTime), clock(r.captureTime), `${r.minutes} min`,
      h.mode, r.scanned ? 'scanned' : 'not scanned yet'].filter(Boolean).join(' · ') }),
    h.activity ? el('p', { class: 'small', text:
      `Model guess: ${label(h.activity.proposal)} in ${Math.round(h.activity.share * 100)}% of frames (a suggestion, not a label)` }) : null,
    keys,
    el('label', { class: 'small', text: 'Note' }),
    field(r.note, 'What happens, who is there, why it matters', `Note for ${r.file}`, (v) => { r.note = v; }, true),
    el('label', { class: 'small', text: 'Tags (comma separated)' }),
    field((r.tags || []).join(', '), 'turtle, Maya, first run', `Tags for ${r.file}`,
      (v) => { r.tags = v.split(',').map((x) => x.trim()).filter(Boolean).slice(0, 30); }),
    el('button', { text: 'Play original (P)', onclick: () => preview(shas[0]) }));
}

function renderLibrary() {
  const view = $('outings-view');
  // Keep keyboard focus on the same recording across redraws.
  const focused = document.activeElement && document.activeElement.closest
    ? document.activeElement.closest('.rec')?.dataset.sha : null;
  const trip = lib.trips[pos.t], day = currentDay();
  const all = Object.keys(lib.recordings);
  const left = unlabelled(all);
  const confirmed = lib.status === 'owner_confirmed';
  const targets = lib.trips.flatMap((t, ti) => t.days.map((d, di) => (ti === pos.t && di === pos.d ? null :
    el('option', { value: `${ti}:${di}`, text: `${t.title || 'Trip'} · ${d.title || date(firstTime(d.recordings))}` }))));
  const move = el('select', { 'aria-label': 'Move selected recordings to', disabled: !picked.size,
    onchange: (e) => { if (e.target.value) moveSelection(e.target.value); } },
  el('option', { value: '', text: picked.size ? `Move ${picked.size} selected to…` : 'Move selected to…' }),
  el('option', { value: 'new-day', text: 'A new day after this one' }), ...targets);
  const suggestible = day.recordings.filter((x) => rec(x).activity === lib.unlabelled && suggestion(rec(x))).length;
  view.replaceChildren(
    el('div', { class: 'bar' },
      el('span', { class: `badge ${confirmed ? 'ok' : ''}`, text: confirmed ? 'Confirmed' : 'Draft' }),
      el('span', { class: 'muted', text: `${lib.trips.length} trips · ${lib.trips.reduce((n, t) => n + t.days.length, 0)} days · ${all.length} recordings · ${left} unlabelled` }),
      el('button', { class: 'primary', disabled: confirmed || left > 0,
        title: left ? 'Label every recording first (accidental counts as a label)' : '',
        text: confirmed ? 'Confirmed' : left ? `Confirm (${left} to label)` : 'Confirm trips, days and activities',
        onclick: () => save('owner_confirmed') })),
    el('div', { class: 'organizer' },
      sidebar(),
      el('section', { class: 'day', 'aria-label': 'Selected day' },
        el('div', { class: 'day-head' },
          el('div', { class: 'fields' },
            field(trip.title, 'Trip name (e.g. French Polynesia)', 'Trip name', (v) => { trip.title = v; }),
            field(trip.place, 'Trip place', 'Trip place', (v) => { trip.place = v; }),
            field(day.title, `Day name (${date(firstTime(day.recordings))})`, 'Day name', (v) => { day.title = v; }),
            field(day.place, 'Day place (e.g. Moorea lagoon)', 'Day place', (v) => { day.place = v; })),
          field(day.note, 'Day notes: who was there, what you did, what mattered', 'Day notes', (v) => { day.note = v; }, true),
          el('p', { class: 'muted small', text:
            `${date(firstTime(day.recordings))} · ${day.recordings.length} recordings · ${minutes(day.recordings)} min · ${unlabelled(day.recordings)} unlabelled` }),
          el('div', { class: 'bar' },
            el('button', { text: 'Select all (A)', onclick: () => { picked = new Set(day.recordings); renderLibrary(); } }),
            el('button', { text: `Accept suggestions (${suggestible})`, disabled: !suggestible,
              onclick: () => acceptSuggestions(day.recordings) }),
            move,
            el('button', { text: 'Merge into previous day', disabled: pos.d === 0, onclick: mergeDayIntoPrevious }),
            el('button', { text: 'Start a new trip here', disabled: pos.d === 0, onclick: newTripFromDay }),
            el('button', { text: 'Merge trip into previous', disabled: pos.t === 0, onclick: mergeTripIntoPrevious }))),
        dayGrid(day)),
      details()));
  if (focused) view.querySelector(`.rec[data-sha="${focused}"] .rec-main`)?.focus();
}

// ---- preview -----------------------------------------------------------------------

function preview(sha) {
  const r = rec(sha);
  const dialog = $('player');
  const video = el('video', { src: withToken(`/original/${sha}`), controls: true, autoplay: true,
    preload: 'metadata', 'aria-label': `Original ${r.file}` });
  dialog.replaceChildren(
    el('div', { class: 'bar' }, el('strong', { text: r.file }),
      el('span', { class: 'muted', text: `${clock(r.captureTime)} · ${r.minutes} min · ${label(r.activity)}` }),
      el('button', { text: 'Close (Esc)', onclick: () => dialog.close() })),
    video);
  dialog.addEventListener('close', () => { video.pause(); video.removeAttribute('src'); video.load(); }, { once: true });
  dialog.showModal();
}

document.addEventListener('keydown', (e) => {
  if ($('outings-view').hidden || !lib || $('player').open) return;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
  const digit = e.code && e.code.startsWith('Digit') ? Number(e.code.slice(5)) : -1;
  if (digit === 0) { e.preventDefault(); setActivity(lib.unlabelled); }
  else if (digit >= 1 && digit <= lib.activities.length) { e.preventDefault(); setActivity(lib.activities[digit - 1]); }
  else if (e.key === 'a' || e.key === 'A') { e.preventDefault(); picked = new Set(currentDay().recordings); renderLibrary(); }
  else if (e.key === 'Escape') { picked.clear(); renderLibrary(); }
  else if ((e.key === 'p' || e.key === 'P') && picked.size === 1) preview([...picked][0]);
  else if (e.key === 'j' || e.key === 'k') {
    const flat = lib.trips.flatMap((t, ti) => t.days.map((d, di) => [ti, di]));
    const at = flat.findIndex(([ti, di]) => ti === pos.t && di === pos.d);
    const next = flat[Math.max(0, Math.min(flat.length - 1, at + (e.key === 'j' ? 1 : -1)))];
    openDay(next[0], next[1]);
  }
});

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

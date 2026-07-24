"""
PWA-Bausteine: App-Icons, Manifest, Service Worker und die Handy-Oberflaeche.

Die Icons werden zur Laufzeit gezeichnet - reines Python, kein Pillow noetig.
So bleibt die Regel erhalten: Skript kopieren, starten, fertig.
"""

import struct
import zlib

INK = (11, 16, 32)
RAIL = (255, 90, 60)
STOPS = [(0.0, (125, 249, 255)), (0.45, (199, 125, 255)), (1.0, (255, 209, 102))]


def _lerp(t: float):
    """Holo-Verlauf: cyan -> lila -> amber."""
    for i in range(len(STOPS) - 1):
        a, ca = STOPS[i]
        b, cb = STOPS[i + 1]
        if a <= t <= b:
            f = (t - a) / (b - a)
            return tuple(int(ca[k] + (cb[k] - ca[k]) * f) for k in range(3))
    return STOPS[-1][1]


def _png(width: int, height: int, rows: list) -> bytes:
    """Minimaler PNG-Encoder (RGB, 8 bit, Filter 0)."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def icon(size: int, scale: int = 3) -> bytes:
    """
    Holo-Karte mit Glanzstreifen und Alarmschiene - dasselbe Zeichen wie
    im Dashboard. Wird ueberabgetastet und dann gemittelt, damit die
    Rundungen ohne Zeichenbibliothek glatt aussehen.
    """
    S = size * scale
    m = int(S * 0.28)
    cw = S - 2 * m
    ch = int(cw * 1.30)
    top = (S - ch) // 2
    radius = cw * 0.14
    rail_w = int(S * 0.062)
    rail_x1 = m - int(rail_w * 1.5) - rail_w
    rail_x2 = m - int(rail_w * 1.5)

    def inside_round(x, y, x0, y0, x1, y1, r):
        if not (x0 <= x < x1 and y0 <= y < y1):
            return False
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    big = []
    for y in range(S):
        row = []
        for x in range(S):
            px = INK
            if inside_round(x, y, rail_x1, top, rail_x2, top + ch, rail_w / 2):
                px = RAIL
            elif inside_round(x, y, m, top, m + cw, top + ch, radius):
                ly = y - top
                px = _lerp(ly / max(ch - 1, 1))
                # Glanzstreifen: schraege Bahn ueber die Karte
                band = 0.66 - (x - m) / max(cw, 1) * 0.36
                if band * ch <= ly <= band * ch + ch * 0.14:
                    px = tuple(min(255, int(c + (255 - c) * 0.28)) for c in px)
            row.append(px)
        big.append(row)

    # Herunterrechnen = Kantenglaettung
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0, 0, 0]
            for dy in range(scale):
                for dx in range(scale):
                    p = big[y * scale + dy][x * scale + dx]
                    for k in range(3):
                        acc[k] += p[k]
            n = scale * scale
            row += bytes(a // n for a in acc)
        rows.append(row)
    return _png(size, size, rows)


MANIFEST = """{
  "name": "PokeWatch",
  "short_name": "PokeWatch",
  "description": "Vorfaelle bei Pokemon- und TCG-Laeden",
  "start_url": "./index.html",
  "scope": "./",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#0b1020",
  "theme_color": "#0b1020",
  "lang": "de",
  "icons": [
    {"src": "icon-192.png", "sizes": "192x192", "type": "image/png",
     "purpose": "any maskable"},
    {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
     "purpose": "any maskable"}
  ]
}
"""

SERVICE_WORKER = """// PokeWatch Service Worker
// Huelle wird gecacht, Daten kommen bevorzugt frisch aus dem Netz.
const VERSION = 'pokewatch-v1';
const SHELL = ['./', './index.html', './app.html', './manifest.webmanifest',
               './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL))
              .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION)
                                  .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Periodic Background Sync gibt es nur unter Android/Chrome und nur fuer
// installierte Apps. Das Intervall bestimmt der Browser, nicht wir.
// Geholt wird bloss die fertige feed.json - suchen darf ein Service Worker
// nicht, er steckt in denselben CORS-Grenzen wie die Seite.
async function warmFeed() {
  try {
    const r = await fetch('./feed.json?t=' + Date.now(), {cache: 'reload'});
    if (r.ok) (await caches.open(VERSION)).put('./feed.json', r.clone());
  } catch (e) {}
}

self.addEventListener('periodicsync', e => {
  if (e.tag === 'pokewatch-refresh') e.waitUntil(warmFeed());
});

self.addEventListener('sync', e => {
  if (e.tag === 'pokewatch-refresh') e.waitUntil(warmFeed());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Daten: erst Netz, dann Cache. Sonst liest man auf der Couch von gestern.
  if (url.pathname.endsWith('feed.json')) {
    e.respondWith(
      fetch(e.request).then(r => {
        const copy = r.clone();
        caches.open(VERSION).then(c => c.put('./feed.json', copy));
        return r;
      }).catch(() => caches.match('./feed.json'))
    );
    return;
  }

  // Huelle: erst Cache, damit der Start auch ohne Netz sofort geht
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
"""


APP_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1020">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PokeWatch">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="manifest" href="manifest.webmanifest">
<title>PokeWatch</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#0b1020; --panel:#141b34; --panel-2:#1a2242; --line:#28325c;
  --paper:#e7eaf4; --muted:#8b96bd;
  --alert:#ff5a3c; --warn:#f5b32e; --cool:#5aa9e6; --legal:#a78bfa; --ship:#3ddc97;
  --holo:linear-gradient(100deg,#7df9ff,#c77dff 45%,#ffd166);
  --top:env(safe-area-inset-top); --bot:env(safe-area-inset-bottom);
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{overscroll-behavior-y:contain}
body{
  margin:0;background:var(--ink);color:var(--paper);
  font-family:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
  font-size:15px;line-height:1.5;
  padding-bottom:calc(28px + var(--bot));
}

/* ---------- Kopf ---------- */
.wrapmax{max-width:600px;margin:0 auto}
header{
  position:sticky;top:0;z-index:20;
  padding:calc(12px + var(--top)) 16px 0;
  background:rgba(11,16,32,.93);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line);
}
.bar{display:flex;align-items:center;gap:12px;max-width:600px;margin:0 auto}
.brand{
  font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:25px;
  text-transform:uppercase;letter-spacing:.01em;line-height:1;margin:0;flex:1
}
.brand em{font-style:normal;background:var(--holo);-webkit-background-clip:text;
  background-clip:text;color:transparent}
.icon{
  width:44px;height:44px;flex:0 0 44px;border:1px solid var(--line);
  background:var(--panel);color:var(--paper);border-radius:50%;
  display:grid;place-items:center;cursor:pointer;padding:0
}
.icon:disabled{opacity:.4}
.icon svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;
  stroke-linecap:round;stroke-linejoin:round}
#hunt{border-color:#3a4a86}
.icon.spin svg{animation:spin 1s linear infinite}
.panel{
  max-width:600px;margin:12px auto 0;background:var(--panel);
  border:1px solid var(--line);border-radius:11px;padding:15px
}
.panel .ph{font-family:"Barlow Condensed",sans-serif;font-weight:600;
  font-size:19px;margin:0 0 7px;text-transform:uppercase}
.panel .pt{font-size:12.5px;color:var(--muted);margin:0 0 13px;line-height:1.5}
.panel label{display:block;font-family:"IBM Plex Mono",monospace;font-size:10px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:11px}
.panel input{
  display:block;width:100%;margin-top:5px;background:var(--ink);
  border:1px solid var(--line);border-radius:7px;color:var(--paper);
  font-family:"IBM Plex Mono",monospace;font-size:13px;padding:13px 12px;
  min-height:44px
}
.panel input:focus{outline:2px solid var(--cool);outline-offset:1px}
.prow{display:flex;gap:8px}
.panel button{
  flex:1;background:var(--paper);color:var(--ink);border:0;border-radius:7px;
  font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;padding:14px 12px;min-height:44px;cursor:pointer
}
.panel button.ghost{background:transparent;color:var(--muted);
  border:1px solid var(--line)}
@keyframes spin{to{transform:rotate(360deg)}}
.status{
  max-width:600px;margin:7px auto 0;
  font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);
  display:flex;gap:8px;align-items:center
}
.status.warnstate{color:var(--warn)}
.status.offline{color:var(--alert)}

/* ---------- Filterchips ---------- */
.chips{
  max-width:600px;margin-inline:auto;
  display:flex;gap:7px;overflow-x:auto;padding:11px 0 12px;
  scrollbar-width:none;-webkit-overflow-scrolling:touch
}
.chips::-webkit-scrollbar{display:none}
.chip{
  flex:0 0 auto;background:transparent;border:1px solid var(--line);
  color:var(--muted);font-family:"IBM Plex Mono",monospace;font-size:11px;
  letter-spacing:.07em;text-transform:uppercase;padding:9px 14px;
  border-radius:999px;cursor:pointer;white-space:nowrap
}
.chip .n{opacity:.5;margin-left:7px;font-variant-numeric:tabular-nums}
.chip.on{background:var(--paper);color:var(--ink);border-color:var(--paper)}
.chip.on .n{opacity:.5}

/* ---------- Liste ---------- */
main{padding:14px 16px 0;max-width:600px;margin:0 auto}
.item{
  display:flex;background:var(--panel);border:1px solid var(--line);
  border-radius:11px;overflow:hidden;margin-bottom:9px;
  text-decoration:none;color:inherit
}
.head:active h2{color:var(--cool)}
.rail{width:4px;flex:0 0 4px;background:var(--line)}
.item[data-tone=alert] .rail{background:var(--alert)}
.item[data-tone=warn]  .rail{background:var(--warn)}
.item[data-tone=cool]  .rail{background:var(--cool)}
.item[data-tone=legal] .rail{background:var(--legal)}
.item[data-tone=ship]  .rail{background:var(--ship)}
.pad{padding:13px 14px;min-width:0;flex:1}
.top{
  display:flex;align-items:center;gap:7px;flex-wrap:wrap;
  font-family:"IBM Plex Mono",monospace;font-size:9.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);margin-bottom:7px
}
.top .cat{color:var(--paper)}
.top .when{margin-left:auto}
.flag{border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.flag.de{color:#ffd166;border-color:#5a4a1e}
h2{
  font-family:"Barlow Condensed",sans-serif;font-weight:600;font-size:19.5px;
  line-height:1.2;margin:0 0 5px
}
.head{display:flex;gap:11px;align-items:flex-start;text-decoration:none;color:inherit}
.art{width:34px;height:34px;flex:0 0 34px;border-radius:7px;object-fit:cover;
  background:var(--panel-2);margin-top:1px}
.art.wide{width:64px;height:44px;flex:0 0 64px;border-radius:6px}
.srcrow{display:flex;align-items:center;gap:10px;margin-top:6px}
.src{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--muted);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.more{
  flex:0 0 auto;background:transparent;border:1px solid var(--line);
  color:var(--cool);font-family:"IBM Plex Mono",monospace;font-size:10px;
  letter-spacing:.06em;text-transform:uppercase;padding:6px 13px;
  border-radius:999px;cursor:pointer;min-height:44px
}
.rel{margin-top:9px;border-top:1px solid var(--line);padding-top:9px;
  display:flex;flex-direction:column;gap:9px}
.rel a{display:block;text-decoration:none;color:var(--paper);font-size:13px;
  line-height:1.35;padding-left:11px;border-left:2px solid var(--line)}
.rel a span{display:block;font-family:"IBM Plex Mono",monospace;font-size:9.5px;
  letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.item.fresh{background:var(--panel-2)}
.item.fresh .rail{background:var(--holo)}
.badge{
  font-family:"IBM Plex Mono",monospace;font-size:8.5px;letter-spacing:.14em;
  color:var(--ink);background:var(--holo);padding:2px 6px;border-radius:3px
}

/* ---------- Zustaende ---------- */
.note{
  border:1px dashed var(--line);border-radius:11px;padding:30px 22px;
  text-align:center;color:var(--muted);font-size:13.5px;margin-top:6px
}
.note b{display:block;font-family:"Barlow Condensed",sans-serif;font-size:21px;
  color:var(--paper);text-transform:uppercase;margin-bottom:7px;font-weight:600}
.skel{height:74px;border-radius:11px;background:var(--panel);margin-bottom:9px;
  animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){
  .skel,#reload.spin svg{animation:none}
}
</style>
</head>
<body>

<header>
  <div class="bar">
    <h1 class="brand">Poke<em>Watch</em></h1>
    <button id="hunt" class="icon" aria-label="Neue Suche in der Cloud starten" hidden>
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    </button>
    <button id="reload" class="icon" aria-label="Neu laden">
      <svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/></svg>
    </button>
    <button id="gear" class="icon" aria-label="Einstellungen">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1"/></svg>
    </button>
  </div>
  <p class="status" id="status">Lade …</p>
  <div class="panel" id="panel" hidden>
    <p class="ph">Suchlauf vom Handy starten</p>
    <p class="pt">Die App selbst kann nicht suchen — der Browser verbietet den
      Zugriff auf fremde Seiten. Mit einem GitHub-Zugriffsschlüssel kann sie
      aber den Suchlauf in der Cloud anstoßen. Der Schlüssel bleibt auf diesem
      Gerät und steht nirgends im Programmcode.</p>
    <label>Repository<input id="repo" placeholder="deinname/pokewatch"
           autocapitalize="off" autocorrect="off" spellcheck="false"></label>
    <label>Token (fine-grained, nur Actions: Read and write)
      <input id="token" type="password" placeholder="github_pat_…"
             autocapitalize="off" autocorrect="off" spellcheck="false"></label>
    <div class="prow">
      <button id="save">Sichern</button>
      <button id="clear" class="ghost">Löschen</button>
    </div>
  </div>
  <div class="chips" id="regionChips"></div>
  <div class="chips" id="catChips" style="padding-top:0"></div>
</header>

<main id="list">
  <div class="skel"></div><div class="skel"></div><div class="skel"></div>
</main>
<!--SEED-->

<script>
const CATS = {
  einbruch:  ['Einbruch / Raub',        'alert'],
  betrug:    ['Betrug / Fakes',         'warn'],
  pleite:    ['Schliessung',            'cool'],
  behoerden: ['Razzia / Recht',         'legal'],
  versand:   ['Versand / Grading',      'ship']
};
const REGIONS = {de:'Deutschland', at:'Österreich', ch:'Schweiz', int:'International'};
const SHORT   = {de:'DE', at:'AT', ch:'CH', int:'INT'};
const LS = 'pokewatch:lastSeen';
// Auf false setzen, wenn keine Symbole neben den Meldungen erscheinen sollen.
const SHOW_LOGOS = true;

let items = [], generated = null, offline = false, builder = '';
// Deutschland ist der Startpunkt, nicht die Weltlage. Ueber die Knoepfe
// oben laesst sich jederzeit auf International oder Alle umschalten.
let fRegion = 'de', fCat = 'all';
let lastSeen = 0;
try { lastSeen = parseInt(localStorage.getItem(LS) || '0', 10) || 0; } catch (e) {}

const $ = id => document.getElementById(id);

function ago(iso) {
  const min = (Date.now() - new Date(iso)) / 60000;
  if (min < 60) return `vor ${Math.max(1, Math.round(min))} Min`;
  if (min < 1440) return `vor ${Math.round(min / 60)} Std`;
  const d = Math.round(min / 1440);
  return d === 1 ? 'gestern' : `vor ${d} Tagen`;
}

function chips(host, map, counts, active, onPick, immerAlle) {
  host.innerHTML = '';
  const add = (key, label, n) => {
    const b = document.createElement('button');
    b.className = 'chip' + (active() === key ? ' on' : '');
    b.innerHTML = label + (n != null ? `<span class="n">${n}</span>` : '');
    b.onclick = () => { onPick(key); render(); };
    host.appendChild(b);
  };
  add('all', 'Alle', null);
  Object.keys(map).forEach(k => {
    // Herkunft immer vollstaendig zeigen (immerAlle): sonst verschwindet
    // "Deutschland" an Tagen ohne deutsche Meldung, und weil das der
    // Startfilter ist, sieht man eine leere Liste ohne Weg zurueck.
    if (!counts[k] && !immerAlle) return;
    // CATS liefert [Label, Farbe], REGIONS nur einen String
    add(k, Array.isArray(map[k]) ? map[k][0] : map[k], counts[k] || 0);
  });
}

function render() {
  const byRegion = {}, byCat = {};
  items.forEach(i => {
    byRegion[i.region] = (byRegion[i.region] || 0) + 1;
    byCat[i.category] = (byCat[i.category] || 0) + 1;
  });
  chips($('regionChips'), REGIONS, byRegion, () => fRegion, k => fRegion = k, true);
  chips($('catChips'), CATS, byCat, () => fCat, k => fCat = k);

  const shown = items.filter(i =>
    (fRegion === 'all' || i.region === fRegion) &&
    (fCat === 'all' || i.category === fCat));

  const list = $('list');
  if (!shown.length) {
    // Beim Startfilter Deutschland ist "leer" der Normalfall an ruhigen
    // Tagen. Dann soll dastehen, wie viele Meldungen anderswo warten -
    // sonst wirkt die App kaputt.
    const anderswo = items.length - items.filter(i => i.region === fRegion).length;
    const hinweis = (fRegion !== 'all' && anderswo > 0)
      ? `Aus anderen Laendern liegen ${anderswo} Meldungen vor
         &mdash; oben auf „Alle" oder „International" tippen.`
      : 'Diese Kombination aus Herkunft und Vorfall ist leer.';
    list.innerHTML = `<div class="note"><b>Nichts hier</b> ${hinweis}</div>`;
    return;
  }

  list.innerHTML = shown.map((i, n) => {
    const [label, tone] = CATS[i.category] || ['Vorfall', 'cool'];
    const fresh = lastSeen && new Date(i.first_seen || i.published) > lastSeen;
    const more = (i.related || []).length;

    // Ladenlogo geht vor, sonst das Zeichen der Quelle. Faellt beides aus,
    // bleibt der Platz leer statt einen Platzhalter zu zeigen.
    const art = !SHOW_LOGOS ? '' : (i.logo || i.image
      || (i.domain ? `https://icons.duckduckgo.com/ip3/${i.domain}.ico` : ''));
    const wide = !!i.image;

    return `<article class="item${fresh ? ' fresh' : ''}" data-tone="${tone}">
      <div class="rail"></div>
      <div class="pad">
        <div class="top">
          <span class="cat">${label}</span>
          <span class="flag${i.region === 'de' ? ' de' : ''}">${SHORT[i.region] || '?'}</span>
          ${fresh ? '<span class="badge">NEU</span>' : ''}
          <span class="when">${ago(i.published)}</span>
        </div>
        <a class="head" href="${safeUrl(i.url)}" target="_blank" rel="noopener noreferrer">
          ${art ? `<img class="art${wide ? ' wide' : ''}" src="${esc(art)}" alt=""
                    loading="lazy" onerror="this.remove()">` : ''}
          <h2>${esc(i.title)}</h2>
        </a>
        <div class="srcrow">
          <span class="src">${esc(i.source)}</span>
          ${more ? `<button class="more" data-i="${n}">+${more} weitere</button>` : ''}
        </div>
        ${more ? `<div class="rel" id="rel${n}" hidden>${
          i.related.map(r => `<a href="${safeUrl(r.url)}" target="_blank"
             rel="noopener noreferrer"><span>${esc(r.source)}</span>${esc(r.title)}</a>`
          ).join('')}</div>` : ''}
      </div>
    </article>`;
  }).join('');

  list.querySelectorAll('.more').forEach(b => b.onclick = () => {
    const box = $('rel' + b.dataset.i);
    box.hidden = !box.hidden;
    b.textContent = box.hidden
      ? `+${box.children.length} weitere` : 'zuklappen';
  });
}

function esc(s) {
  return String(s || '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

// Die Adressen stammen aus fremden Feeds. Nur http(s) durchlassen,
// sonst koennte ein praeparierter Eintrag javascript: einschleusen.
function safeUrl(u) {
  try {
    const p = new URL(u, location.href);
    return (p.protocol === 'http:' || p.protocol === 'https:') ? esc(p.href) : '#';
  } catch (e) { return '#'; }
}

function setStatus() {
  const el = $('status');
  el.className = 'status';
  if (offline) {
    el.classList.add('offline');
    el.textContent = generated
      ? `Offline · Stand ${ago(generated)}` : 'Offline · keine Daten';
    return;
  }
  if (!generated) { el.textContent = `${items.length} Einträge`; return; }
  const hrs = (Date.now() - new Date(generated)) / 3600000;
  // Die Baunummer verraet, welche Fassung des Skripts diesen Feed gebaut
  // hat. Ohne sie raetselt man, ob in der Cloud noch eine alte Datei laeuft.
  el.textContent = `${items.length} Vorfälle · Stand ${ago(generated)}`
    + (builder ? ` · Bau ${builder}` : '');
  if (STANDALONE) {
    el.textContent += ' · feste Datei';
    if (hrs > 36) el.classList.add('warnstate');
    return;
  }
  if (hrs > 36) {
    el.classList.add('warnstate');
    el.textContent += ' · läuft der Abruf noch?';
  }
}

// Steckt der Datenstand fest in der Datei? Dann sofort anzeigen, ohne Netz.
// Das ist der Fall bei der Einzeldatei-Fassung, die man direkt oeffnet.
function readSeed() {
  const el = document.getElementById('seed');
  if (!el) return false;
  try {
    const d = JSON.parse(el.textContent);
    items = d.items || []; generated = d.generated || null; builder = d.builder || '';
    return items.length > 0;
  } catch (e) { return false; }
}

const STANDALONE = location.protocol === 'file:';

async function load(force) {
  const btn = $('reload');
  btn.disabled = true; btn.classList.add('spin');

  if (STANDALONE) {
    // Direkt geoeffnete Datei: es gibt nichts nachzuladen.
    btn.disabled = false; btn.classList.remove('spin');
    setStatus(); render();
    return;
  }

  try {
    const r = await fetch('feed.json?t=' + Date.now(),
                          {cache: force ? 'reload' : 'default'});
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    items = Array.isArray(data) ? data : (data.items || []);
    generated = Array.isArray(data) ? null : data.generated;
    builder = Array.isArray(data) ? '' : (data.builder || '');
    // Der Service Worker liefert notfalls aus dem Cache - der Abruf gelingt
    // also auch ohne Netz. Deshalb hier zusaetzlich den Verbindungsstatus
    // pruefen, sonst sieht alles frisch aus, obwohl es das nicht ist.
    offline = !navigator.onLine;
  } catch (e) {
    offline = true;
    if (!items.length) {
      $('list').innerHTML = `<div class="note"><b>Keine Verbindung</b>
        Es sind noch keine Daten gespeichert. Sobald du einmal online warst,
        ist der letzte Stand auch offline lesbar.</div>`;
    }
  }
  btn.disabled = false; btn.classList.remove('spin');
  setStatus();
  if (items.length) render();
}

// ---------- Suchlauf in der Cloud anstossen ----------
// Die App sucht nicht selbst. Sie bittet GitHub Actions, es zu tun, und
// wartet dann darauf, dass eine neuere feed.json auftaucht.
const CFG = 'pokewatch:gh';
let cfg = {};
try { cfg = JSON.parse(localStorage.getItem(CFG) || '{}'); } catch (e) {}

function applyCfg() {
  $('hunt').hidden = !(cfg.repo && cfg.token);
  $('repo').value = cfg.repo || '';
  $('token').value = cfg.token || '';
}

$('gear').onclick = () => { $('panel').hidden = !$('panel').hidden; };

$('save').onclick = () => {
  cfg = {repo: $('repo').value.trim().replace(/^https?:\/\/github\.com\//, ''),
         token: $('token').value.trim()};
  try { localStorage.setItem(CFG, JSON.stringify(cfg)); } catch (e) {}
  applyCfg();
  $('panel').hidden = true;
};

$('clear').onclick = () => {
  cfg = {};
  try { localStorage.removeItem(CFG); } catch (e) {}
  applyCfg();
};

async function hunt() {
  const btn = $('hunt'), el = $('status');
  const before = generated;
  btn.disabled = true; btn.classList.add('spin');
  el.className = 'status';

  try {
    const r = await fetch(
      `https://api.github.com/repos/${cfg.repo}/actions/workflows/pokewatch.yml/dispatches`,
      {method: 'POST',
       headers: {Authorization: 'Bearer ' + cfg.token,
                 Accept: 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28'},
       body: JSON.stringify({ref: 'main'})});

    if (r.status !== 204) {
      const msg = {401: 'Token ungültig', 403: 'Token darf das nicht',
                   404: 'Repo oder Workflow nicht gefunden'}[r.status]
                  || ('Fehler ' + r.status);
      el.classList.add('offline'); el.textContent = msg;
      btn.disabled = false; btn.classList.remove('spin');
      return;
    }

    // Der Lauf dauert ein paar Minuten, danach muss Pages neu ausliefern.
    const started = Date.now();
    const timer = setInterval(async () => {
      const min = Math.round((Date.now() - started) / 60000);
      el.textContent = `Suche läuft in der Cloud · ${min} Min`;
      try {
        const f = await fetch('feed.json?t=' + Date.now(), {cache: 'reload'});
        const d = await f.json();
        if (d.generated && d.generated !== before) {
          clearInterval(timer);
          items = d.items || []; generated = d.generated; builder = d.builder || '';
          btn.disabled = false; btn.classList.remove('spin');
          setStatus(); render();
        }
      } catch (e) {}
      if (Date.now() - started > 12 * 60000) {
        clearInterval(timer);
        btn.disabled = false; btn.classList.remove('spin');
        el.classList.add('warnstate');
        el.textContent = 'Dauert länger als erwartet — später neu laden';
      }
    }, 20000);
  } catch (e) {
    el.classList.add('offline'); el.textContent = 'Start fehlgeschlagen';
    btn.disabled = false; btn.classList.remove('spin');
  }
}

$('hunt').onclick = hunt;
applyCfg();

const hasSeed = readSeed();
if (hasSeed) { setStatus(); render(); }
if (STANDALONE) { $('reload').hidden = true; $('gear').hidden = true; }

$('reload').onclick = () => load(true);
load(false);

addEventListener('online',  () => { offline = false; load(true); });
addEventListener('offline', () => { offline = true; setStatus(); });

// Gelesen-Stand erst beim Verlassen setzen, damit die NEU-Marken
// nicht schon verschwinden, waehrend man noch liest.
addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    try { localStorage.setItem(LS, String(Date.now())); } catch (e) {}
  }
});

if ('serviceWorker' in navigator && !STANDALONE) {
  addEventListener('load', async () => {
    try {
      await navigator.serviceWorker.register('sw.js');
      const reg = await navigator.serviceWorker.ready;
      // Laesst den Browser die Daten holen, bevor du die App oeffnest.
      // Unter iOS gibt es das nicht - schlaegt still fehl, das ist so gewollt.
      if ('periodicSync' in reg) {
        const st = await navigator.permissions.query(
          {name: 'periodic-background-sync'});
        if (st.state === 'granted') {
          await reg.periodicSync.register('pokewatch-refresh',
                                          {minInterval: 12 * 3600 * 1000});
        }
      }
    } catch (e) {}
  });
}
</script>
</body>
</html>
"""

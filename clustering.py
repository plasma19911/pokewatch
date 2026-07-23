"""
Meldungen zum selben Vorfall zusammenfassen.

Das Problem: Fast jede Meldung enthaelt "einbruch", "sammelkarten", "pokemon",
"gestohlen". Wer danach gruppiert, wirft alles in einen Topf. Was einen Vorfall
wirklich identifiziert, sind die seltenen Woerter - Ortsnamen ("siegburg"),
Betraege ("15000"), Eigennamen. Deshalb wird jedes Wort danach gewichtet, wie
selten es im gesamten Bestand ist (IDF), und nur die seltenen entscheiden.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone

# Woerter ohne Aussagekraft fuer die Frage "welcher Vorfall ist das?"
STOP = {
    # Deutsch
    "und", "oder", "aber", "auch", "noch", "schon", "sehr", "kein", "keine",
    "mehr", "immer", "wieder", "jetzt", "hier", "dort", "weil", "dass", "wenn",
    "diese", "dieser", "dieses", "seine", "ihre", "durch", "ohne", "einer",
    "eine", "einen", "einem", "eines", "nach", "beim", "vom", "zum", "zur",
    "aus", "mit", "sich", "nicht", "ueber", "gegen", "haben", "hatte", "wird",
    "wurde", "wurden", "sind", "waren", "werden", "worden", "soll", "sollen",
    "kann", "koennen", "wert", "euro", "dollar", "jahre", "jahren", "alte",
    "alten", "unbekannte", "unbekannter", "taeter", "polizei", "news",
    # Englisch
    "the", "and", "for", "with", "from", "that", "this", "have", "has", "was",
    "were", "been", "will", "would", "could", "after", "before", "into", "out",
    "over", "under", "about", "more", "most", "than", "then", "when", "what",
    "who", "how", "why", "worth", "says", "said", "new", "police", "man", "men",
    "two", "three", "year", "years", "old",
    # Englische Kriminalitaets-Sprache: steht in fast jeder Meldung und
    # verbindet sonst voellig verschiedene Vorfaelle miteinander.
    "arrested", "arrest", "charged", "charges", "charge", "suspect", "suspects",
    "burglars", "burglar", "thieves", "thief", "guilty", "plead", "pleads",
    "court", "sheriff", "deputies", "deputy", "officer", "officers",
    "investigate", "investigates", "investigation", "caught", "camera",
    "video", "footage", "security", "alleged", "allegedly", "faces", "felony",
    "targeted", "target", "targets", "owner", "owners", "business", "shops",
    "stores", "county", "city", "department", "damages", "spree", "heist",
    "robbery", "stealing", "worth", "value", "rare", "collectible",
    "collectibles", "hobby", "game", "games", "gaming", "seconds", "second",
    "minute", "minutes", "overnight", "brazen", "high", "value", "teen",
    "teens", "local", "state", "west", "east", "north", "south", "northern",
    "southern", "eastern", "western", "latest", "reported", "report",
    # Mengenwoerter und Fuellwoerter. Sie sind selten genug, um faelschlich
    # als Eigenname zu gelten - "nearly" hat drei fremde Vorfaelle vereint.
    "nearly", "almost", "another", "several", "multiple", "following",
    "during", "before", "after", "recently", "reportedly", "apparently",
    "possibly", "roughly", "around", "thousands", "hundreds", "million",
    "millions", "billion", "worth", "estimated", "allegedly", "suspected",
    "including", "according", "amid", "ahead", "along", "among", "behind",
    "counterfeit", "fraudulent", "stolen", "missing", "damage", "damaged",
    "loss", "losses", "victim", "victims", "incident", "incidents", "case",
    "cases", "crime", "crimes", "criminal",
    # Themenwoerter - stehen in fast jeder Meldung, trennen also nichts
    "pokemon", "pokémon", "sammelkarten", "sammelkarte", "karten", "karte",
    "trading", "cards", "card", "tcg", "laden", "shop", "store", "geschaeft",
    "einbruch", "eingebrochen", "diebstahl", "gestohlen", "stiehlt", "stolen",
    "theft", "robbed", "robbery", "burglary", "break", "steal", "stealing",
}


def _norm(text: str) -> str:
    text = text.lower()
    text = (text.replace("ä", "ae").replace("ö", "oe")
                .replace("ü", "ue").replace("ß", "ss"))
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def tokens(item: dict) -> set:
    """
    Aussagekraeftige Woerter eines Eintrags.

    Zwei Feinheiten, die sich als noetig erwiesen haben:
    - Der Medienname wird ueber das Feld 'source' entfernt, nicht per Regex.
      Sonst gruppiert "General-Anzeiger Bonn" zwei Artikel, die nichts
      miteinander zu tun haben, nur weil dieselbe Zeitung sie schrieb.
    - Zusaetzlich zum ganzen Wort wird ein 6-Zeichen-Stamm aufgenommen, damit
      "Siegburg" und "Siegburger" zusammenfinden. Deutsch beugt zu viel.
    """
    title = item.get("title", "")
    source = item.get("source", "")
    if source:
        title = title.replace(source, " ")
        # Auch die Kurzform ohne Rechtsform-Zusaetze
        title = title.replace(source.split(" - ")[0], " ")
    title = re.sub(r"\s+-\s+[\w.\- ]{3,40}$", "", title)

    text = _norm(f"{title} {item.get('summary', '')[:200]}")
    if source:
        for part in _norm(source).replace("-", " ").split():
            text = text.replace(part, " ")

    out = set()
    for w in re.findall(r"[a-z]{4,}|\d[\d.,]{2,}", text):
        w = w.strip(".,")
        if w in STOP:
            continue
        if w[0].isdigit():
            out.add("num" + re.sub(r"[.,]", "", w)[:5])
            continue
        out.add(w)
        if len(w) > 6:
            out.add(w[:6] + "~")   # Wortstamm
    return out


def _days_apart(a: dict, b: dict) -> float:
    def parse(x):
        try:
            return datetime.fromisoformat(x["published"])
        except Exception:
            return datetime.now(timezone.utc)
    return abs((parse(a) - parse(b)).total_seconds()) / 86400


def cluster(items: list, threshold: float = 0.42, max_days: float = 14.0) -> list:
    """
    Gibt eine Liste von Gruppen zurueck, jede Gruppe eine Liste von Eintraegen.
    Die Reihenfolge der Eingabe bleibt erhalten (erste Gruppe = erster Eintrag).
    """
    n = len(items)
    if n < 2:
        return [[i] for i in items]

    toks = [tokens(it) for it in items]

    # IDF: wie selten ist ein Wort im Gesamtbestand?
    df: dict = {}
    for t in toks:
        for w in t:
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log(n / c) for w, c in df.items()}

    # Gewicht eines Eintrags = Summe der Wortgewichte.
    # Verglichen wird per Ueberlappung relativ zum kleineren Eintrag, nicht
    # per Kosinus: Schlagzeilen haben oft nur drei brauchbare Woerter, und
    # Kosinus bestraft die unterschiedliche Laenge dann viel zu hart.
    weight = [sum(idf[w] ** 2 for w in t) for t in toks]

    def matches(i, j) -> bool:
        gap = _days_apart(items[i], items[j])
        if gap > max_days:
            return False
        shared = toks[i] & toks[j]
        if not shared:
            return False

            # Ankerwort: hoechstens drei Mal im ganzen Bestand und lang genug
            # fuer einen Eigennamen - Ortsnamen, Firmennamen, Betraege.
        anchors = [w for w in shared if df[w] <= 3 and len(w) >= 6]
        if anchors:
            return True

        base = min(weight[i], weight[j])
        if base <= 0:
            return False
        sim = sum(idf[w] ** 2 for w in shared) / base

        # Ohne Ankerwort braucht es mehrere gemeinsame Begriffe, sonst
        # haengen "Geschaeft" und "Innenstadt" beliebige Vorfaelle aneinander.
        if len(shared) < 2:
            return False

        # Am selben Tag berichten mehrere Medien ueber denselben Vorfall.
        limit = threshold * (0.72 if gap <= 2 else 1.0)
        return sim >= limit

    # Vollstaendige Verknuepfung: ein Eintrag kommt nur in eine Gruppe, wenn
    # er zu *jedem* Mitglied passt. Mit Union-Find entstand sonst eine Kette -
    # A passt zu B, B zu C, und am Ende lagen 29 voellig verschiedene
    # Einbrueche in einem Topf, nur weil sich Nachbarn paarweise aehnelten.
    groups: list = []
    for i in range(n):
        for g in groups:
            if all(matches(i, j) for j in g):
                g.append(i)
                break
        else:
            groups.append([i])

    return [[items[k] for k in g] for g in groups]


def summarize(group: list) -> dict:
    """
    Aus einer Gruppe einen Eintrag machen. Fuehrend ist die aelteste Meldung -
    meist die urspruengliche Polizeimeldung, nicht die Zweitverwertung.
    Der laengste Titel wird bevorzugt, wenn es am selben Tag mehrere gibt.
    """
    ordered = sorted(group, key=lambda x: (x["published"], -len(x.get("title", ""))))
    lead = dict(ordered[0])

    # Fuehrend soll der aussagekraeftigste Titel sein, nicht der knappste
    best = max(group, key=lambda x: (x.get("score", 0), len(x.get("title", ""))))
    lead["title"] = best["title"]
    lead["url"] = best["url"]
    lead["source"] = best["source"]
    lead["summary"] = best.get("summary", "")

    others = [x for x in ordered if x["url"] != lead["url"]]
    lead["related"] = [{
        "title": x["title"], "url": x["url"],
        "source": x["source"], "published": x["published"],
    } for x in others]
    lead["report_count"] = len(group)
    # Neueste Meldung der Gruppe bestimmt, wie aktuell die Sache ist
    lead["published"] = max(x["published"] for x in group)
    lead["first_seen"] = min(x.get("first_seen") or x["published"] for x in group)
    return lead

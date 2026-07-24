#!/usr/bin/env python3
"""
PokeWatch - Newsfeed fuer Vorfaelle rund um Pokemon-/TCG-Laeden.

Sammelt Meldungen zu Einbruechen, Ueberfaellen, Betrug, Fake-Karten,
Insolvenzen und Schliessungen aus mehreren Quellen und baut daraus
eine HTML-Seite + einen RSS-Feed.

Start:  python3 pokewatch.py
Hilfe:  python3 pokewatch.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime, format_datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# KONFIGURATION - hier anpassen
# ----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "output"
STATE_FILE = BASE_DIR / "seen.json"

# Wie weit zurueck soll geschaut werden
LOOKBACK_DAYS = 45

# Wie viele Eintraege maximal im Feed landen
MAX_ITEMS = 150

# Allgemeine Sammelkarten-Nachrichten - neue Sets, Nachdrucke, Rekordpreise,
# Turniere. Bewusst AUS: das hat den Feed vollgeschwemmt, ohne zu helfen.
# PokeWatch meldet damit wieder ausschliesslich Vorfaelle. Auf True lassen
# sich die Nachrichten jederzeit wieder dazuschalten; die Stichwortlisten und
# die Kategorie "Szene / News" bleiben dafuer erhalten.
SZENE_NEWS = False

# --- Google News (kein API-Key noetig, beste Quelle fuer echte Einbrueche) ---
# Lokalzeitungen berichten ueber Einbrueche - das ist der zuverlaessigste Kanal.
GOOGLE_NEWS = {
    "de": [
        "Sammelkartenladen Einbruch",
        "Pokemon Laden Einbruch",
        "Pokemon Karten Diebstahl",
        "Kartenladen ueberfallen",
        "TCG Shop Einbruch",
        "Sammelkarten gestohlen Laden",
        "Pokemon Karten Raub",
        "Trading Card Shop Insolvenz",
        "Pokemon Karten Betrug Haendler",
        "gefaelschte Pokemon Karten",
        "PSA Submission verschwunden",
        "Sammelkarten Paket verloren Versand",
        "Grading Karten nicht angekommen",
        # ergaenzt: weitere Vorfallarten
        "Sammelkartenladen ueberfallen",
        "Spieleladen Einbruch",
        "Comicladen Einbruch",
        "Kartenladen Ladendiebstahl",
        "Pokemon Karten Hehlerei",
        "Sammelkarten Zoll beschlagnahmt",
        "gefaelschte Sammelkarten Zoll",
        "Pokemon Karten Kleinanzeigen Betrug",
        "Sammelkartenladen Insolvenz",
        "Kartenladen Brand Feuer",
        "Pokemon Karten Prozess Urteil",
        "Sammelkarten Millionen gestohlen",
        # Zoll, Razzia, Ermittlungen
        "Zoll beschlagnahmt Sammelkarten",
        "Zoll gefaelschte Pokemon Karten",
        "Razzia Sammelkarten Faelschungen",
        "Staatsanwaltschaft Sammelkarten Betrug",
        "Ermittlungen Kartenhaendler",
        # Bewertung und Einschweissen
        "PSA Karten verschwunden",
        "Grading Firma Klage Sammler",
        "gefaelschte PSA Slabs",
        "manipulierte Sammelkarten Zertifikat",
        # Formulierungen, wie Redaktionen sie tatsaechlich titeln - Google
        # News durchsucht damit die gesamte Presse, nicht nur die neuesten
        # Meldungen eines einzelnen Blattes.
        "Sammelkarten gestohlen Polizei",
        "Pokemon Karten Diebstahl Prozess",
        "Kartenladen ueberfallen Zeugen gesucht",
        "Sammelkarten Betrug Urteil",
        "Pokemon Karten Faelschungen Prozess",
        "Beute Sammelkarten Einbrecher",
        "Sammelkarten im Wert von gestohlen",
        "Laden fuer Sammelkarten aufgebrochen",
        "Pokemonkarten Betrueger verurteilt",
        "Sammelkartenhaendler betrogen",
        # Wie Lokalredaktionen und Wochenblaetter titeln. Ihre eigenen Feeds
        # sperren Rechenzentren aus (403), aber Google News indexiert sie -
        # ueber diesen Umweg kommt man doch an die Regionalpresse.
        "Unbekannte brechen in Laden ein Sammelkarten",
        "Diebe erbeuten Sammelkarten",
        "Sammelkarten aus Geschaeft gestohlen",
        "Einbrecher stehlen Pokemon Karten",
        "Beute Sammelkarten Zeugen gesucht",
        "Pokemonkarten aus Auto gestohlen",
        "Ladeninhaber bestohlen Sammelkarten",
        "Sammelkarten Raeuber fluechtig",
        "Zeugen gesucht Kartenladen",
        "Sammelkarten sichergestellt Polizei",
    ],
    "en": [
        "Pokemon card shop robbed",
        "trading card store burglary",
        "card shop break-in stolen cards",
        "TCG store theft",
        "card shop owner scam",
        "local game store closing cards",
        "counterfeit Pokemon cards seized",
        "PSA lost submission cards",
        "graded cards lost in transit",
        "card grading company lost my cards",
        # ergaenzt: weitere Vorfallarten
        "card shop armed robbery",
        "smash and grab trading cards",
        "pokemon cards stolen arrested",
        "trading card store lawsuit fraud",
        "counterfeit trading cards customs seized",
        "card shop fire arson",
        "card store closed suddenly customers money",
        "grading company lawsuit collectors",
        "pokemon card heist",
        "card shop employee stole",
        # Zoll, Razzia, Ermittlungen
        "customs seizes counterfeit trading cards",
        "customs seizure pokemon cards",
        "police raid counterfeit cards",
        "prosecutors trading card fraud",
        # Bewertung und Einschweissen
        "PSA lawsuit collectors cards",
        "grading company sued collectors",
        "counterfeit PSA slabs",
        "fake grading labels cards",
        "trimmed cards graded scandal",
        # Presse-Formulierungen
        "pokemon cards stolen police investigating",
        "card shop break-in suspects",
        "trading cards theft sentenced",
        "collector defrauded trading cards",
        "cards worth thousands stolen",
        "trading card fraud charges",
    ],
}

# --- YouTube ----------------------------------------------------------------
# Variante A (ohne API-Key): Kanaele beobachten. @handle oder UC... ID.
# Trage hier die Kanaele ein, die du sowieso schaust.
# Wichtig: Laeden melden eigene Vorfaelle zuerst auf dem eigenen Kanal.
# Handles per "python3 pokewatch.py --add-channel @name" ergaenzen.
YOUTUBE_CHANNELS = [
    "@PokeGeoDude",     # DE, Grading/Shop - meldete den UPS-Submissionsverlust
    "@PokemonKarten",   # DE
    "@PokeSammler",     # DE
    "@PokeRev",         # US
    "@RealBreakingNate",
]

# Variante B (mit API-Key): Volltextsuche ueber ganz YouTube.
# Key holen: console.cloud.google.com -> YouTube Data API v3 aktivieren.
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
YOUTUBE_QUERIES = [
    "card shop robbed",
    "pokemon store break in",
    "lgs burglary cards",
    "pokemon laden einbruch",
    "card shop scam exposed",
    "psa submission verschwunden",
    "psa lost my cards",
    "grading submission missing",
]

# --- Reddit (kein Key noetig, braucht aber ehrlichen User-Agent) -------------
REDDIT_SUBS = [
    "PokemonTCG",
    "pkmntcgcollections",
    "PokeInvesting",
    "pkmntcgtrades",
    "mtgfinance",
    "Pokemoncardcollectors",
    "PSAcard",
    "gradedcards",
]
REDDIT_QUERIES = ["robbed", "broke in", "stolen", "burglary", "scammed",
                  "shut down", "lost submission", "lost in transit", "never arrived",
                  # ergaenzt
                  "counterfeit", "fake cards", "resealed", "chargeback",
                  "package stolen", "arrested", "warning scammer",
                  "took my money", "closed without notice", "police report",
                  # Bewertung und Einschweissen
                  "fake slab", "reslabbed", "trimmed", "cert swap",
                  "psa lawsuit", "grading scandal"]

# --- Bluesky (kein Key noetig) ---------------------------------------------
BLUESKY_QUERIES = [
    "pokemon shop robbed",
    "card shop break in",
    "tcg store stolen",
    "pokemon laden einbruch",
    "psa lost submission",
    "grading company lost cards",
    # ergaenzt
    "pokemon cards stolen",
    "card store theft",
    "sammelkarten gestohlen",
    "kartenladen ueberfall",
    "counterfeit pokemon cards",
    "cgc lost cards",
    "fake psa slab",
    "customs seized cards",
    "trimmed card graded",
]

# --- Eigene RSS-Feeds (Szene-Blogs, Shop-News, Polizeimeldungen) -------------
# Beliebige Feed-URLs eintragen. Nichts voreingestellt, weil die gaengigen
# TCG-Blogs ihre Feeds regelmaessig umziehen - lieber selbst pruefen.
EXTRA_RSS = [
    # Zentrale Sammelstelle fuer Pressemitteilungen der deutschen Polizei.
    # Liefert bundesweit alles - Verkehrsunfaelle, Vermisste, Einbrueche.
    # Das macht nichts: der Stichwortfilter unten verlangt Laden-Bezug UND
    # Vorfall-Bezug, es kommt also nur durch, was mit Kartenlaeden zu tun
    # hat. Genau die Meldungen, die sonst nur in der Lokalzeitung stehen.
    # Das False ist wichtig: bei dieser breiten Quelle muss zusaetzlich ein
    # Laden-Bezug im Text stehen ("Sammelkarten", "Pokemon", "Kartenladen"),
    # sonst kaeme jeder Wohnungseinbruch Deutschlands mit durch.
    ("https://www.presseportal.de/rss/polizei.rss2", "streng"),

    # Dazu die Landesausgaben. Der bundesweite Feed haelt nur die 15
    # neuesten Meldungen vor - bei mehreren hundert Polizeimeldungen taeglich
    # ist der nach einer Viertelstunde durchgelaufen. Jede Landesausgabe
    # fuehrt eigene 15, zusammen also gut 240 statt 15. Kostet pro Lauf
    # 15 zusaetzliche Abrufe und faengt dafuer deutlich mehr ab.
    ("https://www.presseportal.de/rss/polizei/laender/1.rss2", "streng"),       # Baden-Württemberg
    ("https://www.presseportal.de/rss/polizei/laender/2.rss2", "streng"),       # Bayern
    ("https://www.presseportal.de/rss/polizei/laender/3.rss2", "streng"),       # Berlin/Brandenburg
    ("https://www.presseportal.de/rss/polizei/laender/4.rss2", "streng"),       # Bremen
    ("https://www.presseportal.de/rss/polizei/laender/5.rss2", "streng"),       # Hamburg
    ("https://www.presseportal.de/rss/polizei/laender/6.rss2", "streng"),       # Hessen
    ("https://www.presseportal.de/rss/polizei/laender/7.rss2", "streng"),       # Mecklenburg-Vorpommern
    ("https://www.presseportal.de/rss/polizei/laender/8.rss2", "streng"),       # Niedersachsen
    ("https://www.presseportal.de/rss/polizei/laender/9.rss2", "streng"),       # Nordrhein-Westfalen
    ("https://www.presseportal.de/rss/polizei/laender/10.rss2", "streng"),      # Rheinland-Pfalz
    ("https://www.presseportal.de/rss/polizei/laender/11.rss2", "streng"),      # Schleswig-Holstein
    ("https://www.presseportal.de/rss/polizei/laender/13.rss2", "streng"),      # Saarland
    ("https://www.presseportal.de/rss/polizei/laender/14.rss2", "streng"),      # Sachsen
    ("https://www.presseportal.de/rss/polizei/laender/15.rss2", "streng"),      # Thüringen
    ("https://www.presseportal.de/rss/polizei/laender/16.rss2", "streng"),      # Sachsen-Anhalt
    # (12 gibt es nicht - die Nummerierung hat eine Luecke.)

    # Weitere Moeglichkeiten - Adresse pruefen, bevor du sie aktivierst:
    # Einzelne Polizeidienststelle (Nummer steht in der Adresse der
    # Dienststellen-Seite auf presseportal.de/blaulicht/dienststellen):
    # ("https://www.presseportal.de/rss/dienststelle_11491.rss2", "streng"),
    #
    # Allgemeine Pokemon-Nachrichten - abgeschaltet, weil sie den Feed
    # verwaessert haben. Raute entfernen, falls du sie doch willst (dann auch
    # SZENE_NEWS oben auf True setzen, sonst kommt nichts davon durch):
    # ("https://www.nintendo-online.de/rss.xml?type=posts", "pokemon"),

    # Szene-Blogs und Shop-News, falls du welche verfolgst:
    # "https://beispiel-tcg-blog.de/feed",
    #
    # Geprueft und NICHT brauchbar (Stand Juli 2026):
    #   pokebeach.com/feed          -> antwortet Automaten mit 403
    #   pokemon.com/.../rss         -> antwortet Automaten mit 403
    #   bisafans.de, filb.de,
    #   pokemonexperte.de           -> bieten keinen Feed mehr an
    #   pokewiki.de (Atom)          -> liefert kein gueltiges XML
    #
    # Laeuft, falls du auch Videospiel-Nachrichten willst:
    #   ("https://pokemondb.net/news/feed", "pokemon"),
]

# --- Instagram (optional, kostenpflichtig) ----------------------------------
# Meta hat die oeffentliche Hashtag-Suche abgeschaltet. Es gibt keinen
# kostenlosen Weg, IG nach Stichworten zu durchsuchen. Zwei Optionen:
#   1. Konkrete Accounts beobachten (unten eintragen) - braucht Apify-Token.
#   2. Leer lassen und IG manuell verfolgen.
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
INSTAGRAM_ACCOUNTS = [
    # "pokemoncenter",
]

# --- Logos -------------------------------------------------------------------
# Neben jeder Meldung steht das Zeichen der Quelle. Bei Zeitungen wird das
# Favicon der Domain geladen; klappt das nicht, bleibt der Platz einfach leer.
# Das Logo eines betroffenen *Ladens* laesst sich nicht zuverlaessig
# automatisch finden - dafuer hier von Hand eintragen. Schluessel ist ein
# Stichwort, das im Titel vorkommt.
SHOP_LOGOS = {
    # "siegburg": "https://beispiel-laden.de/logo.png",
    # "pokegeodude": "https://…/logo.png",
}

# Mediennamen, deren Domain sich nicht aus dem Namen ableiten laesst
DOMAIN_FIXES = {
    "general-anzeiger bonn": "ga.de",
    "herald sun": "heraldsun.com.au",
    "wochenblatt reporter": "wochenblatt-reporter.de",
    "altkreisblitz": "altkreisblitz.de",
    "pz-news": "pz-news.de",
    "news.de": "news.de",
    "stern.de": "stern.de",
}

# --- Push-Benachrichtigung (optional) ---------------------------------------
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

USER_AGENT = "PokeWatch/1.0 (personal news monitor)"
TIMEOUT = 20

# ----------------------------------------------------------------------------
# STICHWORT-LOGIK
# ----------------------------------------------------------------------------

# Ein Treffer braucht: (Laden-Bezug) UND (Vorfall-Bezug).
# Bei quellen-eigenen Feeds (YT-Kanal, Subreddit) reicht der Vorfall-Bezug,
# weil der Kontext schon Pokemon/TCG ist.

# Fuer den Feed-Modus "pokemon" bewusst enger als SHOP_TERMS: dort sind auch
# Magic und Yu-Gi-Oh aufgefuehrt, weil Kartenladen-Meldungen sie erwaehnen.
# Bei einer allgemeinen Nachrichtenseite wuerde das Magic-Meldungen
# hereinlassen, die niemanden interessieren, der Pokemon verfolgt.
# Bewusst nur dieses eine Wort: "sammelkarten" steckt auch in
# "Sammelkartenspiel", und damit rutschen Magic- und Yu-Gi-Oh-Meldungen mit
# durch. Wer Pokemon meint, schreibt Pokemon.
POKEMON_TERMS = ["pokemon"]

SHOP_TERMS = [
    "pokemon", "pokémon", "sammelkarten", "trading card", "tcg", "kartenladen",
    "card shop", "card store", "game store", "lgs", "hobbyladen", "comicladen",
    "spieleladen", "yugioh", "yu-gi-oh", "magic the gathering", "booster",
    "sammelkartenladen", "kartenshop", "tradingcard",
    # Grading-Kontext zaehlt auch als Laden-Bezug
    "psa", "cgc", "beckett", "sgc", "grading", "submission", "graded", "slab",
    # "slab" hat vier Zeichen und braucht daher Wortgrenzen - die Mehrzahl
    # muss extra dastehen. Gleiches gilt fuer die deutschen Begriffe.
    "slabs", "einsendung", "gradingfirma", "bewertungsdienst",
]

INCIDENT_CATEGORIES = {
    "einbruch": {
        "label": "Einbruch / Raub",
        "terms": [
            "einbruch", "eingebrochen", "einbrecher", "ueberfall", "überfall",
            "ueberfallen", "überfallen", "raub", "ausgeraubt", "gepluendert",
            "geplündert", "diebstahl", "gestohlen", "entwendet", "beute",
            "robbed", "robbery", "burglary", "burglar", "break-in", "broke in",
            "broken into", "stolen", "theft", "looted", "smash and grab",
            "heist", "shoplifting", "held up at gunpoint",
            # Verwandte Delikte, die in Ueberschriften genauso auftauchen
            "hehlerei", "veruntreu", "ladendiebstahl",
            # Staemme statt Vollformen: "stehl" trifft stehlen/stehlt,
            # "gestohl" trifft gestohlen, "entwend" entwendet/entwenden.
            "stehl", "gestohl", "entwend", "erbeut",
            # "unterschlaegt" wird beim Entschaerfen der Umlaute zu
            # "unterschlaegt" - der Stamm muss also vor dem a enden,
            # sonst trifft er nur "Unterschlagung".
            "unterschla",
            "trickdiebstahl", "aufgebrochen", "eingedrungen", "beraubt",
            "erpressung", "schutzgeld", "raubueberfall", "raubüberfall",
            "embezzlement", "extortion", "stole from",
        ],
        # Achtung: ein Muster-Treffer gilt in classify() als starkes Signal
        # und ueberspringt die Pruefung auf Laden-Bezug. Deshalb steht das
        # Laden-Wort hier IM Muster - sonst faengt es jeden Wohnungseinbruch.
        "patterns": [
            # "Unbekannte brachen in der Nacht in ein Geschaeft ein":
            # das Verb steht auseinandergerissen, kein Stichwort trifft es.
            # Keine Wortgrenze am Ende: "Sammelkartengeschaeft" und
            # "Ladenlokal" sollen genauso treffen wie "Laden".
            r"\bbrach(en)?\b[^.!?]{0,60}\b(laden|geschaeft|geschäft|shop|"
            r"store|kartenladen|sammelkarten|pokemon)",
        ],
    },
    "betrug": {
        "label": "Betrug / Fakes",
        "terms": [
            # Maschen rund um Bewertung und Einschweissen: gefaelschte Slabs,
            # ausgetauschte Etiketten, beschnittene Karten. Eigene Begriffe,
            # weil "Betrug" allein sie nicht trifft.
            "gefaelschte slabs", "fake slab", "counterfeit slab",
            "etikett ausgetauscht", "label swap", "reslabbed", "reholder",
            "beschnitten", "getrimmt", "trimmed card", "trimming",
            "gefaelschtes zertifikat", "fake cert", "cert swap",
            "manipulierte karte", "tampered",
            # Online-Betrug: die haeufigste Masche ueberhaupt, und in
            # Ueberschriften steht selten schlicht "Betrug". Bewusst
            # zusammengesetzte Begriffe - "kleinanzeigen" allein wuerde
            # jeden harmlosen Verkaufsartikel einfangen.
            "kleinanzeigen betrug", "ebay betrug", "ebay kleinanzeigen betrug",
            "vinted betrug", "fakeshop", "fake shop", "fake-shop",
            "vorkasse", "nie geliefert", "nicht geliefert", "nie erhalten",
            "ware nicht erhalten", "geld weg", "phishing",
            "paypal freunde", "freundschaftszahlung", "rueckbuchung",
            "dreiecksbetrug", "vorschussbetrug", "identitaetsdiebstahl",
            "never shipped", "never sent", "did not ship", "non-delivery",
            "friends and family", "chargeback fraud", "fake listing",
            "fake seller", "ghosted after payment",
            "betrug", "betrog", "betrueger", "betrüger", "abzocke", "faelschung",
            "fälschung", "gefaelscht", "gefälscht", "faelschungen", "fake karten",
            "proxy cards", "scam", "scammed", "scammer", "fraud", "counterfeit",
            "fake cards", "resealed", "weighted packs", "search packs",
            "ripped off", "ponzi", "nicht geliefert", "vorkasse",
        ],
    },
    "pleite": {
        "label": "Schliessung / Insolvenz",
        "terms": [
            "insolvenz", "insolvent", "pleite", "schliesst", "schließt",
            "geschlossen", "aufgabe", "geschaeftsaufgabe", "geschäftsaufgabe",
            "raeumungsverkauf", "räumungsverkauf", "closing down", "shutting down",
            "went out of business", "bankruptcy", "liquidation", "closed for good",
            "final day", "shut down",
        ],
    },
    "behoerden": {
        "label": "Razzia / Rechtliches",
        "terms": [
            "razzia", "durchsuchung", "beschlagnahmt", "ermittlungen", "angeklagt",
            "verurteilt", "polizei", "staatsanwaltschaft", "zoll",
            # Gebeugte Formen: "klagt" trifft auch "verklagt" und "beklagt",
            # "ermittelt" faengt Meldungen, in denen "Ermittlungen" fehlt.
            "klagt", "klage", "ermittelt", "razzien", "sichergestellt",
            "raid", "seized", "arrested", "charged", "lawsuit", "sued",
            "investigation", "indicted", "customs",
        ],
    },
    # Der Fall PokeGeoDude: UPS verliert eine PSA-Submission. Kein Einbruch,
    # keine Presse - lief nur ueber den eigenen Kanal. Deshalb eigene Kategorie.
    "versand": {
        "label": "Versand / Grading",
        "terms": [
            "verschwunden", "verschollen", "hilferuf", "zeugenaufruf",
            # Der haeufigste Fall steht in der Ueberschrift meist so:
            # "Grading-Firma verliert Einsendung"
            "verliert", "verloren", "nicht zurueck", "einbehalten",
            "nie angekommen", "nicht angekommen", "spurlos", "unterschlagen",
            "falsch zugestellt", "versicherungsfall", "totalverlust",
            "lost submission", "missing submission", "lost package",
            "lost parcel", "never arrived", "never delivered",
            "lost in transit", "missing in transit", "misdelivered",
            "insurance claim",
        ],
        # Diese Muster zaehlen als starkes Signal, auch ohne weiteren Treffer.
        "patterns": [
            r"(paket|sendung|submission|lieferung|karten|package|shipment|einsendung)"
            r".{0,30}(verschwunden|verloren|verschollen|weg|fehlt|nicht angekommen"
            r"|lost|missing|gone|stuck)",
            r"(ups|dhl|gls|hermes|fedex|dpd|usps|post)"
            r".{0,30}(verliert|verloren|verschwunden|lost|loses|missing|beschaedigt)",
            r"(psa|cgc|beckett|sgc|grading).{0,70}(verschwunden|verloren|problem"
            r"|skandal|wartezeit|verzoeger|delay|backlog|lost|missing|scandal"
            r"|error|fehler|chaos)",
            r"wir brauchen (eure|euer|eure hilfe)",
            r"(bitte teilen|bitte um mithilfe|please share|please help)",
        ],
    },
    # Brand, Wasser, Vandalismus: kein Diebstahl, aber fuer einen Laden
    # genauso existenzbedrohend - und in den Meldungen taucht es haeufig auf.
    # Achtung bei kurzen Woertern: "brand" allein wuerde in "Brandenburg"
    # treffen, deshalb stehen hier nur eindeutige Zusammensetzungen.
    "schaden": {
        "label": "Brand / Schaden",
        "terms": [
            "brandstiftung", "brandanschlag", "grossbrand", "großbrand",
            "abgebrannt", "ausgebrannt", "feuer", "flammen", "loeschte",
            "wasserschaden", "ueberschwemmt", "überschwemmt",
            "vandalismus", "verwuestet", "verwüstet", "beschmiert",
            "zerstoert", "zerstört", "demoliert", "sachbeschaedigung",
            "sachbeschädigung", "scheibe eingeschlagen",
            "arson", "burned down", "fire destroyed", "vandalism",
            "vandalised", "vandalized", "water damage", "flooded",
        ],
        "patterns": [
            r"(brand|feuer|fire).{0,40}(laden|shop|store|geschaeft|geschäft)",
            r"(laden|shop|store).{0,40}(abgebrannt|ausgebrannt|burned)",
        ],
    },
    # Alles rund um Sammelkarten, was kein Vorfall ist: neue Sets, Termine,
    # Nachdrucke, Preise, Turniere. Steht bewusst als LETZTE Kategorie -
    # classify nimmt die erste, die passt, also gewinnt jeder Vorfall.
    # Abschaltbar ueber SZENE_NEWS ganz oben.
    "szene": {
        "label": "Szene / News",
        "terms": [
            # Neue Produkte und Termine
            "neues set", "neue erweiterung", "erweiterung", "erscheinungstermin",
            "veroeffentlichungstermin", "angekuendigt", "ankuendigung",
            "vorgestellt", "enthuellt", "erscheint am", "kommt im",
            "new set", "new expansion", "release date", "announced",
            "revealed", "unveiled", "leak", "leaked",
            # Verfuegbarkeit
            "nachdruck", "neuauflage", "wieder verfuegbar", "ausverkauft",
            "vorbestellung", "vorbestellbar", "kontingent",
            "reprint", "restock", "back in stock", "sold out", "preorder",
            "allocation", "scalper",
            # Preise und Markt
            "rekordpreis", "hoechstpreis", "versteigert", "auktion",
            "wertsteigerung", "preisentwicklung", "marktwert", "sammlerwert",
            "record price", "auction", "sells for", "price guide",
            "market value", "investment",
            # Spiel und Turniere
            "turnier", "meisterschaft", "weltmeisterschaft", "regionalturnier",
            "championship", "worlds", "regionals", "tournament", "banlist",
            "rotation", "format",
            # Bewertung
            "psa 10", "gem mint", "eingestuft", "graded",
        ],
        "patterns": [
            r"(pokemon|pok[eé]mon|tcg|sammelkarten|trading card)"
            r".{0,45}(neues? set|neue erweiterung|new set|expansion"
            r"|kollektion|collection|display|elite trainer)",
            r"(neues? set|new set|neue erweiterung|new expansion)"
            r".{0,45}(pokemon|pok[eé]mon|tcg|sammelkarten)",
            r"(pokemon|pok[eé]mon).{0,30}(karte|card).{0,40}"
            r"(rekord|record|versteigert|auktion|auction|verkauft fuer|sells for)",
        ],
    },
}

# Sehr starke Signale - reichen allein, ohne zweiten Treffer
STRONG_PATTERNS = [
    re.compile(r"(pokemon|pok[eé]mon|tcg|sammelkarten|card).{0,25}"
               r"(shop|store|laden|geschaeft|gesch[aä]ft).{0,40}"
               r"(einbruch|[uü]berfall|ausgeraubt|robbed|burglary|break[- ]?in)", re.I),
    re.compile(r"(einbruch|[uü]berfall|robbery|burglary).{0,40}"
               r"(pokemon|pok[eé]mon|sammelkarten|kartenladen|card shop)", re.I),
]

# Rauschfilter - Titel, die fast immer Fehlalarm sind
NOISE_PATTERNS = [
    re.compile(r"\b(pack opening|opening pack|unboxing|pull rate|top \d+ cards)\b", re.I),
    re.compile(r"\b(shiny hunt|nuzlocke|speedrun|randomizer|team rocket)\b", re.I),
    re.compile(r"\b(deck (profile|tech|list)|tier list|meta report)\b", re.I),
    re.compile(r"\b(mystery (pack|box)|i (risked|spent|bought)|challenge|giveaway)\b", re.I),
    # "burned down" und "fire destroyed" standen hier frueher mit drin -
    # sinnvoll, solange Brandmeldungen nichts zu suchen hatten. Seit es die
    # Kategorie "Brand / Schaden" gibt, waeren sie genau das, was wir wollen.
    re.compile(r"\b(restaurant|cafe opened|coffee shop opened)\b", re.I),
]


def normalize(text: str) -> str:
    """Kleinschreibung + Umlaute entschaerfen, damit Matching robust ist."""
    text = text.lower()
    text = (text.replace("ä", "ae").replace("ö", "oe")
                .replace("ü", "ue").replace("ß", "ss"))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text)


def term_hit(term: str, text: str) -> bool:
    """
    Kurze Kuerzel (psa, tcg, lgs) brauchen Wortgrenzen, sonst matcht 'psa'
    in beliebigen Woertern. Laengere Begriffe bleiben Teilstring-Suche,
    weil Deutsch zusammensetzt: 'sammelkarten' steckt in 'sammelkartenladen'.
    """
    t = normalize(term)
    if len(t) <= 4:
        return re.search(rf"\b{re.escape(t)}\b", text) is not None
    return t in text


# Kategorie-Muster einmalig kompilieren
_CAT_PATTERNS = {
    key: [re.compile(p, re.I) for p in cfg.get("patterns", [])]
    for key, cfg in INCIDENT_CATEGORIES.items()
}


def classify(title: str, summary: str = "", assume_context: bool = False):
    """
    Prueft, ob ein Eintrag relevant ist.
    assume_context=True bei Quellen, die schon thematisch gefiltert sind.
    Rueckgabe: (category_key, score, matched_terms) oder None.
    """
    raw = f"{title} {summary}"
    text = normalize(raw)

    for pat in NOISE_PATTERNS:
        if pat.search(raw):
            return None

    hits: list[str] = []
    category = None
    score = 0
    pattern_hit = False

    for key, cfg in INCIDENT_CATEGORIES.items():
        if key == "szene" and not SZENE_NEWS:
            continue
        found = [t for t in cfg["terms"] if term_hit(t, text)]
        matched_pat = [p for p in _CAT_PATTERNS[key] if p.search(text)]
        if not (found or matched_pat):
            continue
        hits.extend(found)
        if matched_pat:
            pattern_hit = True
            hits.append(f"muster:{key}")
        if category is None:
            category = key
        score += len(found) + 3 * len(matched_pat)

    if not category:
        return None

    shop_hits = [t for t in SHOP_TERMS if term_hit(t, text)]
    strong = pattern_hit or any(p.search(raw) for p in STRONG_PATTERNS)

    if strong:
        score += 5
    elif shop_hits:
        score += len(shop_hits)
    elif not assume_context:
        return None  # Vorfall ohne Laden-Bezug -> raus

    # Einbruch schlaegt andere Kategorien, wenn beides matcht
    if any(term_hit(t, text) for t in INCIDENT_CATEGORIES["einbruch"]["terms"]):
        category = "einbruch"

    return category, score, sorted(set(hits + shop_hits))[:8]


def source_domain(source: str, region: str = "int") -> str:
    """
    Domain eines Mediums erraten. Google News verbirgt die echte Adresse hinter
    einer Weiterleitung, die sich seit 2024 nicht mehr aufloesen laesst - also
    bleibt nur der Name. Trifft nicht immer; wenn nicht, faellt das Logo weg.
    """
    s = (source or "").strip()
    if not s:
        return ""
    if s.lower() in DOMAIN_FIXES:
        return DOMAIN_FIXES[s.lower()]
    # Schon eine Domain? ("meinestadt.de", "games.gg")
    if " " not in s and re.match(r"^[\w.\-]+\.[a-z]{2,}$", s, re.I):
        return s.lower()
    slug = re.sub(r"[^a-z0-9\-]", "", normalize(s))
    if not slug:
        return ""
    return slug + (".de" if region in ("de", "at", "ch") else ".com")


def shop_logo(title: str) -> str:
    """Von Hand hinterlegtes Ladenlogo, falls ein Stichwort passt."""
    t = normalize(title)
    for key, url in SHOP_LOGOS.items():
        if normalize(key) in t:
            return url
    return ""


# ----------------------------------------------------------------------------
# REGIONSERKENNUNG
# ----------------------------------------------------------------------------

# Reihenfolge zaehlt: TLD schlaegt Stadt, Stadt schlaegt Sprache.
AT_CITIES = ["wien", "graz", "linz", "salzburg", "innsbruck", "klagenfurt",
             "villach", "steiermark", "tirol", "vorarlberg", "burgenland",
             "oesterreich", "kaernten"]
CH_CITIES = ["zuerich", "bern", "basel", "genf", "lausanne", "winterthur",
             "luzern", "st. gallen", "lugano", "kanton", "schweiz", "aargau"]

# Nur ein Auszug - reicht, um deutsche Lokalmeldungen zu erkennen
DE_PLACES = [
    "berlin", "hamburg", "muenchen", "koeln", "frankfurt", "stuttgart",
    "duesseldorf", "leipzig", "dortmund", "essen", "bremen", "dresden",
    "hannover", "nuernberg", "duisburg", "bochum", "wuppertal", "bielefeld",
    "bonn", "muenster", "karlsruhe", "mannheim", "augsburg", "wiesbaden",
    "moenchengladbach", "gelsenkirchen", "aachen", "braunschweig", "kiel",
    "chemnitz", "halle", "magdeburg", "freiburg", "krefeld", "mainz",
    "luebeck", "erfurt", "rostock", "kassel", "hagen", "saarbruecken",
    "potsdam", "wuerzburg", "regensburg", "paderborn", "ingolstadt", "ulm",
    "bayern", "sachsen", "thueringen", "hessen", "niedersachsen", "saarland",
    "brandenburg", "westfalen", "schleswig-holstein", "rheinland-pfalz",
    "baden-wuerttemberg", "mecklenburg", "siegburg", "grossburgwedel",
    # "deutschland" fehlt hier bewusst: steckt in Mediennamen wie
    # "IGN Deutschland" und wuerde US-Meldungen als deutsch markieren.
]

# Funktionswoerter, die es im Englischen so nicht gibt. "die" waere
# zweideutig ("die hard"), steht deshalb bewusst nicht allein als Beleg.
DE_STOPWORDS = [
    " der ", " das ", " und ", " ist ", " wurde", " wurden ", " nach ",
    " bei ", " von ", " aus ", " mit ", " sich ", " nicht ", " einen ", " einem ",
    " eines ", " ueber ", " gegen ", " haben ", " wird ", " sind ", " zwei ",
    " wir ", " eure ", " euer ", " unsere ", " unser ", " auch ", " noch ",
    " schon ", " sehr ", " kein ", " keine ", " mehr ", " immer ", " wieder ",
    " jetzt ", " hier ", " weil ", " aber ", " oder ", " wenn ", " dass ",
    " diese ", " dieser ", " seine ", " ihre ", " durch ", " ohne ", " einer ",
]

# Kanaele haben eine feste Herkunft - verlaesslicher als jede Textanalyse.
CHANNEL_REGIONS = {
    "@pokegeodude": "de",
    "@pokemonkarten": "de",
    "@pokesammler": "de",
}

# Deutschsprachige Seiten berichten auch ueber Faelle in den USA. Diese
# Marker verhindern, dass so ein Artikel als deutscher Vorfall zaehlt.
FOREIGN_MARKERS = [
    "florida", "texas", "kalifornien", "california", "kanada", "canada",
    "japan", "tokio", "tokyo", "australien", "melbourne", "sydney",
    "grossbritannien", "england", "london", "usa", "us-bundesstaat",
    "amerikaner", "us-amerikan", "arizona", "ohio", "michigan", "calgary",
    "new york", "chicago", "los angeles", "houston", "seattle", "vereinigte staaten",
]

REGION_LABEL = {"de": "Deutschland", "at": "Oesterreich",
                "ch": "Schweiz", "int": "International"}


def detect_region(title: str, summary: str, url: str, hint: str = "") -> str:
    """Grobe Herkunftsbestimmung. hint kommt aus dem Suchgebietsschema."""
    # Google News haengt " - Medienname" an. Der Name sagt nichts darueber,
    # wo der Vorfall passiert ist, und stoert die Ortserkennung.
    clean_title = re.sub(r"\s+-\s+[^-]{3,40}$", "", title)
    text = normalize(f" {clean_title} {summary} ")
    host = normalize(urllib.parse.urlparse(url).netloc)

    # 1. Konkrete Ortsnamen schlagen alles - sie sagen, wo es passiert ist
    if any(c in text for c in AT_CITIES):
        return "at"
    if any(c in text for c in CH_CITIES):
        return "ch"
    if any(c in text for c in DE_PLACES):
        return "de"

    # 2. Auslandsbezug: deutsche Seite, auslaendischer Fall
    if any(m in text for m in FOREIGN_MARKERS):
        return "int"

    # 3. Laenderdomain
    for tld, region in ((".at", "at"), (".ch", "ch"), (".de", "de")):
        if host.endswith(tld) or f"{tld}/" in host:
            return region

    # 4. Sprache - deutscher Text ohne AT/CH-Signal gilt als DE.
    #    Umlaute und ss zaehlen als zusaetzlicher Beleg.
    evidence = sum(1 for w in DE_STOPWORDS if w in text)
    if re.search(r"[aeiouAEIOU]\u0308|[äöüÄÖÜß]", f"{title} {summary}"):
        evidence += 2
    if evidence >= 2:
        return "de"

    return hint or "int"


# ----------------------------------------------------------------------------
# DATENMODELL
# ----------------------------------------------------------------------------

@dataclass
class Item:
    title: str
    url: str
    source: str
    platform: str
    published: str          # ISO 8601
    summary: str = ""
    author: str = ""
    category: str = "einbruch"
    region: str = "int"
    image: str = ""      # Vorschaubild (YouTube)
    domain: str = ""     # fuer das Favicon der Quelle
    logo: str = ""       # von Hand hinterlegtes Ladenlogo
    score: int = 0
    matched: list = field(default_factory=list)
    first_seen: str = ""
    related: list = field(default_factory=list)   # weitere Meldungen dazu
    report_count: int = 1

    @property
    def key(self) -> str:
        base = normalize(self.title)[:120] or self.url
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def dt(self) -> datetime:
        """Erscheinungsdatum als Zeitpunkt - immer mit Zeitzone.

        Quellen liefern gemischt: mal mit Zeitzone, mal ohne. Python
        weigert sich, beides zu vergleichen ("can't compare offset-naive
        and offset-aware datetimes") und das Sortieren waere mit einem
        Fehler abgebrochen. Deshalb wird alles auf UTC gezogen."""
        try:
            d = datetime.fromisoformat(self.published)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)


# ----------------------------------------------------------------------------
# HTTP-HELFER
# ----------------------------------------------------------------------------

def http_get(url: str, headers: dict | None = None) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        print(f"    ! HTTP {e.code} -> {url[:70]}", file=sys.stderr)
    except Exception as e:
        print(f"    ! {type(e).__name__}: {e} -> {url[:70]}", file=sys.stderr)
    return None


def http_json(url: str, headers: dict | None = None):
    raw = http_get(url, headers)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    for parser in (parsedate_to_datetime,
                   lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            dt = parser(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return datetime.now(timezone.utc)


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ----------------------------------------------------------------------------
# QUELLEN
# ----------------------------------------------------------------------------

def fetch_google_news(cutoff: datetime) -> list[Item]:
    items: list[Item] = []
    locales = {"de": ("de", "DE", "DE:de"), "en": ("en-US", "US", "US:en")}

    for lang, queries in GOOGLE_NEWS.items():
        hl, gl, ceid = locales[lang]
        for q in queries:
            url = ("https://news.google.com/rss/search?q="
                   + urllib.parse.quote(f"{q} when:{LOOKBACK_DAYS}d")
                   + f"&hl={hl}&gl={gl}&ceid={ceid}")
            raw = http_get(url)
            if not raw:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue

            for entry in root.iterfind(".//item"):
                title = (entry.findtext("title") or "").strip()
                link = (entry.findtext("link") or "").strip()
                if not title or not link:
                    continue
                pub = parse_date(entry.findtext("pubDate"))
                if pub < cutoff:
                    continue
                summary = strip_html(entry.findtext("description") or "")
                outlet = entry.findtext("source") or "Google News"

                verdict = classify(title, summary)
                if not verdict:
                    continue
                cat, score, matched = verdict
                items.append(Item(
                    title=title, url=link, source=outlet, platform="news",
                    published=pub.isoformat(), summary=summary[:300],
                    category=cat, score=score, matched=matched,
                    region=detect_region(title, summary, link,
                                         hint="de" if lang == "de" else "int"),
                ))
            time.sleep(0.4)
    return items


def resolve_channel_id(handle: str) -> str | None:
    """@handle -> UC... Kanal-ID (einmalig, wird gecacht)."""
    if handle.startswith("UC"):
        return handle
    cache = BASE_DIR / "channels.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if handle in known:
        return known[handle]

    raw = http_get(f"https://www.youtube.com/{handle}",
                   {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    if not raw:
        return None
    m = re.search(rb'"externalId":"(UC[\w-]{20,})"', raw)
    if not m:
        return None
    cid = m.group(1).decode()
    known[handle] = cid
    cache.write_text(json.dumps(known, indent=2))
    return cid


def fetch_youtube_channels(cutoff: datetime) -> list[Item]:
    """Kanal-RSS - braucht keinen API-Key."""
    items: list[Item] = []
    ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}

    extra_file = BASE_DIR / "channels_extra.json"
    extra = json.loads(extra_file.read_text()) if extra_file.exists() else []

    for handle in list(dict.fromkeys(YOUTUBE_CHANNELS + extra)):
        cid = resolve_channel_id(handle)
        if not cid:
            print(f"    ! Kanal nicht aufloesbar: {handle}", file=sys.stderr)
            continue
        raw = http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue

        channel = root.findtext("a:title", default=handle, namespaces=ns)
        for entry in root.iterfind("a:entry", ns):
            title = entry.findtext("a:title", default="", namespaces=ns)
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            pub = parse_date(entry.findtext("a:published", namespaces=ns))
            if pub < cutoff or not link:
                continue
            desc = entry.findtext(".//m:description", default="", namespaces=ns) or ""
            thumb_el = entry.find(".//m:thumbnail", ns)
            thumb = thumb_el.get("url", "") if thumb_el is not None else ""

            verdict = classify(title, desc[:400], assume_context=True)
            if not verdict:
                continue
            cat, score, matched = verdict
            items.append(Item(
                title=title, url=link, source=channel, platform="youtube",
                published=pub.isoformat(), summary=desc[:300], author=channel,
                category=cat, score=score, matched=matched,
                region=CHANNEL_REGIONS.get(handle.lower(), "int"),
                image=thumb,
            ))
        time.sleep(0.3)
    return items


def fetch_youtube_search(cutoff: datetime) -> list[Item]:
    """Volltextsuche - braucht YOUTUBE_API_KEY."""
    if not YOUTUBE_API_KEY:
        return []
    items: list[Item] = []
    after = cutoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for q in YOUTUBE_QUERIES:
        params = urllib.parse.urlencode({
            "part": "snippet", "q": q, "type": "video", "order": "date",
            "publishedAfter": after, "maxResults": 25,
            "relevanceLanguage": "de", "key": YOUTUBE_API_KEY,
        })
        data = http_json(f"https://www.googleapis.com/youtube/v3/search?{params}")
        if not data or "items" not in data:
            continue
        for entry in data["items"]:
            sn = entry.get("snippet", {})
            vid = entry.get("id", {}).get("videoId")
            if not vid:
                continue
            title = strip_html(sn.get("title", ""))
            desc = strip_html(sn.get("description", ""))
            verdict = classify(title, desc)
            if not verdict:
                continue
            cat, score, matched = verdict
            items.append(Item(
                title=title, url=f"https://www.youtube.com/watch?v={vid}",
                source=sn.get("channelTitle", "YouTube"), platform="youtube",
                published=parse_date(sn.get("publishedAt")).isoformat(),
                summary=desc[:300], author=sn.get("channelTitle", ""),
                category=cat, score=score, matched=matched,
            ))
        time.sleep(0.3)
    return items


def fetch_reddit(cutoff: datetime) -> list[Item]:
    items: list[Item] = []
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    for sub in REDDIT_SUBS:
        for q in REDDIT_QUERIES:
            params = urllib.parse.urlencode({
                "q": q, "restrict_sr": 1, "sort": "new", "t": "month", "limit": 25,
            })
            data = http_json(f"https://www.reddit.com/r/{sub}/search.json?{params}", headers)
            if not data:
                continue
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                title = d.get("title", "")
                created = datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc)
                if created < cutoff or not title:
                    continue
                body = strip_html(d.get("selftext", ""))[:400]
                verdict = classify(title, body, assume_context=True)
                if not verdict:
                    continue
                cat, score, matched = verdict
                items.append(Item(
                    title=title,
                    url="https://www.reddit.com" + d.get("permalink", ""),
                    source=f"r/{sub}", platform="reddit",
                    published=created.isoformat(), summary=body[:300],
                    author=d.get("author", ""),
                    category=cat, score=score + int(d.get("score", 0) // 100),
                    matched=matched,
                ))
            time.sleep(1.2)  # Reddit ist empfindlich
    return items


def fetch_bluesky(cutoff: datetime) -> list[Item]:
    items: list[Item] = []
    hosts = ["https://api.bsky.app", "https://public.api.bsky.app"]

    for q in BLUESKY_QUERIES:
        data = None
        for host in hosts:
            url = (f"{host}/xrpc/app.bsky.feed.searchPosts?"
                   + urllib.parse.urlencode({"q": q, "limit": 40, "sort": "latest"}))
            data = http_json(url)
            if data and data.get("posts"):
                break
        if not data:
            continue

        for post in data.get("posts", []):
            rec = post.get("record", {})
            text = (rec.get("text") or "").strip()
            created = parse_date(rec.get("createdAt"))
            if created < cutoff or not text:
                continue
            verdict = classify(text)
            if not verdict:
                continue
            cat, score, matched = verdict
            author = post.get("author", {})
            handle = author.get("handle", "")
            rkey = post.get("uri", "").rsplit("/", 1)[-1]
            items.append(Item(
                title=text[:180], url=f"https://bsky.app/profile/{handle}/post/{rkey}",
                source=f"@{handle}", platform="bluesky",
                published=created.isoformat(), summary=text[:300],
                author=author.get("displayName") or handle,
                category=cat, score=score, matched=matched,
            ))
        time.sleep(0.5)
    return items


def fetch_instagram(cutoff: datetime) -> list[Item]:
    """
    Instagram hat keine oeffentliche Suche mehr. Laeuft nur mit Apify-Token
    und nur fuer konkret genannte Accounts.
    """
    if not (APIFY_TOKEN and INSTAGRAM_ACCOUNTS):
        return []
    items: list[Item] = []
    url = ("https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"
           f"?token={APIFY_TOKEN}")
    payload = json.dumps({
        "directUrls": [f"https://www.instagram.com/{a}/" for a in INSTAGRAM_ACCOUNTS],
        "resultsType": "posts", "resultsLimit": 12,
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            posts = json.loads(resp.read())
    except Exception as e:
        print(f"    ! Instagram/Apify: {e}", file=sys.stderr)
        return []

    for post in posts:
        caption = (post.get("caption") or "").strip()
        created = parse_date(post.get("timestamp"))
        if created < cutoff or not caption:
            continue
        verdict = classify(caption, assume_context=True)
        if not verdict:
            continue
        cat, score, matched = verdict
        owner = post.get("ownerUsername", "instagram")
        items.append(Item(
            title=caption[:180], url=post.get("url", ""),
            source=f"@{owner}", platform="instagram",
            published=created.isoformat(), summary=caption[:300], author=owner,
            category=cat, score=score, matched=matched,
        ))
    return items


MEDIA_NS = "{http://search.yahoo.com/mrss/}"


def bild_aus_eintrag(entry, basis_url: str = "") -> str:
    """Sucht in einem RSS/Atom-Eintrag nach einem Vorschaubild.

    Die Quellen liefern das auf drei verschiedene Arten: als <enclosure>,
    als media:thumbnail bzw. media:content (der Media-RSS-Standard) oder
    einfach als <img> mitten im Beschreibungstext. Presseportal etwa haengt
    Fahndungsfotos als enclosure an."""
    # 1. Media RSS
    for pfad in (f"{MEDIA_NS}thumbnail", f"{MEDIA_NS}content"):
        for knoten in entry.iterfind(pfad):
            url = knoten.get("url", "")
            if url:
                return url

    # 2. enclosure - nur Bilder, keine PDFs oder Tondateien
    for knoten in entry.iterfind("enclosure"):
        typ = (knoten.get("type") or "").lower()
        url = knoten.get("url", "")
        if url and (typ.startswith("image/") or
                    url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp"))):
            return url

    # 3. Erstes <img> in der Beschreibung
    for feldname in ("description", "content:encoded",
                     "{http://purl.org/rss/1.0/modules/content/}encoded",
                     "{http://www.w3.org/2005/Atom}summary",
                     "{http://www.w3.org/2005/Atom}content"):
        text = entry.findtext(feldname) or ""
        treffer = re.search(r'<img[^>]+src=["\']([^"\']+)', text, re.I)
        if treffer:
            gefunden = treffer.group(1)
            if gefunden.startswith("//"):
                return "https:" + gefunden
            if gefunden.startswith("/") and basis_url:
                teile = urllib.parse.urlparse(basis_url)
                return f"{teile.scheme}://{teile.netloc}{gefunden}"
            if gefunden.startswith("http"):
                return gefunden
    return ""


def fetch_extra_rss(cutoff: datetime) -> list[Item]:
    """Beliebige Feeds aus EXTRA_RSS - Szene-Blogs, Shop-News, Polizeimeldungen."""
    items: list[Item] = []
    for eintrag in EXTRA_RSS:
        # Ein Eintrag ist entweder nur eine Adresse oder (Adresse, Modus):
        #
        #   "thema"    Feed ist schon thematisch passend (TCG-Blog). Es muss
        #              nur ein Thema treffen - Vorfall oder Szene-Nachricht.
        #   "streng"   Breite Quelle wie Polizeimeldungen. Zusaetzlich muss
        #              ein Sammelkarten-Bezug im Text stehen, sonst kaeme
        #              jeder Wohnungseinbruch Deutschlands mit durch.
        #   "pokemon"  Allgemeine Seite, von der nur die Pokemon-Meldungen
        #              interessieren. Alles mit Pokemon-Bezug kommt durch,
        #              auch ohne Vorfall oder Szene-Stichwort, und landet
        #              unter "Szene / News".
        if isinstance(eintrag, (tuple, list)):
            url, modus = eintrag[0], eintrag[1]
        else:
            url, modus = eintrag, "thema"
        if modus is True:
            modus = "thema"
        elif modus is False:
            modus = "streng"
        raw = http_get(url, {"User-Agent":
                             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            print(f"    ! Kein gueltiges XML: {url[:60]}", file=sys.stderr)
            continue

        feed_name = (root.findtext(".//channel/title")
                     or root.findtext("{http://www.w3.org/2005/Atom}title")
                     or urllib.parse.urlparse(url).netloc)

        # RSS <item> und Atom <entry> gleichermassen
        entries = list(root.iterfind(".//item")) + list(
            root.iterfind(".//{http://www.w3.org/2005/Atom}entry"))

        for entry in entries:
            def field(name: str) -> str:
                return (entry.findtext(name)
                        or entry.findtext(f"{{http://www.w3.org/2005/Atom}}{name}")
                        or "")

            title = field("title").strip()
            link = field("link").strip()
            if not link:
                le = entry.find("{http://www.w3.org/2005/Atom}link")
                link = le.get("href", "") if le is not None else ""
            if not title or not link:
                continue

            pub = parse_date(field("pubDate") or field("published") or field("updated"))
            if pub < cutoff:
                continue
            summary = strip_html(field("description") or field("summary"))

            if modus == "pokemon":
                text = normalize(f"{title} {summary}")
                treffer = [t for t in POKEMON_TERMS if term_hit(t, text)]
                if not treffer:
                    continue
                if any(pat.search(f"{title} {summary}") for pat in NOISE_PATTERNS):
                    continue
                verdict = classify(title, summary, assume_context=True)
                # Kein Thema erkannt? Dann ist es eine normale Nachricht.
                cat, score, matched = verdict or ("szene", 1, treffer[:5])
            else:
                verdict = classify(title, summary, assume_context=(modus == "thema"))
                if not verdict:
                    continue
                cat, score, matched = verdict
            items.append(Item(
                image=bild_aus_eintrag(entry, url),
                title=title, url=link, source=feed_name[:50], platform="rss",
                published=pub.isoformat(), summary=summary[:300],
                category=cat, score=score, matched=matched,
            ))
        time.sleep(0.3)
    return items


SOURCES = [
    ("Google News", fetch_google_news),
    ("YouTube (Kanaele)", fetch_youtube_channels),
    ("YouTube (Suche)", fetch_youtube_search),
    ("Reddit", fetch_reddit),
    ("Bluesky", fetch_bluesky),
    ("RSS", fetch_extra_rss),
    ("Instagram", fetch_instagram),
]


# ----------------------------------------------------------------------------
# ZUSTAND
# ----------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"items": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False))


def tag_regions(items: list[Item]) -> list[Item]:
    """Herkunft, Domain und Logo nachtraeglich bestimmen - an einer Stelle."""
    for it in items:
        if it.region == "int":
            it.region = detect_region(it.title, it.summary, it.url)
        if not it.domain:
            it.domain = source_domain(it.source, it.region)
        if not it.logo:
            it.logo = shop_logo(f"{it.title} {it.source}")
    return items


def dedupe(items: list[Item]) -> list[Item]:
    best: dict[str, Item] = {}
    seen_urls: set[str] = set()
    for it in sorted(items, key=lambda x: -x.score):
        clean_url = it.url.split("?")[0].rstrip("/")
        if clean_url in seen_urls:
            continue
        if it.key in best:
            continue
        best[it.key] = it
        seen_urls.add(clean_url)
    return list(best.values())


# ----------------------------------------------------------------------------
# AUSGABE
# ----------------------------------------------------------------------------

CAT_META = {
    "einbruch":  ("Einbruch / Raub",        "alert"),
    "betrug":    ("Betrug / Fakes",         "warn"),
    "pleite":    ("Schliessung / Insolvenz", "cool"),
    "behoerden": ("Razzia / Rechtliches",   "legal"),
    "versand":   ("Versand / Grading",      "ship"),
    "schaden":   ("Brand / Schaden",        "alert"),
    "szene":     ("Szene / News",           "cool"),
}

# Grobe Zweiteilung fuer die oberste Filterzeile: Vorfaelle sind das, wofuer
# PokeWatch urspruenglich gebaut wurde; News ist alles andere rund um
# Sammelkarten und Pokemon.
CAT_KIND = {
    "einbruch": "vorfall", "betrug": "vorfall", "pleite": "vorfall",
    "behoerden": "vorfall", "versand": "vorfall", "schaden": "vorfall",
    "szene": "news",
}
KIND_LABEL = {"vorfall": "Vorfaelle", "news": "News"}

PLATFORM_LABEL = {
    "news": "Presse", "youtube": "YouTube", "reddit": "Reddit",
    "bluesky": "Bluesky", "instagram": "Instagram", "rss": "RSS",
}


def relative_age(dt: datetime) -> str:
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"vor {int(delta.total_seconds() // 60)} Min"
    if hours < 24:
        return f"vor {int(hours)} Std"
    days = int(hours // 24)
    return "gestern" if days == 1 else f"vor {days} Tagen"


def render_html(items: list[Item], new_keys: set[str]) -> str:
    now = datetime.now(timezone.utc).astimezone()
    counts = {k: sum(1 for i in items if i.category == k) for k in CAT_META}

    stats = "".join(
        f'<div class="stat" data-tone="{CAT_META[k][1]}">'
        f'<span class="stat-n">{counts[k]}</span>'
        f'<span class="stat-l">{html.escape(CAT_META[k][0])}</span></div>'
        for k in CAT_META
    )

    rows = []
    for it in items:
        label, tone = CAT_META.get(it.category, ("Sonstiges", "cool"))
        is_new = it.key in new_keys
        tags = "".join(f'<span class="tag">{html.escape(t)}</span>'
                       for t in it.matched[:5])
        # Vorschaubild, falls die Quelle eines mitliefert - bei Polizei-
        # meldungen sind das oft Fahndungsfotos, bei YouTube das Videobild.
        # Laesst sich das Bild nicht laden, raeumt es sich selbst weg.
        bild = ""
        if it.image:
            bild = (f'<a class="shot" href="{html.escape(it.url)}" target="_blank" '
                    f'rel="noopener" aria-hidden="true" tabindex="-1">'
                    f'<img src="{html.escape(it.image)}" alt="" loading="lazy" '
                    f'referrerpolicy="no-referrer" '
                    f'onerror="this.closest(\'.shot\').remove()"></a>')
        rows.append(f"""
    <article class="entry{' is-new' if is_new else ''}" data-tone="{tone}"
             data-cat="{it.category}" data-region="{it.region}"
             data-art="{CAT_KIND.get(it.category, 'news')}"
             data-platform="{it.platform}">
      <div class="rail"></div>
      <div class="body">
        <div class="meta">
          <span class="cat">{html.escape(label)}</span>
          <span class="dot"></span>
          <span class="plat">{PLATFORM_LABEL.get(it.platform, it.platform)}</span>
          <span class="dot"></span>
          <span class="src">{html.escape(it.source[:48])}</span>
          <span class="reg reg-{it.region}">{REGION_LABEL.get(it.region, "")}</span>
          <span class="age">{relative_age(it.dt)}</span>
        </div>
        <h2><a href="{html.escape(it.url)}" target="_blank" rel="noopener">
          {html.escape(it.title[:200])}</a></h2>
        <p class="sum">{html.escape(it.summary[:220])}</p>
        <div class="tags">{tags}</div>
      </div>{bild}
    </article>""")

    body = "\n".join(rows) if rows else (
        '<div class="empty"><p>Nichts gefunden.</p>'
        '<p class="empty-hint">Kein Fund heisst hier: keine Meldung im Zeitfenster, '
        'die Sammelkarten-Bezug und ein Thema gleichzeitig trifft. '
        'Fuer mehr Reichweite LOOKBACK_DAYS erhoehen oder Suchbegriffe ergaenzen.</p></div>')

    # Abgeschaltete Kategorien gar nicht erst als Knopf anbieten - ein
    # Filter, der garantiert nichts findet, verwirrt nur.
    filters = "".join(
        f'<button class="fbtn" data-filter="{k}">{html.escape(v[0])}</button>'
        for k, v in CAT_META.items()
        if k != "szene" or SZENE_NEWS)

    kcounts = {}
    for it in items:
        art = CAT_KIND.get(it.category, "news")
        kcounts[art] = kcounts.get(art, 0) + 1
    # Die Zeile lohnt nur, wenn tatsaechlich beides vorkommt. Laeuft
    # PokeWatch als reiner Vorfall-Melder (SZENE_NEWS = False), waere sie
    # eine Auswahl ohne Wahl.
    vorhanden = [k for k in ("vorfall", "news") if kcounts.get(k)]
    if len(vorhanden) < 2:
        kindrow = ""
    else:
        kindrow = ('<div class="frow"><span class="flabel">Art</span>'
                   '<button class="abtn on" data-art="all">Alles</button>'
                   + "".join(
                       f'<button class="abtn" data-art="{k}">{KIND_LABEL[k]}'
                       f'<span class="n">{kcounts[k]}</span></button>'
                       for k in vorhanden)
                   + '</div>')

    rcounts = {}
    for it in items:
        rcounts[it.region] = rcounts.get(it.region, 0) + 1
    order = ["de", "at", "ch", "int"]

    # Deutschland ist immer der Startpunkt - auch wenn gerade nichts drin
    # steht. Frueher wich die Ansicht in dem Fall auf "Alle" aus; das
    # verwirrt aber mehr, als es hilft, weil man dann je nach Tageslage mal
    # deutsche und mal internationale Meldungen vor sich hat.
    start_region = "de"

    # Alle Herkuenfte immer anzeigen, auch mit Null - sonst verschwindet der
    # Knopf, sobald es nichts gibt, und man kann nicht zurueckwechseln.
    regions = (f'<button class="rbtn" data-region="all">Alle'
               f'<span class="n">{len(items)}</span></button>')
    regions += "".join(
        f'<button class="rbtn{" on" if r == start_region else ""}" data-region="{r}">'
        f'{REGION_LABEL[r]}<span class="n">{rcounts.get(r, 0)}</span></button>'
        for r in order)

    return (HTML_TEMPLATE
            .replace("%%STATS%%", stats)
            .replace("%%FILTERS%%", filters)
            .replace("%%KINDROW%%", kindrow)
            .replace("%%REGIONS%%", regions)
            .replace("%%REGION_START%%", start_region)
            .replace("%%ENTRIES%%", body)
            .replace("%%TOTAL%%", str(len(items)))
            .replace("%%NEW%%", str(len(new_keys)))
            .replace("%%UPDATED%%", now.strftime("%d.%m.%Y, %H:%M"))
            .replace("%%WINDOW%%", str(LOOKBACK_DAYS)))


def render_rss(items: list[Item]) -> str:
    entries = []
    for it in items[:60]:
        label = CAT_META.get(it.category, ("Vorfall", ""))[0]
        entries.append(f"""  <item>
   <title>[{html.escape(label)}] {html.escape(it.title[:180])}</title>
   <link>{html.escape(it.url)}</link>
   <guid isPermaLink="false">pokewatch-{it.key}</guid>
   <pubDate>{format_datetime(it.dt)}</pubDate>
   <source>{html.escape(it.source[:60])}</source>
   <description>{html.escape(it.summary[:400])}</description>
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
 <channel>
  <title>PokeWatch - Vorfaelle bei Pokemon-/TCG-Laeden</title>
  <link>https://example.local/pokewatch</link>
  <description>Einbrueche, Betrug, Razzien und Schliessungen im Kartenhandel.</description>
  <language>de</language>
  <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{chr(10).join(entries)}
 </channel>
</rss>"""


def push_notifications(new_items: list[Item]) -> None:
    if not new_items:
        return

    if DISCORD_WEBHOOK:
        lines = [f"**{CAT_META.get(i.category, ('Vorfall',''))[0]}** - "
                 f"[{i.title[:110]}]({i.url}) _({i.source[:30]})_"
                 for i in new_items[:10]]
        payload = json.dumps({"content": "**PokeWatch**\n" + "\n".join(lines)}).encode()
        req = urllib.request.Request(DISCORD_WEBHOOK, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print(f"    ! Discord: {e}", file=sys.stderr)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        text = "PokeWatch\n\n" + "\n\n".join(
            f"{CAT_META.get(i.category, ('Vorfall',''))[0]}\n{i.title[:110]}\n{i.url}"
            for i in new_items[:10])
        params = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID, "text": text,
            "disable_web_page_preview": "true"})
        http_get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?{params}")


# ----------------------------------------------------------------------------
# HAUPTPROGRAMM
# ----------------------------------------------------------------------------

# Zustand des laufenden Abrufs - der Server fragt ihn ab, damit der Knopf
# waehrend des Laufs zeigen kann, welche Quelle gerade dran ist.
RUN_STATE = {
    "running": False, "step": "", "done": 0, "total_steps": len(SOURCES),
    "last_run": None, "count": 0, "new": 0, "error": None,
}
_RUN_LOCK = threading.Lock()


def write_pwa_assets() -> None:
    """
    Handy-App neben den Feed legen. Icons werden nur einmal erzeugt -
    das Zeichnen kostet ein paar Sekunden und aendert sich nie.
    """
    try:
        import pwa_assets
    except ImportError:
        return  # ohne pwa_assets.py laeuft alles andere weiter

    (OUT_DIR / "app.html").write_text(pwa_assets.APP_HTML, encoding="utf-8")
    # Gleiche Datei als index.html: dann oeffnet die nackte URL direkt die App
    (OUT_DIR / "index.html").write_text(pwa_assets.APP_HTML, encoding="utf-8")
    (OUT_DIR / "manifest.webmanifest").write_text(pwa_assets.MANIFEST, encoding="utf-8")
    (OUT_DIR / "sw.js").write_text(pwa_assets.SERVICE_WORKER, encoding="utf-8")

    # Einzeldatei mit fest eingebackenen Daten: laesst sich direkt auf dem
    # Handy oeffnen, ohne Server und ohne feed.json daneben.
    feed = OUT_DIR / "feed.json"
    if feed.exists():
        seed = ('<script id="seed" type="application/json">'
                + feed.read_text(encoding="utf-8").replace("</", "<\\/")
                + "</script>")
        (OUT_DIR / "pokewatch-einzeldatei.html").write_text(
            pwa_assets.APP_HTML.replace("<!--SEED-->", seed), encoding="utf-8")

    for size, scale in ((192, 3), (512, 2)):
        target = OUT_DIR / f"icon-{size}.png"
        if not target.exists():
            target.write_bytes(pwa_assets.icon(size, scale))


def run_once(days: int = LOOKBACK_DAYS, only: list | None = None,
             region: str = "alle", push: bool = True) -> tuple[int, int]:
    """Ein kompletter Durchlauf. Gibt (Anzahl, davon neu) zurueck."""
    global LOOKBACK_DAYS
    LOOKBACK_DAYS = days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    known: dict = state.get("items", {})

    active = [(n, f) for n, f in SOURCES
              if not only or any(o.lower() in n.lower() for o in only)]
    RUN_STATE["total_steps"] = len(active)
    RUN_STATE["done"] = 0

    collected: list[Item] = []
    for name, fn in active:
        RUN_STATE["step"] = name
        print(f"  -> {name} ...")
        try:
            found = fn(cutoff)
        except Exception as e:
            print(f"    ! {name} fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            found = []
        print(f"     {len(found)} Treffer")
        collected.extend(found)
        RUN_STATE["done"] += 1

    RUN_STATE["step"] = "Auswerten"
    items = dedupe(tag_regions(collected))

    # Meldungen zum selben Vorfall zusammenfassen. Drei Zeitungen ueber
    # denselben Einbruch sind ein Eintrag, nicht drei.
    try:
        import clustering
        raw = [asdict(i) for i in items]
        merged = [clustering.summarize(g) for g in clustering.cluster(raw)]
        merged.sort(key=lambda d: d["published"], reverse=True)
        items = [Item(**{k: v for k, v in d.items()
                         if k in Item.__dataclass_fields__}) for d in merged]
        for it, d in zip(items, merged):
            it.related = d.get("related", [])
            it.report_count = d.get("report_count", 1)
        print(f"     {len(raw)} Meldungen -> {len(items)} Vorfaelle")
    except ImportError:
        pass

    if region and region != "alle":
        wanted = {"dach": {"de", "at", "ch"}}.get(region, {region})
        items = [i for i in items if i.region in wanted]

    new_keys: set[str] = set()
    for it in items:
        if it.key in known:
            it.first_seen = known[it.key].get("first_seen", it.published)
        else:
            it.first_seen = datetime.now(timezone.utc).isoformat()
            new_keys.add(it.key)
        known[it.key] = {"first_seen": it.first_seen, "title": it.title[:120]}

    # Beim allerersten Lauf ist alles "neu" - das waere nutzlos.
    if not state.get("last_run"):
        new_keys = set()

    # Streng nach Erscheinungsdatum, neueste zuerst. Frueher stand hier
    # zusaetzlich "in diesem Lauf neu entdeckt" als erstes Kriterium - dadurch
    # sprang eine alte Meldung, die eine Quelle gerade erst ausspuckte, ueber
    # die Nachrichten von heute Morgen. Als "neu" markiert werden sie in der
    # Anzeige weiterhin, sie draengeln sich nur nicht mehr vor.
    items.sort(key=lambda x: x.dt, reverse=True)
    items = items[:MAX_ITEMS]

    (OUT_DIR / "feed.html").write_text(render_html(items, new_keys), encoding="utf-8")
    (OUT_DIR / "feed.xml").write_text(render_rss(items), encoding="utf-8")
    (OUT_DIR / "feed.json").write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "new": len(new_keys),
        "items": [asdict(i) for i in items],
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    write_pwa_assets()

    state["items"] = dict(list(known.items())[-4000:])
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    if push:
        push_notifications([i for i in items if i.key in new_keys])

    RUN_STATE.update({"last_run": state["last_run"], "count": len(items),
                      "new": len(new_keys), "step": ""})
    return len(items), len(new_keys)


def local_ip() -> str:
    """Eigene WLAN-Adresse, damit das Handy den Rechner findet."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 1))  # verbindet nicht wirklich
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def start_refresh(days: int, region: str, push: bool) -> bool:
    """Startet einen Abruf im Hintergrund. False, wenn schon einer laeuft."""
    with _RUN_LOCK:
        if RUN_STATE["running"]:
            return False
        RUN_STATE.update({"running": True, "error": None, "step": "Start"})

    def worker():
        try:
            run_once(days=days, region=region, push=push)
        except Exception as e:
            RUN_STATE["error"] = f"{type(e).__name__}: {e}"
            print(f"! Abruf fehlgeschlagen: {e}", file=sys.stderr)
        finally:
            RUN_STATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


def serve(port: int, days: int, region: str, push: bool,
          auto_hours: float = 12.0) -> int:
    """Kleiner lokaler Server, damit der Aktualisieren-Knopf etwas ausloesen kann."""
    import http.server

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(OUT_DIR), **kw)

        def log_message(self, fmt, *a):
            pass  # Zugriffe nicht ins Terminal spammen

        def _json(self, payload: dict, code: int = 200):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.rstrip("/") == "/api/refresh":
                started = start_refresh(days, region, push)
                self._json({"started": started, **RUN_STATE},
                           200 if started else 409)
            else:
                self.send_error(404)

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path == "/api/status":
                self._json(dict(RUN_STATE))
                return
            if path == "":
                # Wurzel = App. Genau wie spaeter auf GitHub Pages,
                # damit sich lokal und im Netz nichts unterschiedlich verhaelt.
                self.path = "/index.html"
            # Feed nie aus dem Cache, sonst zeigt der Knopf nichts Neues
            if self.path.endswith((".html", ".json", ".xml", ".js", ".webmanifest")):
                target = OUT_DIR / self.path.lstrip("/")
                if not target.exists():
                    self.send_error(404)
                    return
                self.send_response(200)
                data = target.read_bytes()
                ctype = {"html": "text/html", "json": "application/json",
                         "xml": "application/rss+xml", "js": "text/javascript",
                         "webmanifest": "application/manifest+json"}[
                             self.path.rsplit(".", 1)[1]]
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            super().do_GET()

    if not (OUT_DIR / "feed.html").exists():
        print("Noch kein Feed vorhanden - hole einmal Daten ...")
        run_once(days=days, region=region, push=False)

    state = load_state()
    RUN_STATE["last_run"] = state.get("last_run")
    RUN_STATE["auto_hours"] = auto_hours

    def age_hours() -> float:
        last = RUN_STATE.get("last_run")
        if not last:
            return 1e9
        return (datetime.now(timezone.utc)
                - parse_date(last)).total_seconds() / 3600

    if auto_hours > 0:
        # Beim Start nachholen, falls die Daten schon zu alt sind
        if age_hours() >= auto_hours:
            print(f"Daten aelter als {auto_hours:g}h - hole nach ...")
            start_refresh(days, region, push)

        def ticker():
            while True:
                time.sleep(300)  # alle 5 Minuten pruefen
                if not RUN_STATE["running"] and age_hours() >= auto_hours:
                    print(f"[auto] Turnus erreicht, rufe ab ...")
                    start_refresh(days, region, push)

        threading.Thread(target=ticker, daemon=True).start()

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    lan = local_ip()
    print(f"\nPokeWatch laeuft auf http://127.0.0.1:{port}")
    print(f"Am Handy im selben WLAN:  http://{lan}:{port}")
    print(f"Grosses Dashboard:        http://127.0.0.1:{port}/feed.html")
    print("Der Aktualisieren-Knopf im Dashboard startet einen neuen Abruf.")
    if auto_hours > 0:
        print(f"Automatik: alle {auto_hours:g} Stunden, solange dieses Fenster laeuft.")
    else:
        print("Automatik aus - nur auf Knopfdruck.")
    print("Beenden mit Strg+C\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Newsfeed fuer Vorfaelle bei Pokemon-/TCG-Laeden")
    ap.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="Zeitfenster in Tagen")
    ap.add_argument("--only", nargs="*", help="Nur diese Quellen (news youtube reddit bluesky instagram)")
    ap.add_argument("--no-push", action="store_true", help="Keine Discord-/Telegram-Meldung")
    ap.add_argument("--region", choices=["alle", "de", "at", "ch", "dach", "int"],
                    default="alle", help="Nur diese Herkunft in den Feed aufnehmen")
    ap.add_argument("--add-channel", metavar="@HANDLE",
                    help="YouTube-Kanal dauerhaft aufnehmen")
    ap.add_argument("--serve", action="store_true",
                    help="Dashboard mit Aktualisieren-Knopf im Browser oeffnen")
    ap.add_argument("--port", type=int, default=8420, help="Port fuer --serve")
    ap.add_argument("--auto", type=float, default=12.0, metavar="STUNDEN",
                    help="Bei --serve automatisch alle N Stunden abrufen (0 = aus)")
    args = ap.parse_args()

    if args.add_channel:
        handle = args.add_channel if args.add_channel.startswith(("@", "UC")) \
            else "@" + args.add_channel
        cid = resolve_channel_id(handle)
        if not cid:
            print(f"Kanal nicht gefunden: {handle}")
            return 1
        wl = BASE_DIR / "channels_extra.json"
        current = json.loads(wl.read_text()) if wl.exists() else []
        if handle not in current:
            current.append(handle)
            wl.write_text(json.dumps(current, indent=1))
        print(f"{handle} aufgenommen ({cid}). Wird ab dem naechsten Lauf mitgelesen.")
        return 0
    if args.serve:
        return serve(args.port, args.days, args.region,
                     not args.no_push, args.auto)

    count, new = run_once(days=args.days, only=args.only,
                          region=args.region, push=not args.no_push)
    print(f"\n{count} Eintraege, davon {new} neu")
    print(f"-> {OUT_DIR / 'feed.html'}")
    return 0


# ----------------------------------------------------------------------------
# HTML-TEMPLATE
# ----------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PokeWatch</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#0b1020; --panel:#141b34; --panel-2:#1a2242; --line:#28325c;
  --paper:#e7eaf4; --muted:#8b96bd;
  --alert:#ff5a3c; --warn:#f5b32e; --cool:#5aa9e6; --legal:#a78bfa; --ship:#3ddc97;
  --holo:linear-gradient(100deg,#7df9ff,#c77dff 45%,#ffd166);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--ink); color:var(--paper);
  font-family:"IBM Plex Sans",system-ui,sans-serif; font-size:15px; line-height:1.55;
  background-image:radial-gradient(ellipse at 15% -10%,#18214a 0%,transparent 55%);
}
.wrap{max-width:900px;margin:0 auto;padding:0 20px 90px}

/* --- Kopf --- */
header{border-bottom:1px solid var(--line);padding:44px 0 26px;margin-bottom:26px}
.eyebrow{
  font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--muted);margin:0 0 10px
}
h1{
  font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:clamp(38px,8vw,66px);
  letter-spacing:-.01em;line-height:.94;margin:0;text-transform:uppercase
}
h1 em{font-style:normal;background:var(--holo);-webkit-background-clip:text;
  background-clip:text;color:transparent}
.sub{color:var(--muted);margin:12px 0 0;max-width:52ch}
.runline{
  font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted);
  margin-top:18px;display:flex;gap:16px;flex-wrap:wrap
}
.runline b{color:var(--paper);font-weight:500}

.actions{display:flex;align-items:center;gap:14px;margin-top:16px}
#refresh{background:var(--paper);color:var(--ink);border:0;
  font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.13em;
  text-transform:uppercase;padding:10px 18px;cursor:pointer;transition:.15s}
#refresh:hover:not(:disabled){background:#fff}
#refresh:disabled{opacity:.4;cursor:progress}
#refresh:focus-visible{outline:2px solid var(--cool);outline-offset:3px}
.progress{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--muted)}
.progress.err{color:var(--alert)}
.static-note{font-family:"IBM Plex Mono",monospace;font-size:11px;
  color:var(--muted);margin:16px 0 0;line-height:1.7}
.static-note code{background:var(--panel-2);padding:2px 7px;color:var(--paper)}

/* --- Zaehler --- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px;margin:26px 0 22px}
.stat{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line);
  padding:14px 16px}
.stat[data-tone=alert]{border-left-color:var(--alert)}
.stat[data-tone=warn]{border-left-color:var(--warn)}
.stat[data-tone=cool]{border-left-color:var(--cool)}
.stat[data-tone=legal]{border-left-color:var(--legal)}
.stat[data-tone=ship]{border-left-color:var(--ship)}
.stat-n{display:block;font-family:"Barlow Condensed",sans-serif;font-size:34px;
  font-weight:700;line-height:1}
.stat-l{display:block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-top:4px}

/* --- Filter --- */
.filterbar{border-top:1px solid var(--line);border-bottom:1px solid var(--line);
  padding:14px 0;margin-bottom:26px;display:flex;flex-direction:column;gap:11px}
.frow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.flabel{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted);width:74px;flex:0 0 74px}
.rbtn{background:transparent;border:1px solid var(--line);color:var(--muted);
  font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;padding:7px 13px;cursor:pointer;transition:.15s;
  display:inline-flex;align-items:center;gap:7px}
.rbtn:hover{border-color:var(--muted);color:var(--paper)}
.rbtn.on{background:var(--paper);color:var(--ink);border-color:var(--paper)}
.rbtn:focus-visible{outline:2px solid var(--cool);outline-offset:2px}
.rbtn .n{font-size:10px;opacity:.6}
.rbtn.on .n{opacity:.55}
.reg{font-family:"IBM Plex Mono",monospace;font-size:9.5px;letter-spacing:.1em;
  border:1px solid var(--line);padding:1px 6px;color:var(--muted)}
.reg-de{color:#ffd166;border-color:#5a4a1e}
.entry .meta .age{margin-left:auto}
.fbtn{
  background:transparent;border:1px solid var(--line);color:var(--muted);
  font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;padding:7px 13px;cursor:pointer;transition:.15s
}
.fbtn:hover{border-color:var(--muted);color:var(--paper)}
.fbtn.on{background:var(--paper);color:var(--ink);border-color:var(--paper)}
.fbtn:focus-visible{outline:2px solid var(--cool);outline-offset:2px}
.abtn{background:transparent;border:1px solid var(--line);color:var(--muted);
  font:inherit;font-size:12px;padding:4px 11px;border-radius:999px;cursor:pointer;
  display:inline-flex;align-items:center;gap:6px;transition:.15s}
.abtn:hover{border-color:var(--muted);color:var(--paper)}
.abtn.on{background:var(--cool);color:var(--ink);border-color:var(--cool)}
.abtn:focus-visible{outline:2px solid var(--cool);outline-offset:2px}
.abtn .n{font-size:10px;opacity:.6}
.abtn.on .n{opacity:.55}

/* --- Eintraege --- */
.entry{display:flex;gap:0;background:var(--panel);border:1px solid var(--line);
  margin-bottom:10px;transition:border-color .15s}
.entry:hover{border-color:#3b477a}
.rail{width:3px;flex:0 0 3px;background:var(--line)}
.entry[data-tone=alert] .rail{background:var(--alert)}
.entry[data-tone=warn]  .rail{background:var(--warn)}
.entry[data-tone=cool]  .rail{background:var(--cool)}
.entry[data-tone=legal] .rail{background:var(--legal)}
.entry[data-tone=ship]  .rail{background:var(--ship)}
.body{padding:15px 18px 14px;flex:1;min-width:0}
.shot{flex:0 0 104px;align-self:stretch;overflow:hidden;background:var(--line);
  display:block;border-left:1px solid var(--line)}
.shot img{width:100%;height:100%;min-height:96px;object-fit:cover;display:block}
/* Auf schmalen Bildschirmen wird aus dem Streifen ein Quadrat oben rechts -
   sonst zieht sich das Bild ueber die ganze Kartenhoehe und vom Motiv bleibt
   ein schmaler Ausschnitt uebrig. */
@media (max-width:560px){
  .shot{flex-basis:84px;align-self:flex-start;height:84px;margin:12px 12px 0 0;
    border-left:0;border-radius:6px}
  .shot img{min-height:0;height:84px}
}

.meta{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
  font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);margin-bottom:9px}
.meta .cat{color:var(--paper);font-weight:600}
.dot{width:3px;height:3px;border-radius:50%;background:currentColor;opacity:.5}
.age{margin-left:auto;opacity:.75}

.entry h2{font-family:"Barlow Condensed",sans-serif;font-weight:600;
  font-size:23px;line-height:1.15;margin:0 0 7px;letter-spacing:.005em}
.entry h2 a{color:var(--paper);text-decoration:none}
.entry h2 a:hover{text-decoration:underline;text-underline-offset:3px}
.entry h2 a:focus-visible{outline:2px solid var(--cool);outline-offset:3px}
.sum{margin:0;color:var(--muted);font-size:13.5px;line-height:1.5}

.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px}
.tag{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--muted);
  border:1px solid var(--line);padding:2px 7px;letter-spacing:.05em}

/* Signatur: neue Eintraege bekommen den Holo-Schimmer einer Kartenrueckseite */
.entry.is-new{position:relative;background:var(--panel-2)}
.entry.is-new .rail{background:var(--holo);background-size:100% 300%;
  animation:holo 4.5s ease-in-out infinite}
.entry.is-new .body::before{
  content:"NEU";position:absolute;top:-1px;right:-1px;
  font-family:"IBM Plex Mono",monospace;font-size:9.5px;letter-spacing:.18em;
  padding:3px 8px;color:var(--ink);background:var(--holo)}
@keyframes holo{0%,100%{background-position:0 0}50%{background-position:0 100%}}
@media (prefers-reduced-motion:reduce){
  .entry.is-new .rail{animation:none;background:#7df9ff}
}

.resultline{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--muted);
  letter-spacing:.08em;margin:0 0 12px;text-transform:uppercase}
.empty{border:1px dashed var(--line);padding:40px 26px;text-align:center;color:var(--muted)}
.empty p:first-child{font-family:"Barlow Condensed",sans-serif;font-size:26px;
  color:var(--paper);margin:0 0 10px;text-transform:uppercase}
.empty-hint{max-width:46ch;margin:0 auto;font-size:13.5px}

footer{border-top:1px solid var(--line);margin-top:34px;padding-top:18px;
  font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--muted);line-height:1.8}
footer a{color:var(--cool)}
@media(max-width:560px){.age{margin-left:0;width:100%}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="eyebrow">Vorfaelle im Kartenhandel &middot; letzte %%WINDOW%% Tage</p>
  <h1>Poke<em>Watch</em></h1>
  <p class="sub">Einbrueche, Betrug, Razzien und Schliessungen bei Pokemon- und
     TCG-Laeden. Zusammengetragen aus Presse, YouTube, Reddit und Bluesky.</p>
  <div class="runline">
    <span>Stand <b id="stand">%%UPDATED%%</b></span>
    <span>Eintraege <b>%%TOTAL%%</b></span>
    <span>Neu seit letztem Lauf <b>%%NEW%%</b></span>
  </div>
  <div class="actions" id="actions" hidden>
    <button id="refresh">Jetzt aktualisieren</button>
    <span class="progress" id="progress"></span>
  </div>
  <p class="static-note" id="staticnote">
    Statische Datei &mdash; sie aktualisiert sich nicht von selbst.
    Fuer einen Aktualisieren-Knopf:
    <code>python3 pokewatch.py --serve</code>
  </p>
</header>

<div class="stats">%%STATS%%</div>

<div class="filterbar">
%%KINDROW%%
  <div class="frow">
    <span class="flabel">Herkunft</span>
    %%REGIONS%%
  </div>
  <div class="frow">
    <span class="flabel">Vorfall</span>
    <button class="fbtn on" data-filter="all">Alle</button>
    %%FILTERS%%
  </div>
</div>

<p class="resultline"><span id="count">%%TOTAL%%</span> von %%TOTAL%% Eintraegen</p>

<main id="feed">
%%ENTRIES%%
<div class="empty" id="none" hidden>
  <p>Keine Treffer</p>
  <p class="empty-hint" id="leerhinweis" hidden></p>
  <p class="empty-hint">Diese Kombination aus Herkunft und Vorfall enthaelt
     nichts. Setz einen der beiden Filter zurueck auf "Alle".</p>
</div>
</main>

<footer>
  Instagram laesst sich nicht nach Stichworten durchsuchen &mdash; Meta hat die
  oeffentliche Hashtag-Suche abgeschaltet. Was dort passiert, faengt dieser Feed
  nur ueber Umwege ein.<br>
  Auch als <a href="feed.xml">RSS</a> und <a href="feed.json">JSON</a>.
</footer>
</div>

<script>
// Der Knopf funktioniert nur, wenn die Seite von pokewatch --serve kommt.
// Bei file:// gaebe es nichts, was den Abruf ausloesen koennte.
const served = location.protocol.startsWith('http');
document.getElementById('actions').hidden = !served;
document.getElementById('staticnote').hidden = served;

if (served) {
  const btn = document.getElementById('refresh');
  const prog = document.getElementById('progress');
  let polling = null;

  function show(msg, isError) {
    prog.textContent = msg;
    prog.classList.toggle('err', !!isError);
  }

  async function poll() {
    try {
      const st = await (await fetch('/api/status', {cache: 'no-store'})).json();
      if (st.running) {
        const step = st.step ? st.step : 'laeuft';
        show(`${step} (${st.done}/${st.total_steps})`);
        return;
      }
      clearInterval(polling);
      polling = null;
      btn.disabled = false;
      if (st.error) { show(st.error, true); return; }
      show('Fertig, lade neu');
      location.reload();
    } catch (e) {
      clearInterval(polling);
      polling = null;
      btn.disabled = false;
      show('Server nicht erreichbar', true);
    }
  }

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    show('Starte');
    try {
      const r = await fetch('/api/refresh', {method: 'POST'});
      if (r.status === 409) { show('Laeuft bereits'); }
      polling = setInterval(poll, 1500);
    } catch (e) {
      btn.disabled = false;
      show('Start fehlgeschlagen', true);
    }
  });

  // Laeuft beim Oeffnen schon ein Abruf (z.B. der automatische)? Dann anzeigen.
  fetch('/api/status', {cache: 'no-store'}).then(r => r.json()).then(st => {
    if (st.running) { btn.disabled = true; polling = setInterval(poll, 1500); }
  }).catch(() => {});
}

const catButtons = document.querySelectorAll('.fbtn');
const regButtons = document.querySelectorAll('.rbtn');
const artButtons = document.querySelectorAll('.abtn');
const entries = document.querySelectorAll('.entry');
let cat = 'all', region = '%%REGION_START%%', art = 'all';

function apply() {
  let shown = 0;
  entries.forEach(e => {
    const ok = (cat === 'all' || e.dataset.cat === cat)
            && (region === 'all' || e.dataset.region === region)
            && (art === 'all' || e.dataset.art === art);
    e.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  document.getElementById('count').textContent = shown;
  document.getElementById('none').hidden = shown > 0;
  // Wenn fuer Deutschland nichts vorliegt, soll man nicht raten muessen,
  // wo die uebrigen Meldungen stecken.
  const hinweis = document.getElementById('leerhinweis');
  if (hinweis) {
    const gesamt = entries.length;
    hinweis.hidden = !(shown === 0 && gesamt > 0);
    hinweis.textContent = gesamt === 1
      ? 'Es liegt 1 Meldung aus anderen Laendern vor - oben unter Herkunft umschalten.'
      : `Es liegen ${gesamt} Meldungen aus anderen Laendern vor - oben unter Herkunft umschalten.`;
  }
}

catButtons.forEach(b => b.addEventListener('click', () => {
  cat = b.dataset.filter;
  catButtons.forEach(x => x.classList.toggle('on', x === b));
  apply();
}));
regButtons.forEach(b => b.addEventListener('click', () => {
  region = b.dataset.region;
  regButtons.forEach(x => x.classList.toggle('on', x === b));
  apply();
}));
artButtons.forEach(b => b.addEventListener('click', () => {
  art = b.dataset.art;
  artButtons.forEach(x => x.classList.toggle('on', x === b));
  // Beim Wechsel der Art die Kategorie zurueck auf "Alle" - sonst waehlt man
  // "News" und sieht nichts, weil noch "Einbruch" aktiv ist.
  cat = 'all';
  catButtons.forEach(x => x.classList.toggle('on', x.dataset.filter === 'all'));
  apply();
}));

// Einmal beim Laden anwenden - sonst stuende der Knopf auf "Deutschland",
// waehrend darunter noch alle Meldungen der Welt liegen.
apply();
</script>
</body>
</html>"""


if __name__ == "__main__":
    sys.exit(main())

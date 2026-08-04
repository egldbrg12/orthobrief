#!/usr/bin/env python3
"""
OrthoBrief — today's orthopaedic literature, in one place.

Aggregates newly published orthopaedic papers from two canonical sources, tags
each with its subspecialty and study design, and lets the reader follow only
the parts of the field they care about:

  * Crossref  — DOIs are registered the day an article goes online, so this is
                the fastest signal that something new exists.
  * PubMed    — slower to index, but has clean structured abstracts.

Results are merged on DOI (falling back to normalized title), filtered to
orthopaedics, and given a short extractive summary. No API keys, no
paid services, no third-party packages — standard library only.

    python3 app.py              # serve on http://localhost:8087
    python3 app.py --json       # print today's feed as JSON (cron-friendly)
    python3 app.py --check      # sanity-check the journal ISSN table
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")
CACHE_TTL = 30 * 60  # seconds; a re-fetch of the same window inside this is free
# Bump whenever a cached feed gains a field the app now depends on, so stale
# files are refetched rather than served half-populated.
FEED_VERSION = 3
# Deep-paging backstops, so a busy window is never silently cut off at one page.
MAX_PAGES = 12          # Crossref cursor pages, 500 rows each
MAX_PMIDS = 9000        # PubMed's esearch ceiling is 10k per query
MAX_DAYS = 30           # longest window the API will serve
# NB: browsers hard-block a list of "unsafe" ports (SIP 5060/5061, SMTP 25,
# NFS 2049, …) and refuse to connect at all — the page just goes blank. 8087 is
# outside that list.
PORT = int(os.environ.get("ORTHOBRIEF_PORT", "8087"))
# Crossref's polite pool and NCBI both want a contact address. The default here
# is deliberately impersonal so a public repo doesn't publish a private inbox —
# set ORTHOBRIEF_EMAIL locally (and as a repo variable) to the real one.
CONTACT = os.environ.get("ORTHOBRIEF_EMAIL", "orthobrief@users.noreply.github.com")
USER_AGENT = f"OrthoBrief/1.0 (mailto:{CONTACT})"

# ---------------------------------------------------------------------------
# Subspecialty fields
# ---------------------------------------------------------------------------
# Orthopaedics is not one literature — it is ten, each with its own journals,
# its own questions, and (mostly) its own readers. Every paper is tagged with
# the fields it belongs to, and the reader picks which of them they follow.
#
# The split follows fellowship lines rather than anatomy, because that is how
# journals, departments and training are organised: a revision TKA paper is
# arthroplasty, not "knee", and doesn't belong in the same bucket as an ACL
# reconstruction.

FIELDS = [
    {"key": "arthro",   "label": "Arthroplasty",   "blurb": "Hip, knee, shoulder replacement; revision; PJI"},
    {"key": "sports",   "label": "Sports medicine","blurb": "Ligament, meniscus, cartilage, arthroscopy, return to play"},
    {"key": "trauma",   "label": "Trauma",         "blurb": "Fracture care, fixation, nonunion, polytrauma"},
    {"key": "spine",    "label": "Spine",          "blurb": "Degenerative, deformity, fusion, disc"},
    {"key": "hand",     "label": "Hand & upper extremity", "blurb": "Hand, wrist, distal radius, nerve compression"},
    {"key": "shoulder", "label": "Shoulder & elbow","blurb": "Rotator cuff, instability, elbow"},
    {"key": "foot",     "label": "Foot & ankle",   "blurb": "Hindfoot, forefoot, Achilles, ankle"},
    {"key": "peds",     "label": "Pediatrics",     "blurb": "Paediatric ortho, deformity, growth"},
    {"key": "onc",      "label": "MSK oncology",   "blurb": "Bone and soft-tissue tumour, metastatic disease"},
    {"key": "general",  "label": "General ortho",  "blurb": "Education, policy, epidemiology, everything else"},
]

FIELD_KEYS = [f["key"] for f in FIELDS]
FIELD_LABELS = {f["key"]: f["label"] for f in FIELDS}
_FIELD_ORDER = {f["key"]: i for i, f in enumerate(FIELDS)}

# ---------------------------------------------------------------------------
# Journal table
# ---------------------------------------------------------------------------
# `fields` lists the subspecialties a journal is *dedicated* to — everything it
# publishes is kept and tagged with them, no cue matching needed. An empty list
# marks a general orthopaedic journal, whose papers are tagged by cue instead.
#
# `pm` is the NLM title abbreviation used for PubMed's [Journal] field. A wrong
# ISSN degrades gracefully (PubMed still covers the journal by name) — run
# `--check` to find them.

JOURNALS = [
    # --- general orthopaedics ----------------------------------------------
    {"name": "JBJS",                               "issn": "0021-9355", "pm": "J Bone Joint Surg Am",        "fields": []},
    {"name": "JBJS Open Access",                   "issn": "2472-7245", "pm": "JB JS Open Access",           "fields": []},
    {"name": "The Bone & Joint Journal",           "issn": "2049-4394", "pm": "Bone Joint J",                "fields": []},
    {"name": "Bone & Joint Open",                  "issn": "2633-1462", "pm": "Bone Jt Open",                "fields": []},
    {"name": "Bone & Joint Research",              "issn": "2046-3758", "pm": "Bone Joint Res",              "fields": []},
    {"name": "Clinical Orthopaedics & Related Research", "issn": "0009-921X", "pm": "Clin Orthop Relat Res", "fields": []},
    {"name": "Acta Orthopaedica",                  "issn": "1745-3674", "pm": "Acta Orthop",                 "fields": []},
    {"name": "International Orthopaedics",         "issn": "0341-2695", "pm": "Int Orthop",                  "fields": []},
    {"name": "J Orthopaedic Surgery & Research",   "issn": "1749-799X", "pm": "J Orthop Surg Res",           "fields": []},
    {"name": "JAAOS",                              "issn": "1067-151X", "pm": "J Am Acad Orthop Surg",       "fields": []},
    {"name": "EFORT Open Reviews",                 "issn": "2058-5241", "pm": "EFORT Open Rev",              "fields": []},
    {"name": "Journal of Orthopaedics",            "issn": "0972-978X", "pm": "J Orthop",                    "fields": []},
    {"name": "Journal of Experimental Orthopaedics","issn": "2197-1153","pm": "J Exp Orthop",                "fields": []},
    {"name": "Journal of Orthopaedic Research",    "issn": "0736-0266", "pm": "J Orthop Res",                "fields": []},
    {"name": "Orthopedic Clinics of North America","issn": "0030-5898", "pm": "Orthop Clin North Am",        "fields": []},
    {"name": "Orthop & Traumatology: Surg & Res",  "issn": "1877-0568", "pm": "Orthop Traumatol Surg Res",   "fields": []},
    {"name": "Eur J Orthop Surg & Traumatology",   "issn": "1432-1068", "pm": "Eur J Orthop Surg Traumatol", "fields": []},
    {"name": "Archives of Orthop & Trauma Surgery","issn": "1434-3916", "pm": "Arch Orthop Trauma Surg",     "fields": []},

    # --- arthroplasty -------------------------------------------------------
    {"name": "The Journal of Arthroplasty",        "issn": "0883-5403", "pm": "J Arthroplasty",              "fields": ["arthro"]},
    {"name": "Arthroplasty Today",                 "issn": "2352-3441", "pm": "Arthroplast Today",           "fields": ["arthro"]},
    {"name": "Arthroplasty",                       "issn": "2524-7948", "pm": "Arthroplasty",                "fields": ["arthro"]},
    {"name": "Seminars in Arthroplasty",           "issn": "1045-4527", "pm": "Semin Arthroplasty",          "fields": ["arthro"]},
    {"name": "Knee Surgery & Related Research",    "issn": "2234-2451", "pm": "Knee Surg Relat Res",         "fields": ["arthro"]},
    {"name": "HIP International",                  "issn": "1120-7000", "pm": "Hip Int",                     "fields": ["arthro"]},

    # --- sports medicine ----------------------------------------------------
    {"name": "American Journal of Sports Medicine","issn": "0363-5465", "pm": "Am J Sports Med",             "fields": ["sports"]},
    {"name": "Arthroscopy",                        "issn": "0749-8063", "pm": "Arthroscopy",                 "fields": ["sports"]},
    {"name": "Orthopaedic J of Sports Medicine",   "issn": "2325-9671", "pm": "Orthop J Sports Med",         "fields": ["sports"]},
    {"name": "Arthroscopy, Sports Med & Rehab",    "issn": "2666-061X", "pm": "Arthrosc Sports Med Rehabil", "fields": ["sports"]},
    {"name": "Knee Surg Sports Traumatol Arthrosc","issn": "0942-2056", "pm": "Knee Surg Sports Traumatol Arthrosc", "fields": ["sports"]},
    {"name": "Sports Health",                      "issn": "1941-7381", "pm": "Sports Health",               "fields": ["sports"]},
    {"name": "Clinical J of Sport Medicine",       "issn": "1050-642X", "pm": "Clin J Sport Med",            "fields": ["sports"]},
    {"name": "The Knee",                           "issn": "0968-0160", "pm": "Knee",                        "fields": []},

    # --- trauma -------------------------------------------------------------
    {"name": "Journal of Orthopaedic Trauma",      "issn": "0890-5339", "pm": "J Orthop Trauma",             "fields": ["trauma"]},
    {"name": "Injury",                             "issn": "0020-1383", "pm": "Injury",                      "fields": ["trauma"]},
    {"name": "OTA International",                  "issn": "2574-2167", "pm": "OTA Int",                     "fields": ["trauma"]},
    {"name": "Eur J Trauma & Emergency Surgery",   "issn": "1863-9941", "pm": "Eur J Trauma Emerg Surg",     "fields": ["trauma"]},

    # --- spine --------------------------------------------------------------
    {"name": "Spine",                              "issn": "0362-2436", "pm": "Spine (Phila Pa 1976)",       "fields": ["spine"]},
    {"name": "European Spine Journal",             "issn": "0940-6719", "pm": "Eur Spine J",                 "fields": ["spine"]},
    {"name": "The Spine Journal",                  "issn": "1529-9430", "pm": "Spine J",                     "fields": ["spine"]},
    {"name": "Global Spine Journal",               "issn": "2192-5682", "pm": "Global Spine J",              "fields": ["spine"]},
    {"name": "Journal of Neurosurgery: Spine",     "issn": "1547-5654", "pm": "J Neurosurg Spine",           "fields": ["spine"]},
    {"name": "Clinical Spine Surgery",             "issn": "2380-0186", "pm": "Clin Spine Surg",             "fields": ["spine"]},
    {"name": "North American Spine Society J",     "issn": "2666-5484", "pm": "N Am Spine Soc J",            "fields": ["spine"]},
    {"name": "Spine Deformity",                    "issn": "2212-134X", "pm": "Spine Deform",                "fields": ["spine", "peds"]},

    # --- hand & upper extremity --------------------------------------------
    {"name": "J Hand Surgery (American)",          "issn": "0363-5023", "pm": "J Hand Surg Am",              "fields": ["hand"]},
    {"name": "J Hand Surgery (European)",          "issn": "1753-1934", "pm": "J Hand Surg Eur Vol",         "fields": ["hand"]},
    {"name": "HAND",                               "issn": "1558-9447", "pm": "Hand (N Y)",                  "fields": ["hand"]},
    {"name": "J Hand Surgery Global Online",       "issn": "2589-5141", "pm": "J Hand Surg Glob Online",     "fields": ["hand"]},
    {"name": "Hand Clinics",                       "issn": "0749-0712", "pm": "Hand Clin",                   "fields": ["hand"]},
    {"name": "Journal of Wrist Surgery",           "issn": "2163-3916", "pm": "J Wrist Surg",                "fields": ["hand"]},

    # --- shoulder & elbow ---------------------------------------------------
    {"name": "J Shoulder and Elbow Surgery",       "issn": "1058-2746", "pm": "J Shoulder Elbow Surg",       "fields": ["shoulder"]},
    {"name": "JSES International",                 "issn": "2666-6383", "pm": "JSES Int",                    "fields": ["shoulder"]},
    {"name": "JSES Reviews, Reports & Techniques", "issn": "2666-6391", "pm": "JSES Rev Rep Tech",           "fields": ["shoulder"]},
    {"name": "Shoulder & Elbow",                   "issn": "1758-5732", "pm": "Shoulder Elbow",              "fields": ["shoulder"]},

    # --- foot & ankle -------------------------------------------------------
    {"name": "Foot & Ankle International",         "issn": "1071-1007", "pm": "Foot Ankle Int",              "fields": ["foot"]},
    {"name": "Foot and Ankle Surgery",             "issn": "1268-7731", "pm": "Foot Ankle Surg",             "fields": ["foot"]},
    {"name": "Foot & Ankle Orthopaedics",          "issn": "2473-0114", "pm": "Foot Ankle Orthop",           "fields": ["foot"]},
    {"name": "J of Foot & Ankle Surgery",          "issn": "1067-2516", "pm": "J Foot Ankle Surg",           "fields": ["foot"]},

    # --- paediatrics --------------------------------------------------------
    {"name": "Journal of Pediatric Orthopaedics",  "issn": "0271-6798", "pm": "J Pediatr Orthop",            "fields": ["peds"]},
    {"name": "J Pediatric Orthopaedics B",         "issn": "1060-152X", "pm": "J Pediatr Orthop B",          "fields": ["peds"]},
    {"name": "Journal of Children's Orthopaedics", "issn": "1863-2521", "pm": "J Child Orthop",              "fields": ["peds"]},

    # --- musculoskeletal oncology ------------------------------------------
    {"name": "Journal of Bone Oncology",           "issn": "2212-1374", "pm": "J Bone Oncol",                "fields": ["onc"]},
    # Crossref has no recent deposits for Sarcoma; PubMed covers it by name.
    {"name": "Sarcoma",                            "issn": "1357-714X", "pm": "Sarcoma",                     "fields": ["onc"]},
]

# Journals dedicated to a field, indexed both ways so a paper can be tagged from
# whichever identifier the source happened to carry.
JOURNAL_FIELDS_BY_ISSN = {j["issn"]: j["fields"] for j in JOURNALS if j["fields"]}
JOURNAL_FIELDS_BY_NAME = {j["pm"].lower(): j["fields"] for j in JOURNALS if j["fields"]}
ORTHO_ISSNS = {j["issn"] for j in JOURNALS}
ORTHO_NAMES = {j["pm"].lower() for j in JOURNALS}
ALL_ISSNS = [j["issn"] for j in JOURNALS]

# ---------------------------------------------------------------------------
# Field cues
# ---------------------------------------------------------------------------
# (field, weight, pattern). A paper can belong to more than one field — a
# revision shoulder arthroplasty paper is genuinely both arthroplasty and
# shoulder — so this scores every field and keeps all of them that clear the
# bar, with the top scorer as the primary tag.

_FIELD_CUES = [
    ("arthro", 6, r"arthroplast|joint replacement|hemiarthroplast|(hip|knee|shoulder|ankle|elbow|wrist) replacement|\bt[kh]a\b|\buka\b|\btsa\b|\brtsa\b|\btja\b|\bt[kh]r\b|\bukr\b|periprosthetic|prosthetic joint infection|\bpji\b|aseptic loosening|unicompartmental|hip resurfacing|revision (hip|knee|shoulder)"),
    ("arthro", 4, r"acetabular (cup|component|liner)|femoral (stem|head component)|tibial (component|insert|baseplate)|polyethylene (liner|wear|insert)|implant survivorship|prosthesis survival|patellofemoral replacement|dual mobility|cemented|cementless"),

    ("sports", 6, r"\bacl\b|\bpcl\b|\bmcl\b|anterior cruciate|posterior cruciate|collateral ligament|menisc|labral|labrum|\bslap tear|patellar instability|\bmpfl\b|osteochondral (allograft|autograft)|cartilage (repair|restoration)|\bmaci\b|return to (sport|play)|athletes?\b|ulnar collateral ligament"),
    ("sports", 4, r"arthroscop|hamstring (autograft|tendon)|quadriceps tendon graft|tenodesis|femoroacetabular impingement|\bfai\b|hip preservation|sports[- ]related"),

    ("trauma", 6, r"fracture|nonunion|non[- ]union|malunion|\borif\b|open reduction|intramedullary nail|external fixat|polytrauma|damage control|gustilo|osteosynthesis|dislocation of the (hip|shoulder)"),
    ("trauma", 4, r"plate fixation|screw fixation|\bk[- ]wire|traction|acute injur|emergency department|trauma (centre|center|patients)"),

    ("spine", 6, r"\bspine\b|spinal|lumbar|cervical (spine|disc|radiculopathy|myelopathy)|thoracolumbar|scoliosis|spondylolisthesis|kyphosis|laminectomy|discectomy|interbody|vertebr|disc (herniation|degeneration|replacement)|myelopathy|spinal stenosis|pedicle screw"),

    ("hand", 6, r"\bhand\b|\bwrist\b|carpal tunnel|scaphoid|distal radius|trigger finger|dupuytren|thumb (cmc|basal)|flexor tendon|metacarp|phalan|replantation|\bfinger|digital nerve|ulnar tunnel|de quervain"),

    ("shoulder", 6, r"shoulder|rotator cuff|glenohumeral|glenoid|humeral head|\belbow\b|olecranon|epicondylitis|acromioclavicular|\bclavicle|biceps tenodesis|subacromial|\bcuff (tear|repair)"),

    ("foot", 6, r"\bfoot\b|\bankle\b|hallux (valgus|rigidus)|bunion|plantar (fasciitis|plate)|achilles|calcane|metatars|flatfoot|pes planus|charcot|syndesmo|talus|talar|subtalar|hindfoot|forefoot"),

    ("peds", 6, r"p(a)?ediatric|developmental dysplasia|\bddh\b|slipped capital femoral|\bscfe\b|perthes|clubfoot|supracondylar humer|physeal|growth plate|limb lengthening|skeletally immature"),
    # Generic enough to appear in any paper that mentions its population, so
    # they can colour a tag but can't carry one alone (see MIN_FIELD_SCORE).
    ("peds", 3, r"\bchildren\b|\bchild\b|adolescen|\binfant|congenital"),

    ("onc", 6, r"sarcoma|osteosarcoma|chondrosarcoma|ewing|giant cell tumou?r|bone tumou?r|soft[- ]tissue tumou?r|metasta(tic|ses) (bone|disease|lesion)|skeletal metasta|endoprosthetic reconstruction|\bmyeloma\b|tumou?r resection"),
    # "Limb salvage" means something entirely different in vascular surgery.
    ("onc", 3, r"limb salvage"),
]

# A field needs at least one solid cue — one weight-6 body hit, or a weaker cue
# in the title. Below this the tag is a passing mention, not the subject.
MIN_FIELD_SCORE = 5.0

# Outside the orthopaedic journal list, these veto the paper outright: the topic
# queries reach into oncology, plastics and vascular surgery, where the same
# words describe entirely different operations.
NOT_ORTHO_RE = re.compile(
    r"orbital|maxillofacial|mandib|craniofacial|\bskull\b|\bdental\b|periodont|\bnasal\b"
    r"|uterine|ovarian|cervical cancer|\bbreast\b|prostate|pulmonary adenocarcinoma"
    r"|coronary|myocardial|aortic|revasculari[sz]ation|\bbowel\b|hepat|cleft"
    r"|corneal|retinal|\bburn (injur|wound)",
    re.IGNORECASE,
)

# Front matter that is not a paper at all.
JUNK_TITLE_RE = re.compile(
    r"^\s*(journal )?(cme|continuing medical education) (instructions|questions)"
    r"|^\s*(editorial board|table of contents|masthead|front matter|cover\b"
    r"|information for (readers|authors|contributors)|instructions (to|for) authors"
    r"|(subject|author) index|acknowledg(e)?ment of reviewers|list of reviewers"
    r"|announcement|calendar of events|meetings? calendar)"
    # A bare "Erratum" with no subject is unreadable; "Corrigendum to '…'" stays.
    r"|^\s*(erratum|corrigendum|correction)\s*\.?\s*$",
    re.IGNORECASE,
)

FIELD_CUES = [(key, weight, re.compile(pat, re.IGNORECASE)) for key, weight, pat in _FIELD_CUES]

# A cue this far below the winner isn't a real second subject, just a mention.
FIELD_SECONDARY_RATIO = 0.5
FIELD_TITLE_BOOST = 1.8


def classify_fields(paper: dict) -> dict:
    """Tag a paper with the subspecialties it belongs to.

    Returns {"primary": key, "all": [keys]}. A journal dedicated to a field
    settles it outright — everything in *Foot & Ankle International* is foot and
    ankle — otherwise the cues decide, and a paper can carry more than one.
    """
    title = paper.get("title") or ""
    if JUNK_TITLE_RE.search(title):
        return {"primary": "", "all": []}

    dedicated: list[str] = []
    for issn in paper.get("issns") or []:
        dedicated += JOURNAL_FIELDS_BY_ISSN.get(issn, [])
    dedicated += JOURNAL_FIELDS_BY_NAME.get((paper.get("journal") or "").lower(), [])
    in_ortho_journal = bool(dedicated) or bool(set(paper.get("issns") or []) & ORTHO_ISSNS) \
        or (paper.get("journal") or "").lower() in ORTHO_NAMES

    # A paper from outside orthopaedics has to announce itself in the title. One
    # cue buried in an abstract is how a vascular limb-salvage series or a
    # maxillofacial fracture paper ends up in an orthopaedic feed.
    if not in_ortho_journal and NOT_ORTHO_RE.search(title):
        return {"primary": "", "all": []}

    body, _ = split_evidence(paper.get("abstract") or "")

    scores: dict[str, float] = {}
    for key, weight, rx in FIELD_CUES:
        if rx.search(title):
            scores[key] = scores.get(key, 0.0) + weight * FIELD_TITLE_BOOST
        elif rx.search(body) and in_ortho_journal:
            scores[key] = scores.get(key, 0.0) + weight

    # The journal's own subject is worth more than any single cue.
    for key in dict.fromkeys(dedicated):
        scores[key] = scores.get(key, 0.0) + 12.0

    ranked = [kv for kv in sorted(scores.items(), key=lambda kv: (-kv[1], _FIELD_ORDER[kv[0]]))
              if kv[1] >= MIN_FIELD_SCORE]

    if not ranked:
        # In an orthopaedic journal but about nothing the cues recognise —
        # education, policy, workforce, epidemiology. That is its own field.
        return {"primary": "general", "all": ["general"]} if in_ortho_journal \
            else {"primary": "", "all": []}

    top = ranked[0][1]
    keep = [k for k, v in ranked if v >= top * FIELD_SECONDARY_RATIO]
    return {"primary": ranked[0][0], "all": sorted(keep, key=lambda k: _FIELD_ORDER[k])}


# Crossref "type" values that aren't papers.
SKIP_TYPES = {"component", "peer-review", "grant", "dataset", "book", "book-series"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, timeout: int = 30, retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a banner
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _get_json(url: str, **kw) -> dict:
    return json.loads(_get(url, **kw).decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str | None) -> str:
    """Strip JATS/HTML markup and normalize whitespace."""
    if not raw:
        return ""
    txt = _TAG_RE.sub(" ", raw)
    txt = htmllib.unescape(txt)
    txt = _WS_RE.sub(" ", txt).strip()
    # Publishers frequently prefix the literal word "Abstract".
    txt = re.sub(r"^(abstract|summary)[:\s\-–]+", "", txt, flags=re.I)
    return txt


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    # Protect common abbreviations from the naive split.
    guarded = re.sub(r"\b(vs|e\.g|i\.e|approx|Dr|no|Fig|et al)\.", r"\1<DOT>", text, flags=re.I)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", guarded)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:120]


# ---------------------------------------------------------------------------
# Extractive summarizer
# ---------------------------------------------------------------------------
# Deliberately local + free: no LLM call, no API key, deterministic. Structured
# abstracts already carry the answer in their CONCLUSIONS section, so the job is
# mostly "find the conclusion, add the single most quantitative result".

SECTION_RE = re.compile(r"\b([A-Z][A-Z][A-Z /&'\-]{2,}?)\s*[:\.]\s+")

CONCLUSION_KEYS = ("CONCLUSION", "INTERPRETATION", "DISCUSSION", "SUMMARY")
RESULT_KEYS = ("RESULT", "FINDING", "OUTCOME")

_QUANT_RE = re.compile(r"(\d+(\.\d+)?\s?%|p\s?[=<>]\s?0?\.\d+|95%\s?CI|\bOR\b|\bHR\b|\bn\s?=\s?\d+|\d{3,})")
_SIGNAL_RE = re.compile(
    r"we\s+(found|observed|report)|significantly|no\s+(significant\s+)?difference|"
    r"associated\s+with|was\s+(higher|lower|greater|superior|comparable)|"
    r"these\s+(results|findings)|demonstrat|suggest",
    re.I,
)
_BACKGROUND_RE = re.compile(
    r"remains\s+(unclear|controversial|unknown)|little\s+is\s+known|the\s+(purpose|aim|objective|goal)\s+of\s+this|"
    r"we\s+(sought|aimed)\s+to|has\s+become\s+increasingly|is\s+a\s+common",
    re.I,
)


def parse_sections(abstract: str) -> dict[str, str]:
    """Split a structured abstract into {LABEL: body}. Empty dict if unstructured."""
    hits = list(SECTION_RE.finditer(abstract))
    if len(hits) < 2:
        return {}
    out: dict[str, str] = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(abstract)
        label = m.group(1).strip().rstrip(":.")
        out[label] = abstract[m.end():end].strip()
    return out


def _first_match(sections: dict[str, str], keys: tuple[str, ...]) -> str:
    for label, body in sections.items():
        if any(k in label.upper() for k in keys):
            return body
    return ""


def _trim(sentences: list[str], limit: int = 340) -> str:
    out: list[str] = []
    total = 0
    for s in sentences:
        if out and total + len(s) > limit:
            break
        out.append(s)
        total += len(s) + 1
        if total > limit:
            break
    text = " ".join(out).strip()
    if len(text) > limit + 60:
        text = text[: limit + 57].rsplit(" ", 1)[0] + "…"
    return text


def summarize(abstract: str, title: str = "") -> str:
    """Two-sentence gist of a paper. Prefers conclusion + hardest number."""
    abstract = clean_text(abstract)
    if not abstract:
        return ""
    if len(abstract) < 320:
        return abstract  # already short enough to be its own summary

    sections = parse_sections(abstract)
    if sections:
        concl = split_sentences(_first_match(sections, CONCLUSION_KEYS))[:2]
        results = split_sentences(_first_match(sections, RESULT_KEYS))
        quant = next((s for s in results if _QUANT_RE.search(s)), "")
        if concl:
            picked = concl if not quant else [quant] + concl[:1]
            return _trim(picked)

    # Unstructured: score every sentence and keep the best two, in reading order.
    sents = split_sentences(abstract)
    if not sents:
        return _trim([abstract])
    n = len(sents)
    scored = []
    for i, s in enumerate(sents):
        score = 0.0
        if _QUANT_RE.search(s):
            score += 2.0
        if _SIGNAL_RE.search(s):
            score += 2.0
        if _BACKGROUND_RE.search(s):
            score -= 2.5
        if i >= n - 2:            # conclusions live at the end
            score += 1.5
        elif i >= (2 * n) // 3:
            score += 0.75
        if i == 0:
            score += 0.25
        if len(s) < 40:
            score -= 1.0
        scored.append((score, i, s))
    best = sorted(scored, key=lambda t: (-t[0], t[1]))[:2]
    return _trim([s for _, _, s in sorted(best, key=lambda t: t[1])])


# ---------------------------------------------------------------------------
# Study-type classifier
# ---------------------------------------------------------------------------
# Arthroplasty research is fragmented across study designs that publish in
# different journals and answer different questions. Tagging each paper by
# design is what turns a feed into a map: it lets you filter to the two
# designs you actually care about, and it makes the aggregate shape of the
# literature visible (how much is registry work, how much is bench work).
#
# Rule-based on purpose — deterministic, offline, $0, and auditable
# (`--audit` shows which cue fired for every paper). Two evidence streams:
#
#   1. PubMed PublicationType tags — human-assigned by NLM indexers, so they
#      are the strongest signal, but they only exist once PubMed indexes the
#      paper (a few days late) and never for Crossref-only records.
#   2. Cue phrases in the title/abstract — always available, weaker per hit.
#      A cue in the title, or in the METHODS section of a structured
#      abstract, counts for more than the same cue buried anywhere else.

# Display order for chips/filters when counts tie; also the tie-break order
# when two classes score identically (earlier wins).
STUDY_TYPES = [
    {"key": "rct",       "label": "RCT",              "long": "Randomized controlled trial"},
    {"key": "sysrev",    "label": "Systematic review", "long": "Systematic review / meta-analysis"},
    {"key": "registry",  "label": "Registry",         "long": "Registry or administrative-database study"},
    {"key": "cohort",    "label": "Clinical cohort",  "long": "Observational clinical study"},
    {"key": "case",      "label": "Case report",      "long": "Case report or small case series"},
    {"key": "biomech",   "label": "Biomechanics",     "long": "Cadaveric, finite-element or implant-testing study"},
    {"key": "basic",     "label": "Basic science",    "long": "In vitro, animal or molecular study"},
    {"key": "ml",        "label": "ML / prediction",  "long": "Machine-learning or prediction-model study"},
    {"key": "technique", "label": "Technique",        "long": "Surgical technique or technical note"},
    {"key": "survey",    "label": "Survey / consensus", "long": "Survey, Delphi or consensus statement"},
    {"key": "biblio",    "label": "Bibliometric",     "long": "Bibliometric or research-trends analysis"},
    {"key": "review",    "label": "Review / editorial", "long": "Narrative review, editorial or commentary"},
    {"key": "other",     "label": "Unclassified",     "long": "No design cue found"},
]

STUDY_LABELS = {t["key"]: t["label"] for t in STUDY_TYPES}
_STUDY_ORDER = {t["key"]: i for i, t in enumerate(STUDY_TYPES)}

# NLM PublicationType → (class, weight). Lowercased on lookup.
PTYPE_SIGNALS = {
    "randomized controlled trial": ("rct", 7),
    "pragmatic clinical trial": ("rct", 5),
    "clinical trial, phase iii": ("rct", 5),
    "clinical trial": ("rct", 2),
    "equivalence trial": ("rct", 5),
    "meta-analysis": ("sysrev", 8),
    "systematic review": ("sysrev", 8),
    "case reports": ("case", 8),
    "observational study": ("cohort", 3),
    "comparative study": ("cohort", 2),
    "multicenter study": ("cohort", 2),
    "twin study": ("cohort", 2),
    "review": ("review", 4),
    "editorial": ("review", 7),
    "letter": ("review", 7),
    "comment": ("review", 6),
    "historical article": ("review", 4),
    "congress": ("review", 3),
    "practice guideline": ("survey", 4),
    "consensus development conference": ("survey", 6),
    "validation study": ("cohort", 1),
}

# (class, weight, pattern, strict). Weights are tuned so one strong cue (>=5)
# beats a pile of weak ones; `cohort` deliberately maxes out low so that a
# registry or trial paper full of ordinary clinical language still lands in its
# own class.
#
# `strict` marks phrases that papers routinely *mention* without *being*:
# "no randomized trials exist", "a recent systematic review found", "AI may
# soon…". Those count fully in the title or METHODS, and are heavily discounted
# anywhere else — which is where the passing mentions live.
STRICT_DISCOUNT = 0.35

_CUES = [
    ("sysrev", 6, r"systematic review|meta[- ]?analys[ie]s|network meta|pooled analysis|scoping review|umbrella review|evidence synthesis", True),
    ("sysrev", 3, r"\bprisma\b|\bprospero\b|random[- ]effects model|forest plot|pooled (odds|risk|mean)|studies were (included|screened)", False),

    ("rct", 6, r"randomi[sz]ed[ -]?(controlled |clinical |double[- ]blind |open[- ]label |)(pilot |feasibility |crossover |multicent(er|re) |)(trial|study)|\brct\b", True),
    ("rct", 4, r"randomly (assigned|allocated)|double[- ]blind|placebo[- ]controlled|allocation concealment|1:1 randomi|intention[- ]to[- ]treat", False),

    ("registry", 6, r"(national|regional|institutional) (joint |arthroplasty |)registr|joint replacement registr|\bnjr\b|aoanjrr|\bajrr\b|swedish (hip|knee|arthroplasty)|danish (hip|knee|arthroplasty)|norwegian arthroplasty|dutch arthroplasty|new zealand joint", False),
    ("registry", 5, r"\bnsqip\b|national inpatient sample|nationwide (readmissions|inpatient)|pearldiver|marketscan|trinetx|premier healthcare database|medicare (claims|beneficiar)|administrative claims|claims database|national surgical quality", False),
    ("registry", 3, r"\bregistry\b|national database|large (administrative |)database|database study", True),

    ("ml", 6, r"machine learning|deep learning|artificial intelligence|neural network|random forest|gradient boost|xgboost|convolutional|large language model|\bllm\b|chatgpt|\bgpt-?[0-9]|radiomics|computer vision|natural language processing", True),
    ("ml", 4, r"predicti(ve|on) model|nomogram|risk calculator|area under the (receiver|curve)|\bauroc\b|\bc[- ]statistic", True),

    ("biblio", 6, r"bibliometric|most[- ]cited|citation analysis|altmetric|h[- ]index|publication trends|research productivity|scientometric|authorship trends", False),

    ("case", 6, r"\bcase report\b|we (report|present|describe) (a|an|two|three|the) (rare |unusual |unique |novel |)case", False),
    # "Case series" is a Level-IV label journals also hang on 60-patient chart
    # reviews, so it stays weak — the cohort cues below should outrank it there.
    ("case", 2, r"\bcase series\b|case presentation|this case (illustrates|highlights)", False),

    ("biomech", 6, r"cadaver|finite element|biomechanical (study|test|evaluation|analy[sz]|compar)|wear simulat|load[- ]to[- ]failure|micromotion|sawbones|synthetic bone|implant retrieval|retrieval analysis|specimens were (tested|loaded|prepared)", False),
    ("biomech", 5, r"statistical shape model|computational model|\bin silico\b|simulation study|phantom (study|model)|bench(top)? test", True),
    ("biomech", 3, r"\bmpa\b|axial load|shear (force|strength)|pull[- ]out strength|stiffness was|\bnewton[- ]met|contact pressure", False),

    ("basic", 6, r"cell (culture|line|viability)|osteoblast|osteoclast|macrophage|fibroblast|murine|\brats?\b|\bmice\b|mouse model|rabbit model|ovine|biofilm|cytotox|gene expression|immunohistochem|western blot|rna[- ]seq|\belisa\b|antimicrobial activity|antibiotic elution", False),
    ("basic", 5, r"\bin vitro\b|canine|\bdogs?\b|veterinary", True),
    # PK work is usually bench/modelling, but "PK-guided dosing" trials are not —
    # weak enough that the clinical cues outrank it when the study is clinical.
    ("basic", 3, r"pharmacokinetic", True),

    ("technique", 5, r"surgical technique|technical note|technique (article|description|guide)|tips and tricks|step[- ]by[- ]step|we (describe|present) (a|our) (novel |modified |new |)(technique|approach)", True),

    ("survey", 5, r"\bdelphi\b|consensus statement|expert consensus|cross[- ]sectional survey|survey (was |were |)(distributed|sent|administered)|response rate of|survey respondents|we surveyed", False),

    # Editorial notices are decisive: a "CORR Insights®" piece commenting on a
    # systematic review must not be filed as one.
    ("review", 8, r"^(corrigendum|erratum|correction to|retraction)|corr insights|(author'?s? |)reply to the letter|response to the letter to the editor", False),
    ("review", 4, r"narrative review|current concepts|state of the art|review of the literature|this review (summari|discuss|examin)|letter to the editor|editorial comment|\bcommentary\b", True),

    ("cohort", 4, r"retrospective(ly)? (review|cohort|analy[sz]|stud|identif|examin|evaluat|assess)|were retrospectively|prospective(ly)? (cohort|study|series|observational|collected|enrolled|followed)|propensity[- ](score[- ])?match|matched (cohort|control)|consecutive patients|case[- ]control|chart review|medical records (of|were|from)", False),
    ("cohort", 3, r"(data|records|charts) of \d+ patients|\d+ patients (were|who|with|undergoing)|clinical data of|patients were (enrolled|included|identified|divided|allocated|followed)|we (included|identified|reviewed|analy[sz]ed|enrolled)\b", False),
    ("cohort", 2, r"patients (who |)underwent|minimum \d+[- ](year|month) follow|mean follow[- ]up|\bfollow[- ]up of \d|were included in the (study|analysis)|harris hip score|\bwomac\b|\bkss\b|oxford (knee|hip) score|patient[- ]reported outcome", False),
]

STUDY_CUES = [
    (key, weight, re.compile(pat, re.IGNORECASE), strict) for key, weight, pat, strict in _CUES
]

# Both halves must appear for the "it's clinical, they just never said so"
# fallback to fire: a countable group of humans, and an outcome measured on it.
_CLINICAL_FALLBACK_RE = re.compile(
    r"(?=.*\b(\d+\s+(patients|cases|hips|knees|shoulders)|patients|cohort)\b)"
    r"(?=.*\b(outcome|complication|revision|survivorship|follow[- ]up|mortality|readmission|score)s?\b)",
    re.IGNORECASE | re.DOTALL,
)

# Hit in the title, or in a structured abstract's METHODS block, is worth more
# than the same phrase anywhere in the body.
_TITLE_BOOST, _METHODS_BOOST = 1.7, 1.3

METHODS_KEYS = ("METHOD", "MATERIAL", "DESIGN", "PATIENTS AND")


# The tail of an abstract holds two very different things. "LEVEL OF EVIDENCE:
# III, retrospective cohort study" is the authors naming their own design — the
# best cue there is. What follows it is often a journal policy footer ("…
# manuscripts that concern Basic Science, Animal Studies, Cadaver Studies…"),
# whose words belong to the journal and fire cues for designs the paper isn't.
_LOE_RE = re.compile(r"\b(?:LEVEL[- ]\w+ )?LEVEL OF EVIDENCE\b[:\s.]*", re.IGNORECASE)
_POLICY_RE = re.compile(
    r"\b(This journal requires that authors assign a level of evidence"
    r"|For a full description of these Evidence[- ]?Based Medicine ratings"
    r"|see the Instructions? (to|for) Authors)\b",
    re.IGNORECASE,
)


def split_evidence(abstract: str) -> tuple[str, str]:
    """Return (body, level-of-evidence statement), both boilerplate-free."""
    loe = ""
    m = _LOE_RE.search(abstract)
    if m:
        loe, abstract = abstract[m.end():m.end() + 140], abstract[:m.start()]
    policy = _POLICY_RE.search(loe)
    if policy:
        loe = loe[:policy.start()]
    policy = _POLICY_RE.search(abstract)
    if policy:
        abstract = abstract[:policy.start()]
    return abstract.strip(), loe.strip()


def classify(paper: dict) -> dict:
    """Tag a paper with a study design, a confidence, and the cue that fired."""
    title = paper.get("title") or ""
    abstract, loe = split_evidence(paper.get("abstract") or "")
    sections = parse_sections(abstract) if abstract else {}
    # The level-of-evidence line is a design statement, so it carries the same
    # weight as METHODS rather than sitting in the discounted body text.
    methods = " ".join(filter(None, (_first_match(sections, METHODS_KEYS), loe)))

    scores: dict[str, float] = {}
    why: dict[str, str] = {}

    def bump(key: str, amount: float, evidence: str) -> None:
        if amount <= 0:
            return
        if amount > scores.get(key, 0.0):
            why[key] = evidence
        scores[key] = scores.get(key, 0.0) + amount

    for raw in paper.get("ptypes") or []:
        hit = PTYPE_SIGNALS.get(raw.strip().lower())
        if hit:
            bump(hit[0], float(hit[1]), f"PubMed tag: {raw}")

    for key, weight, rx, strict in STUDY_CUES:
        m = rx.search(title)
        boost = _TITLE_BOOST
        if not m and methods:
            m = rx.search(methods)
            boost = _METHODS_BOOST
        if not m:
            m = rx.search(abstract)
            boost = STRICT_DISCOUNT if strict else 1.0
        if m:
            bump(key, weight * boost, f'"{m.group(0).strip().lower()}"')

    if not scores:
        # Last resort: an abstract that counts patients and reports outcomes is
        # a clinical study even when it never names its own design — which is
        # most of the non-English-language and older-format literature.
        if abstract and _CLINICAL_FALLBACK_RE.search(abstract):
            return {
                "key": "cohort",
                "label": STUDY_LABELS["cohort"],
                "confidence": "low",
                "why": "no design stated — inferred from patient counts and outcomes",
            }
        return {
            "key": "other",
            "label": STUDY_LABELS["other"],
            "confidence": "low",
            "why": "no design cue in title or abstract",
        }

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], _STUDY_ORDER[kv[0]]))
    key, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    if top >= 6 and top - runner_up >= 2:
        conf = "high"
    elif top >= 3.5:
        conf = "medium"
    else:
        conf = "low"
    if not abstract:                    # title-only evidence is thin by nature
        conf = "low" if conf != "high" else "medium"

    return {"key": key, "label": STUDY_LABELS[key], "confidence": conf, "why": why.get(key, "")}


# ---------------------------------------------------------------------------
# DOI verification
# ---------------------------------------------------------------------------
# Not every DOI in the wild is registered. PubMed reproduces whatever string a
# journal supplied, and some — several Chinese-language journals in particular —
# publish DOIs that were never deposited with the global DOI system, so
# doi.org answers "DOI NOT FOUND". Checking is cheap against the handle API
# (no publisher involved, so nothing blocks us), and the verdict is cached
# permanently, because a DOI that resolves today will resolve tomorrow.

DOI_STATUS_PATH = os.path.join(CACHE_DIR, "doi-status.json")
DOI_RECHECK_AFTER = 7 * 24 * 3600   # a dead DOI may just be "not activated yet"
_doi_status: dict[str, dict] | None = None
_doi_lock = threading.Lock()


def _doi_status_all() -> dict[str, dict]:
    global _doi_status
    if _doi_status is None:
        try:
            with open(DOI_STATUS_PATH, encoding="utf-8") as fh:
                _doi_status = json.load(fh)
        except Exception:  # noqa: BLE001
            _doi_status = {}
    return _doi_status


def _doi_status_save() -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(DOI_STATUS_PATH, "w", encoding="utf-8") as fh:
            json.dump(_doi_status_all(), fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[doi] {exc}", file=sys.stderr)


def _check_doi(doi: str) -> bool | None:
    """True registered · False not in the DOI system · None couldn't tell."""
    url = f"https://doi.org/api/handles/{urllib.parse.quote(doi)}"
    try:
        code = json.loads(_get(url, timeout=15, retries=1)).get("responseCode")
        return True if code == 1 else False if code == 100 else None
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except Exception:  # noqa: BLE001
        return None


def verify_dois(papers: list[dict]) -> None:
    """Tag each paper with `doi_ok`, checking only DOIs we haven't seen."""
    status = _doi_status_all()
    now = time.time()
    todo = []
    for p in papers:
        doi = p.get("doi")
        if not doi:
            continue
        rec = status.get(doi)
        if rec and (rec.get("ok") or now - rec.get("t", 0) < DOI_RECHECK_AFTER):
            continue
        todo.append(doi)

    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for doi, ok in zip(todo, pool.map(_check_doi, todo)):
                if ok is not None:
                    with _doi_lock:
                        status[doi] = {"ok": ok, "t": now}
        _doi_status_save()

    for p in papers:
        doi = p.get("doi")
        rec = status.get(doi) if doi else None
        p["doi_ok"] = rec["ok"] if rec else None
        # A dead DOI must not be the link behind the title either.
        if p.get("doi_ok") is False and p.get("pmid") and "doi.org" in (p.get("url") or ""):
            p["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/"


def apply_classifier(feed: dict) -> dict:
    """(Re)tag every paper in a feed and refresh the per-type counts.

    Run on cached feeds too: classification is local and instant, so editing a
    cue takes effect on the next load without re-hitting Crossref or PubMed.
    """
    papers = feed.get("papers", [])
    for p in papers:
        p["study"] = classify(p)
        p["fields"] = classify_fields(p)
    # Re-apply the relevance gate too: a tightened cue has to be able to *drop*
    # a paper from a cached window, not just retag it.
    papers = [p for p in papers if is_relevant(p)]
    verify_dois(papers)
    feed["papers"] = papers
    feed["counts"] = {
        **feed.get("counts", {}),
        "total": len(papers),
        "with_abstract": sum(1 for p in papers if p.get("abstract")),
        "journals": len({p["journal"] for p in papers if p.get("journal")}),
    }
    feed["study_types"] = type_counts(papers)
    feed["fields"] = field_counts(papers)
    return feed


def type_counts(papers: list[dict]) -> list[dict]:
    """Per-class counts, biggest first — drives the design filter chips."""
    tally: dict[str, int] = {}
    for p in papers:
        tally[p.get("study", {}).get("key", "other")] = tally.get(p.get("study", {}).get("key", "other"), 0) + 1
    return [
        {"key": t["key"], "label": t["label"], "long": t["long"], "n": tally[t["key"]]}
        for t in sorted(STUDY_TYPES, key=lambda t: (-tally.get(t["key"], 0), _STUDY_ORDER[t["key"]]))
        if tally.get(t["key"])
    ]


def field_counts(papers: list[dict]) -> list[dict]:
    """Per-field counts in taxonomy order — drives the interests picker.

    Counts every field a paper carries, so they sum to more than the number of
    papers; a paper on revision shoulder arthroplasty is in both its fields.
    """
    tally: dict[str, int] = {}
    for p in papers:
        for key in p.get("fields", {}).get("all", []):
            tally[key] = tally.get(key, 0) + 1
    return [
        {"key": f["key"], "label": f["label"], "blurb": f["blurb"], "n": tally.get(f["key"], 0)}
        for f in FIELDS
    ]


def in_fields(paper: dict, selected: set[str]) -> bool:
    return not selected or bool(set(paper.get("fields", {}).get("all", [])) & selected)


# ---------------------------------------------------------------------------
# Source: Crossref
# ---------------------------------------------------------------------------

CROSSREF_SELECT = "DOI,title,container-title,ISSN,author,abstract,published,created,type,URL,issued"


# Crossref's filter names and its `sort` vocabulary are not the same words.
SORT_FOR_FILTER = {"created-date": "created", "online-pub-date": "published-online"}


def _crossref_query(start: str, end: str, date_field: str) -> list[dict]:
    """All works in the window, following Crossref's cursor until it runs dry.

    A single `rows=400` page covers a normal window, but a busy fortnight can
    run past it and the overflow vanishes without a word. Cursor paging is
    Crossref's supported way to walk past the row cap; `sort` is dropped because
    deep paging orders by relevance internally and the feed re-sorts by date.
    """
    issn_filter = ",".join(f"issn:{i}" for i in ALL_ISSNS)
    items: list[dict] = []
    cursor = "*"
    for _ in range(MAX_PAGES):
        params = {
            "filter": f"from-{date_field}:{start},until-{date_field}:{end},{issn_filter}",
            "rows": "500",
            "select": CROSSREF_SELECT,
            "cursor": cursor,
            "mailto": CONTACT,
        }
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        message = _get_json(url).get("message", {})
        page = message.get("items", [])
        items.extend(page)
        cursor = message.get("next-cursor") or ""
        if len(page) < 500 or not cursor:
            break
    else:
        print(f"[crossref:{date_field}] stopped at the {MAX_PAGES}-page cap", file=sys.stderr)
    return items


def fetch_crossref(start: str, end: str) -> list[dict]:
    """Query by DOI-creation date and by online-publication date, then merge.

    Publishers are inconsistent about which of the two reflects "it appeared
    today", so we ask for both and let the dedupe step sort it out.
    """
    items: list[dict] = []
    for field in ("created-date", "online-pub-date"):
        try:
            items.extend(_crossref_query(start, end, field))
        except Exception as exc:  # noqa: BLE001
            print(f"[crossref:{field}] {exc}", file=sys.stderr)

    papers = []
    for it in items:
        if it.get("type") in SKIP_TYPES:
            continue
        title = clean_text(" ".join(it.get("title") or []))
        if not title:
            continue
        journal = clean_text(" ".join(it.get("container-title") or []))
        issns = [s.strip() for s in (it.get("ISSN") or [])]
        authors = [
            clean_text(f"{a.get('given', '')} {a.get('family', '')}".strip())
            for a in (it.get("author") or [])
            if a.get("family") or a.get("given")
        ]
        papers.append(
            {
                "doi": (it.get("DOI") or "").lower(),
                "title": title,
                "journal": journal,
                "issns": issns,
                "authors": authors,
                "abstract": clean_text(it.get("abstract")),
                "date": _crossref_date(it),
                "type": it.get("type", ""),
                "url": it.get("URL") or (f"https://doi.org/{it.get('DOI')}" if it.get("DOI") else ""),
                "pmid": "",
                "sources": ["crossref"],
            }
        )
    return papers


def _crossref_date(item: dict) -> str:
    """Best 'this appeared on' date.

    Articles published online today routinely carry a *print issue* date months
    in the future ("published: 2026-10-01"). Taking that at face value sorts
    unread papers to the top of a "today" feed and mislabels them, so future
    dates lose to the DOI-registration date.
    """
    today = date.today()
    candidates: list[tuple[int, date]] = []
    for rank, key in enumerate(("published-online", "created", "published", "issued")):
        parts = (item.get(key) or {}).get("date-parts") or []
        if not (parts and parts[0] and parts[0][0]):
            continue
        y = parts[0][0]
        m = parts[0][1] if len(parts[0]) > 1 else 1
        d = parts[0][2] if len(parts[0]) > 2 else 1
        try:
            candidates.append((rank, date(int(y), int(m), int(d))))
        except (ValueError, TypeError):
            continue
    if not candidates:
        return ""
    past = [c for c in candidates if c[1] <= today]
    pick = min(past or candidates, key=lambda c: c[0])
    return pick[1].isoformat()


# ---------------------------------------------------------------------------
# Source: PubMed (E-utilities)
# ---------------------------------------------------------------------------

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _window_retmax(days: int) -> int:
    """Room for the window, with slack — ~90 arthroplasty records a day is a
    generous ceiling, and asking for more costs nothing but a bigger id list."""
    return max(300, min(MAX_PMIDS, days * 90))


def _pubmed_search(term: str, retmax: int = 300) -> list[str]:
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": str(retmax),
        "retmode": "json",
        "tool": "orthobrief",
        "email": CONTACT,
    }
    url = f"{EUTILS}/esearch.fcgi?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    return data.get("esearchresult", {}).get("idlist", []) or []


def _pubmed_fetch(pmids: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(pmids), 200):
        chunk = pmids[i : i + 200]
        params = {
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml",
            "tool": "orthobrief",
            "email": CONTACT,
        }
        url = f"{EUTILS}/efetch.fcgi?" + urllib.parse.urlencode(params)
        xml = _get(url, timeout=45)
        out.extend(_parse_pubmed_xml(xml))
        time.sleep(0.4)  # stay well under NCBI's 3 req/s cap
    return out


def _parse_pubmed_xml(raw: bytes) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        print(f"[pubmed] parse error: {exc}", file=sys.stderr)
        return []

    papers = []
    for art in root.findall(".//PubmedArticle"):
        med = art.find("MedlineCitation")
        if med is None:
            continue
        pmid = (med.findtext("PMID") or "").strip()
        article = med.find("Article")
        if article is None:
            continue

        title = clean_text("".join(article.find("ArticleTitle").itertext())) if article.find("ArticleTitle") is not None else ""
        if not title:
            continue

        journal_el = article.find("Journal")
        journal = ""
        issns = []
        if journal_el is not None:
            journal = (journal_el.findtext("ISOAbbreviation") or journal_el.findtext("Title") or "").strip()
            issn_el = journal_el.find("ISSN")
            if issn_el is not None and issn_el.text:
                issns.append(issn_el.text.strip())

        # Structured abstracts keep their section labels.
        chunks = []
        for node in article.findall(".//Abstract/AbstractText"):
            body = clean_text("".join(node.itertext()))
            if not body:
                continue
            label = (node.get("Label") or "").strip()
            chunks.append(f"{label.upper()}: {body}" if label else body)
        abstract = " ".join(chunks)

        authors = []
        for a in article.findall(".//AuthorList/Author"):
            last, fore = a.findtext("LastName"), a.findtext("ForeName")
            if last:
                authors.append(f"{fore} {last}".strip() if fore else last)
            elif a.findtext("CollectiveName"):
                authors.append(a.findtext("CollectiveName"))

        doi = ""
        for eid in art.findall(".//ArticleIdList/ArticleId"):
            if eid.get("IdType") == "doi" and eid.text:
                doi = eid.text.strip().lower()
                break

        ptypes = [p.text for p in article.findall(".//PublicationTypeList/PublicationType") if p.text]

        papers.append(
            {
                "doi": doi,
                "title": title,
                "journal": journal,
                "issns": issns,
                "authors": authors,
                "abstract": abstract,
                "date": _pubmed_date(art, article),
                "type": ptypes[0] if ptypes else "journal-article",
                "ptypes": ptypes,
                "url": f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "pmid": pmid,
                "sources": ["pubmed"],
            }
        )
    return papers


def _pubmed_date(art: ET.Element, article: ET.Element) -> str:
    adate = article.find("ArticleDate")
    if adate is not None:
        y, m, d = adate.findtext("Year"), adate.findtext("Month"), adate.findtext("Day")
        if y:
            try:
                return date(int(y), int(m or 1), int(d or 1)).isoformat()
            except ValueError:
                pass
    for status in ("entrez", "pubmed"):
        node = art.find(f".//PubMedPubDate[@PubStatus='{status}']")
        if node is not None:
            try:
                return date(
                    int(node.findtext("Year")), int(node.findtext("Month")), int(node.findtext("Day"))
                ).isoformat()
            except (TypeError, ValueError):
                continue
    return ""


# One topic query per field, so a relevant paper published outside the journal
# list still surfaces. MeSH terms do the heavy lifting; [tiab] catches papers
# PubMed hasn't finished indexing.
PUBMED_TOPICS = [
    '"Arthroplasty"[MeSH] OR arthroplasty[tiab] OR "joint replacement"[tiab] OR periprosthetic[tiab]',
    '"Athletic Injuries"[MeSH] OR "Anterior Cruciate Ligament"[MeSH] OR "Arthroscopy"[MeSH] '
    'OR "rotator cuff repair"[tiab] OR "return to sport"[tiab] OR meniscal[tiab]',
    '"Fractures, Bone"[MeSH] OR "Fracture Fixation"[MeSH] OR nonunion[tiab] OR "open fracture"[tiab]',
    '"Spinal Diseases"[MeSH] OR "Spinal Fusion"[MeSH] OR "Scoliosis"[MeSH] '
    'OR "lumbar fusion"[tiab] OR "cervical myelopathy"[tiab]',
    '"Hand Injuries"[MeSH] OR "Carpal Tunnel Syndrome"[MeSH] OR "distal radius fracture"[tiab] '
    'OR "scaphoid"[tiab] OR "flexor tendon"[tiab]',
    '"Shoulder Joint"[MeSH] OR "Rotator Cuff"[MeSH] OR "Elbow Joint"[MeSH] '
    'OR "shoulder instability"[tiab] OR epicondylitis[tiab]',
    '"Foot Diseases"[MeSH] OR "Ankle Injuries"[MeSH] OR "hallux valgus"[tiab] '
    'OR "achilles tendon"[tiab] OR "flatfoot"[tiab]',
    '"Orthopedic Procedures"[MeSH] AND ("Child"[MeSH] OR "Adolescent"[MeSH]) '
    'OR "slipped capital femoral epiphysis"[tiab] OR clubfoot[tiab] OR "supracondylar"[tiab]',
    '"Bone Neoplasms"[MeSH] OR "Sarcoma"[MeSH] OR "limb salvage"[tiab] '
    'OR "endoprosthetic reconstruction"[tiab]',
]


def fetch_pubmed(start: str, end: str, days: int = 1) -> list[dict]:
    s, e = start.replace("-", "/"), end.replace("-", "/")
    window = f'("{s}"[EDAT] : "{e}"[EDAT])'
    retmax = _window_retmax(days)

    journal_clause = " OR ".join(f'"{j["pm"]}"[Journal]' for j in JOURNALS)

    pmids: set[str] = set()
    terms = [f"({journal_clause}) AND {window}"]
    terms += [f"({clause}) AND {window}" for clause in PUBMED_TOPICS]
    for term in terms:
        try:
            pmids.update(_pubmed_search(term, retmax))
        except Exception as exc:  # noqa: BLE001
            print(f"[pubmed:search] {exc}", file=sys.stderr)
        time.sleep(0.4)

    if not pmids:
        return []
    try:
        return _pubmed_fetch(sorted(pmids))
    except Exception as exc:  # noqa: BLE001
        print(f"[pubmed:fetch] {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Merge, filter, rank
# ---------------------------------------------------------------------------

def is_relevant(paper: dict) -> bool:
    """Orthopaedic at all? Belonging to no field is what "no" now means.

    The topic queries reach outside the journal list on purpose, so they pull in
    plenty of neurology, rheumatology and general surgery along the way — this
    is where that goes back out.
    """
    return bool(paper.get("fields", {}).get("all"))


def merge(*groups: list[dict]) -> list[dict]:
    """Merge sources, preferring whichever record carries the richer abstract."""
    by_key: dict[str, dict] = {}
    for group in groups:
        for p in group:
            key = p["doi"] or f"title:{normalize_title(p['title'])}"
            if not key or key == "title:":
                continue
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = p
                continue
            # Merge: keep the longer abstract, fill in blanks, union sources.
            if len(p.get("abstract") or "") > len(existing.get("abstract") or ""):
                existing["abstract"] = p["abstract"]
            for field in ("doi", "pmid", "journal", "date", "url"):
                if not existing.get(field) and p.get(field):
                    existing[field] = p[field]
            if len(p.get("authors") or []) > len(existing.get("authors") or []):
                existing["authors"] = p["authors"]
            existing["issns"] = list({*existing.get("issns", []), *p.get("issns", [])})
            # PubMed's indexer tags are the classifier's best evidence; keep
            # them even when the Crossref copy won the merge.
            existing["ptypes"] = list({*existing.get("ptypes", []), *p.get("ptypes", [])})
            existing["sources"] = sorted({*existing["sources"], *p["sources"]})
    return list(by_key.values())


def build_feed(days: int = 1, force: bool = False) -> dict:
    end = date.today()
    start = end - timedelta(days=max(days, 1) - 1)
    s, e = start.isoformat(), end.isoformat()

    cached = _cache_read(days) if not force else None
    if cached:
        return apply_classifier(cached)

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cr = pool.submit(fetch_crossref, s, e)
        f_pm = pool.submit(fetch_pubmed, s, e, days)
        try:
            crossref = f_cr.result()
        except Exception as exc:  # noqa: BLE001
            crossref, _ = [], errors.append(f"Crossref unavailable: {exc}")
        try:
            pubmed = f_pm.result()
        except Exception as exc:  # noqa: BLE001
            pubmed, _ = [], errors.append(f"PubMed unavailable: {exc}")

    merged = merge(crossref, pubmed)
    for p in merged:
        p["fields"] = classify_fields(p)
    papers = [p for p in merged if is_relevant(p)]

    for p in papers:
        p["summary"] = summarize(p.get("abstract", ""), p.get("title", ""))
        p["authors_short"] = _authors_short(p.get("authors") or [])
        p["study"] = classify(p)
        # `ptypes` and `issns` stay on the record so a cached feed can be
        # re-classified after a cue edit without another round trip.

    verify_dois(papers)
    papers.sort(key=lambda p: (p.get("date") or "", p.get("journal") or ""), reverse=True)

    feed = {
        "version": FEED_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"days": days, "start": s, "end": e},
        "counts": {
            "total": len(papers),
            "with_abstract": sum(1 for p in papers if p.get("abstract")),
            "crossref": len(crossref),
            "pubmed": len(pubmed),
            "journals": len({p["journal"] for p in papers if p.get("journal")}),
        },
        "study_types": type_counts(papers),
        "fields": field_counts(papers),
        "errors": errors,
        "papers": papers,
    }
    _cache_write(days, feed)
    return feed


def select_fields(feed: dict, selected: set[str]) -> dict:
    """A view of the feed holding only the reader's fields.

    The cached feed always covers all of orthopaedics; narrowing happens here so
    that changing interests is instant and never re-queries anything. Counts are
    recomputed against the selection, but `fields` keeps every field's total so
    the picker can show what's behind the doors the reader hasn't opened.
    """
    if not selected or set(selected) >= set(FIELD_KEYS):
        return feed
    papers = [p for p in feed["papers"] if in_fields(p, selected)]
    out = dict(feed)
    out["papers"] = papers
    out["selected_fields"] = sorted(selected, key=lambda k: _FIELD_ORDER[k])
    out["counts"] = {
        **feed["counts"],
        "total": len(papers),
        "with_abstract": sum(1 for p in papers if p.get("abstract")),
        "journals": len({p["journal"] for p in papers if p.get("journal")}),
    }
    out["study_types"] = type_counts(papers)
    return out


def _authors_short(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) <= 3:
        return ", ".join(authors)
    return f"{authors[0]}, {authors[1]}, … {authors[-1]} (+{len(authors) - 3} more)"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(days: int) -> str:
    return os.path.join(CACHE_DIR, f"feed-{date.today().isoformat()}-{days}d.json")


def _cache_read(days: int) -> dict | None:
    path = _cache_path(days)
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
            with open(path, encoding="utf-8") as fh:
                feed = json.load(fh)
            # A feed cached by an older build may be missing fields the current
            # one needs (the classifier's PubMed tags, say) — refetch instead of
            # silently serving a degraded record.
            return feed if feed.get("version") == FEED_VERSION else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _cache_write(days: int, feed: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(days), "w", encoding="utf-8") as fh:
            json.dump(feed, fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[cache] {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

DATA_TOKEN = "/*__ORTHOBRIEF_DATA__*/null"
SNAPSHOT_TOKEN = "/*__ORTHOBRIEF_SNAPSHOT__*/null"
FIELDS_TOKEN = "/*__ORTHOBRIEF_FIELDS__*/[]"

SNAPSHOT_WINDOWS = (1, 3, 7, 14)


def _encode(obj) -> str:
    # `</` would otherwise close the inline <script> early.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render_page(feed: dict | None, snapshot: dict | None = None) -> bytes:
    """Render the page. `feed=None` sends the shell alone, for a cold cache —
    the browser paints straight away and fetches the papers itself."""
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as fh:
        shell = fh.read()
    shell = shell.replace(FIELDS_TOKEN, _encode(
        [{"key": f["key"], "label": f["label"], "blurb": f["blurb"]} for f in FIELDS]))
    if feed is not None:
        shell = shell.replace(DATA_TOKEN, _encode(feed))
    if snapshot:
        shell = shell.replace(SNAPSHOT_TOKEN, _encode(snapshot))
    return shell.encode("utf-8")


def _strip_abstracts(feed: dict) -> dict:
    """A copy of the feed carrying metadata but not the publishers' abstracts.

    Abstracts belong to the journals. Reading them locally is ordinary use;
    republishing thousands of them on a public URL is not, so the published
    build ships titles, tags, links and the one-or-two-sentence extractive
    summary, and sends readers to the source for the rest.
    """
    out = dict(feed)
    out["papers"] = [{**p, "abstract": ""} for p in feed.get("papers", [])]
    out["public"] = True
    return out


def write_snapshot(path: str, force: bool = False, public: bool = False) -> str:
    """Render a standalone file with every window pre-fetched.

    Needed because browsers that force HTTPS refuse to talk to a local HTTP
    server, but will happily open a file:// page.
    """
    feeds = {}
    for days in SNAPSHOT_WINDOWS:
        print(f"  fetching {days}-day window…", file=sys.stderr)
        feed = build_feed(days, force)
        feeds[str(days)] = _strip_abstracts(feed) if public else feed
    with open(path, "wb") as fh:
        fh.write(render_page(feeds["1"], feeds))
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = "OrthoBrief"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        days = max(1, min(int(qs.get("days", ["1"])[0] or 1), MAX_DAYS))
        force = qs.get("refresh", ["0"])[0] in ("1", "true", "yes")
        # `fields=` narrows the payload to the reader's subspecialties. One
        # cache holds every field; this filters on the way out, so switching
        # interests never costs a fetch.
        selected = {f for f in qs.get("fields", [""])[0].split(",") if f in FIELD_KEYS}

        try:
            if parsed.path == "/":
                # Building an all-of-orthopaedics window from cold takes tens of
                # seconds, and a browser shows nothing at all while it waits.
                # Send the shell now and let the page fetch its own papers.
                warm = _cache_read(days) if not force else None
                if warm is None:
                    threading.Thread(target=lambda: build_feed(days, force), daemon=True).start()
                    self._send(render_page(None), "text/html; charset=utf-8")
                else:
                    self._send(render_page(select_fields(apply_classifier(warm), selected)),
                               "text/html; charset=utf-8")
            elif parsed.path == "/api/papers":
                feed = select_fields(build_feed(days, force), selected)
                self._send(json.dumps(feed, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            elif parsed.path == "/healthz":
                self._send(b"ok", "text/plain")
            else:
                self._send(b"Not found", "text/plain", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._send(f"OrthoBrief error: {exc}".encode(), "text/plain", 500)


CERT_DIR = os.path.join(HERE, ".cert")
CERT_PEM = os.path.join(CERT_DIR, "cert.pem")
KEY_PEM = os.path.join(CERT_DIR, "key.pem")


def ensure_cert() -> str | None:
    """Self-signed cert for localhost, generated once via the system openssl.

    Safari (and Chrome's HTTPS-First) upgrade http://127.0.0.1 to https:// and
    will not be argued out of it, so the server has to be able to answer TLS.
    Returns None if openssl isn't available — HTTP still works in that case.
    """
    if os.path.exists(CERT_PEM) and os.path.exists(KEY_PEM):
        return CERT_PEM
    os.makedirs(CERT_DIR, exist_ok=True)
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", KEY_PEM, "-out", CERT_PEM, "-days", "825",
        "-subj", "/CN=localhost/O=OrthoBrief",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        os.chmod(KEY_PEM, 0o600)
        return CERT_PEM
    except Exception as exc:  # noqa: BLE001
        print(f"  (no HTTPS: could not generate certificate — {exc})", file=sys.stderr)
        return None


def _ssl_context() -> "ssl.SSLContext | None":
    if not ensure_cert():
        return None
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PEM, KEY_PEM)
        return ctx
    except Exception as exc:  # noqa: BLE001
        print(f"  (no HTTPS: {exc})", file=sys.stderr)
        return None


class DualProtocolServer(ThreadingHTTPServer):
    """Serves HTTP and HTTPS on one port by peeking at the first byte.

    0x16 is the TLS record type for a handshake; anything else is treated as
    plaintext HTTP. This means the same URL works whether or not the browser
    silently upgrades the scheme.
    """

    ssl_ctx: "ssl.SSLContext | None" = None
    daemon_threads = True

    def get_request(self):
        conn, addr = self.socket.accept()
        if self.ssl_ctx is not None:
            try:
                conn.settimeout(10)
                if conn.recv(1, socket.MSG_PEEK) == b"\x16":
                    conn = self.ssl_ctx.wrap_socket(conn, server_side=True)
                conn.settimeout(None)
            except Exception:  # noqa: BLE001 - failed handshake, drop it quietly
                try:
                    conn.close()
                finally:
                    raise BlockingIOError("tls handshake failed")
        return conn, addr

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BlockingIOError, ConnectionResetError, ssl.SSLError, BrokenPipeError)):
            return  # ordinary browser noise, not worth a traceback
        super().handle_error(request, client_address)


class _V6Server(DualProtocolServer):
    address_family = socket.AF_INET6


def serve() -> None:
    """Listen on both loopback stacks.

    Browsers resolve `localhost` to ::1 before 127.0.0.1, so an IPv4-only bind
    looks like "the site isn't working" even though the server is up. Binding
    both keeps us loopback-only (never exposed to the network) either way.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    ctx = _ssl_context()

    servers = []
    for host, cls in (("127.0.0.1", DualProtocolServer), ("::1", _V6Server)):
        try:
            srv = cls((host, PORT), Handler)
            srv.ssl_ctx = ctx
            servers.append(srv)
        except OSError as exc:
            if not servers:  # the first bind failing is fatal; the second is not
                sys.exit(f"\n  Could not bind {host}:{PORT} — {exc}\n"
                         f"  Something else may be using the port. Try --port 5061.\n")
            print(f"  (no IPv6 listener: {exc})", file=sys.stderr)

    print(f"\n  OrthoBrief → http://localhost:{PORT}")
    if ctx:
        print(f"             https://localhost:{PORT}  (self-signed — click through the warning once)")
    print(f"  {len(JOURNALS)} journals · Crossref + PubMed · Ctrl-C to stop\n")

    # Warm the cache in the background so the first page load is instant.
    threading.Thread(target=lambda: build_feed(1), daemon=True).start()
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        for s in servers:
            s.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def check_journals() -> None:
    """Report which ISSNs return nothing over the last 90 days (likely wrong)."""
    end = date.today()
    start = end - timedelta(days=90)
    print(f"Checking {len(JOURNALS)} ISSNs against Crossref ({start} → {end})…\n")
    bad = []
    for j in JOURNALS:
        params = {
            "filter": f"issn:{j['issn']},from-created-date:{start},until-created-date:{end}",
            "rows": "0",
            "mailto": CONTACT,
        }
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        try:
            n = _get_json(url).get("message", {}).get("total-results", 0)
        except Exception as exc:  # noqa: BLE001
            n, exc_note = -1, str(exc)
            print(f"  ??  {j['name']:<45} {j['issn']}  ({exc_note})")
            continue
        flag = "ok " if n > 0 else "ZERO"
        if n == 0:
            bad.append(j)
        print(f"  {flag} {j['name']:<45} {j['issn']}  {n:>6} papers/90d")
        time.sleep(0.2)
    if bad:
        print("\nZero-result ISSNs (fix in the JOURNALS table; PubMed still covers them):")
        for j in bad:
            print(f"  - {j['name']} → {j['issn']}")
    else:
        print("\nAll ISSNs resolve.")


def audit_types(days: int, force: bool = False, verbose: bool = False) -> None:
    """Print every paper's assigned study type and the cue that decided it.

    The classifier is rules, so the only way to know it's right is to read its
    work. Scan the `low` rows first — that's where the misses live.
    """
    feed = build_feed(days, force)
    papers = feed["papers"]
    print(f"\n{len(papers)} papers · {feed['window']['start']} → {feed['window']['end']}\n")

    print("Fields (a paper can be in more than one)")
    for row in feed["fields"]:
        share = 100 * row["n"] / max(len(papers), 1)
        print(f"  {row['label']:<24} {row['n']:>4}  {share:>4.0f}%  {'█' * round(share / 2.5)}")

    print("\nDesigns")
    for row in feed["study_types"]:
        share = 100 * row["n"] / max(len(papers), 1)
        bar = "█" * round(share / 2.5)
        print(f"  {row['label']:<24} {row['n']:>4}  {share:>4.0f}%  {bar}")

    conf = {"high": 0, "medium": 0, "low": 0}
    for p in papers:
        conf[p["study"]["confidence"]] += 1
    print(f"\nConfidence   high {conf['high']} · medium {conf['medium']} · low {conf['low']}")

    order = {"low": 0, "medium": 1, "high": 2}
    rows = sorted(papers, key=lambda p: (order[p["study"]["confidence"]], p["study"]["key"]))
    if not verbose:
        rows = [p for p in rows if p["study"]["confidence"] != "high"]
        print("\nEverything below high confidence (--verbose for all):\n")
    else:
        print()
    for p in rows:
        s = p["study"]
        print(f"  [{s['confidence']:<6}] {s['label']:<20} {p['title'][:78]}")
        print(f"           {'+'.join(p['fields']['all']):<24} cue: {s['why'] or '—'}"
              f"{'' if p.get('abstract') else '   (no abstract)'}")


def main() -> None:
    global PORT

    ap = argparse.ArgumentParser(description="OrthoBrief — today's orthopaedic papers")
    ap.add_argument("--json", action="store_true", help="print the feed as JSON and exit")
    ap.add_argument("--days", type=int, default=1, help="window size in days (default: today only)")
    ap.add_argument("--refresh", action="store_true", help="bypass the cache")
    ap.add_argument("--check", action="store_true", help="validate the journal ISSN table")
    ap.add_argument("--audit", action="store_true",
                    help="print each paper's study type and the cue that fired")
    ap.add_argument("-v", "--verbose", action="store_true", help="with --audit: show every paper")
    ap.add_argument("--snapshot", nargs="?", const=os.path.join(HERE, "orthobrief.html"),
                    metavar="PATH", help="write a standalone offline HTML file and exit")
    ap.add_argument("--public", action="store_true",
                    help="with --snapshot: omit publishers' abstracts (for a published build)")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    if args.check:
        check_journals()
        return
    if args.audit:
        audit_types(args.days, args.refresh, args.verbose)
        return
    if args.snapshot:
        os.makedirs(os.path.dirname(os.path.abspath(args.snapshot)) or ".", exist_ok=True)
        path = write_snapshot(args.snapshot, args.refresh, args.public)
        size = os.path.getsize(path) / 1024
        print(f"\n  Wrote {path} ({size:.0f} KB)\n  Open it directly — no server needed.\n")
        return
    if args.json:
        print(json.dumps(build_feed(args.days, args.refresh), indent=2, ensure_ascii=False))
        return

    PORT = args.port
    serve()


if __name__ == "__main__":
    main()

# OrthoBrief

Today's orthopaedic literature, in one page — filtered to the parts you follow.

```
python3 app.py          # → http://localhost:8087
```

No dependencies. No API keys. Python 3.9+ standard library only.

---

## What it does

Every load, OrthoBrief queries two APIs for papers that appeared in the selected
window, merges them, keeps the orthopaedic ones, tags each with its
subspecialty and study design, and renders it with its DOI, full abstract, and a
short summary.

| Source | Why it's here |
| --- | --- |
| **Crossref REST API** | DOIs are registered the day an article goes online — this is the fastest possible signal that a paper exists. Queried twice, by DOI-creation date *and* online-publication date, because publishers disagree about which one means "today". |
| **PubMed E-utilities** | Indexes a few days slower, but carries clean structured abstracts (`BACKGROUND: … METHODS: … CONCLUSIONS: …`) that Crossref often lacks. |

Records are merged on DOI (falling back to a normalized title), keeping whichever
copy has the richer abstract. Each paper shows which source(s) it came from.

### Why APIs instead of scraping

Publisher sites (Elsevier, Wolters Kluwer, Springer) sit behind Cloudflare, block
non-browser agents, and change their markup constantly — a scraper against them
is broken every few weeks. Crossref and PubMed are the canonical feeds those
pages are rendered *from*, they're free, unauthenticated, and stable. Same
papers, on the same day, without the fragility.

## Fields

Orthopaedics is not one literature. It is ten, each with its own journals and
its own questions, so every paper is tagged with the subspecialties it belongs
to and you choose which ones you follow:

**Arthroplasty · Sports medicine · Trauma · Spine · Hand & upper extremity ·
Shoulder & elbow · Foot & ankle · Pediatrics · MSK oncology · General ortho**

The split follows fellowship lines rather than anatomy, because that's how
journals, departments and training are organised — a revision TKA paper is
arthroplasty, not "knee", and doesn't belong in the same bucket as an ACL
reconstruction.

A paper can be in more than one field, and often is: revision shoulder
arthroplasty is genuinely both. Field counts therefore sum to more than the
number of papers.

Tagging works the same way as the study designs — a journal dedicated to a field
settles it outright (everything in *Foot & Ankle International* is foot and
ankle), otherwise weighted cues in the title and abstract decide. A field needs
one solid cue to be claimed, so a passing mention of "children" doesn't make a
paper paediatric. Papers from *outside* orthopaedics have to announce themselves
in the title, which is what keeps a vascular limb-salvage series or a
maxillofacial fracture paper out of the feed.

The **Fields** button opens the picker; the choice is saved in `localStorage`, so
it's per-device and needs no account. Everything is on until you narrow it.

## Coverage

63 journals across the ten fields — the dedicated ones (*J Arthroplasty*, *AJSM*,
*JOT*, *Spine*, *J Hand Surg*, *JSES*, *FAI*, *JPO*, …) plus the generalists that
publish across all of it (*JBJS*, *BJJ*, *CORR*, *Acta Orthop*, *JAAOS*, …). See
the `JOURNALS` table in `app.py`; `fields` marks what a journal is dedicated to.

Papers from a dedicated journal are always kept and tagged with its field.
Papers from a general journal are tagged by cue. A per-field PubMed topic query
also runs each load, so a relevant paper published *outside* the journal list
still surfaces.

## Study-type tags

Every paper is tagged with its research design, because "what's new in
orthopaedics" is really a dozen different literatures — a cadaver study, a
registry analysis and an RCT answer different questions and are worth different
amounts of your attention. The chip row under the toolbar filters by design and
doubles as a read of the window's shape: *41% clinical cohort, 12% systematic
review, 3% RCT* is the field in one line.

Thirteen classes: RCT, systematic review / meta-analysis, registry, clinical
cohort, case report, biomechanics, basic science, ML / prediction, technique,
survey / consensus, bibliometric, review / editorial, unclassified.

Rule-based, like the summaries — deterministic, offline, $0, and auditable.
Two evidence streams:

1. **NLM publication types** from PubMed (`Randomized Controlled Trial`,
   `Case Reports`, `Meta-Analysis`, …). Human-assigned, so they carry the most
   weight — but they only exist once PubMed indexes the paper, and never for
   Crossref-only records.
2. **Cue phrases** in the title and abstract, weighted. A cue in the title, in
   the METHODS section, or in the `LEVEL OF EVIDENCE` line counts for more than
   the same phrase in the body — because papers constantly *mention* designs
   they aren't ("no randomized trials exist", "a recent systematic review
   found"). Cues marked `strict` in `_CUES` are heavily discounted outside those
   zones, which is what keeps every meta-analysis's inclusion criteria from
   being read as an RCT.

Each tag carries a confidence and the exact cue that fired; hover it in the UI.
Low-confidence tags render dashed with a trailing `?`. A clinical paper that
never names its own design falls back to *clinical cohort* if it counts patients
and reports outcomes, which is most of the non-English-language literature.

```bash
python3 app.py --audit --days 14         # distribution + every non-high-confidence call
python3 app.py --audit --days 14 -v      # ...every call
```

`--audit` is how you tune it: read the misses, edit `_CUES`, re-run. Classification
happens on cached feeds too, so a cue edit takes effect without re-querying the
APIs.

## The summaries

Extractive, not generated — deterministic, offline, and $0.

- **Structured abstract** → pulls the `CONCLUSIONS` section, prepended with the
  single most quantitative sentence from `RESULTS` (one carrying a %, p-value,
  CI, or n).
- **Unstructured abstract** → scores every sentence (numbers and result language
  score up; "the purpose of this study was…" scores down; late sentences get a
  positional bonus), keeps the best two in reading order.

They're labelled *Summary* in the UI and capped at ~340 characters. Nothing is
paraphrased, so nothing is hallucinated — but they're a triage aid, not a
substitute for the abstract, which is one click away on every card.

To swap in an LLM instead, replace `summarize()` in `app.py`; it takes
`(abstract, title)` and returns a string. Everything else is unchanged.

## Using it

- **Window pills** — Today / 3 / 7 / 14 days. Journals publish in bursts, so a
  quiet Sunday is normal; widen the window rather than assuming it's broken.
- **Design chips** — filter to one study type; click again to clear. Counts
  respect the search box and journal filter, so they always describe what you're
  actually looking at.
- **`/`** focuses the search box. Filters across title, authors, abstract, and
  study type.
- **All / New / Saved** — *New* is what wasn't in the feed the last time you
  looked, so a return visit shows a delta rather than the same window again.
  *Saved* is your own shelf: papers stay there after they drop out of the feed,
  and export as **RIS** or **BibTeX** for Zotero.
- **Fields** — pick the subspecialties you follow; counts show what's in the
  current window. Saved per device.
- **Journal dropdown** — counts per journal for the current window.
- **Refresh** — bypasses the 30-minute cache and re-queries both APIs.
- **Appearance** (footer) — Auto follows your system setting; Light and Dark
  override it. Saved per device, and applied before first paint so an explicit
  choice never flashes the other theme on the way in.

Filters live in the popover rather than on a settings page on purpose: which
fields and designs you're looking at is working state, not a preference. The
counts next to them (`Arthroplasty 30`) describe the *current window*, which is
context a separate page would lose.

## Other entry points

```bash
python3 app.py --json --days 3      # feed as JSON on stdout (cron / piping)
python3 app.py --refresh            # bypass cache on startup
python3 app.py --port 8080          # different port
python3 app.py --check              # validate the journal ISSN table
python3 app.py --audit --days 14    # spot-check the study-type classifier
```

`--check` counts each ISSN's Crossref output over the last 90 days and flags any
returning zero — those are typos or ISSN changes worth fixing in the `JOURNALS`
table. Worth running once. A wrong ISSN degrades gracefully rather than
failing: PubMed still covers that journal by name.

`GET /api/papers?days=N&fields=spine,trauma&refresh=1` returns the same JSON the
page uses. `fields` narrows the payload server-side for API and cron use; the
page itself loads every field and filters in the browser, so toggling a field is
instant and never re-queries anything.

## Onboarding and tailoring

First visit asks three skippable questions — what you do, which fields, anything
you're chasing — and the answers drive a **For you** ordering. Type a project or
technique ("periprosthetic joint infection", "robotic") and matching papers rise.

Two rules the ranking keeps:

- **It ranks, it never hides.** Every paper is still in the list; only the order
  changes. Missing a paper is the one failure a literature tool can't afford, so
  personalisation is not allowed to become a filter.
- **It says why.** Each ranked paper carries its reason in words — *Matches
  Spine · "pedicle screw"* — so you can tell whether the ranking is working
  instead of trusting it.

It also learns, quietly: saving a paper adds weight to its field, journal and
study design; opening an abstract adds less; un-saving subtracts. Weights are
capped so no single habit can run away with the feed, and everything lives in
one `orthobrief.profile` object you can inspect or delete. No model, no service,
no data leaving the device — the same rules-not-magic approach as the
classifiers. **Edit your interests** in the footer reopens the questions.

## Reading state

Two things are remembered on the device, in `localStorage`, with no account:

- **Seen** — every paper id the feed has shown you, so "New" means new *to you*
  rather than new to the world. Keyed on DOI (falling back to PMID, then title)
  so a paper keeps its identity across windows and refetches; entries older than
  60 days are pruned.
- **Saved** — the papers you starred, stored with enough metadata (authors,
  journal, date, DOI, PMID) to render and to cite even when they're long gone
  from the current window.

Nothing is uploaded anywhere; clearing site data resets both.

## Notes

- Responses cache to `.cache/` for 30 minutes, keyed by date and window.
- Crossref is queried on the polite pool via the `mailto` parameter; PubMed calls
  are throttled below NCBI's 3 req/s limit. Set `ORTHOBRIEF_EMAIL` to change the
  contact address.
- If one API is down, the other still renders and a banner explains what's
  missing — the page never fails closed.
- Papers with no abstract are still listed (the DOI exists before the metadata
  lands); the card says so explicitly.
- **Every DOI is checked against the DOI registry** before it becomes a link.
  Some journals publish DOI strings that were never deposited with the global
  DOI system — PubMed reproduces them faithfully, and doi.org answers "DOI NOT
  FOUND". Those render struck through and marked *unregistered*, with the card
  linking to PubMed instead. Verdicts cache permanently in
  `.cache/doi-status.json` (a failed DOI is re-checked after a week, in case it
  was merely not activated yet), so the check costs nothing after the first run.
  Currently ~1.7% of papers, almost all from one journal.

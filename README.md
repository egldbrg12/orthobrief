# OrthoBrief

Today's orthopaedic literature, in one page — filtered to the parts you follow.

**Live: https://egldbrg12.github.io/orthobrief/** — rebuilt every morning at
07:00 US Eastern by GitHub Actions.

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
copy has the richer abstract. Which API a paper arrived from is our business,
not the reader's, so the card no longer says — it's still in the JSON (`sources`)
for anyone piping the feed.

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
Shoulder & elbow · Foot & ankle · Paediatrics · MSK oncology · General ortho**

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

**Veterinary papers are dropped outright**, on the journal. The topic queries ask
PubMed about fractures and arthroplasty without saying "in people", so they used
to return tibial fractures in miniature donkeys and the radiological anatomy of
the southern giant anteater's shoulder — eleven papers in a fortnight. A second
rule catches veterinary work in journals whose names don't say so, but only for
companion and exotic species: sheep, goats, pigs and calves are the standard
large-animal models and tissue sources for *human* work, so naming one says
nothing about who the patient was.

Animal *models* stay. A rat model of tendon healing in *AJSM* is orthopaedic
research and belongs in an orthopaedic feed — roughly 4% of a fortnight, most of
it already tagged **Basic science** or **Biomechanics**, which is how you skip it
if you want to.

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

Twelve classes: RCT, systematic review / meta-analysis, clinical cohort, case
report, biomechanics, basic science, ML / prediction, technique, survey /
consensus, bibliometric, review / editorial, unclassified.

Registry studies used to be their own class and are now part of **clinical
cohort**: a national joint registry paper is an observational cohort whose data
came from a registry rather than a chart pull, and splitting them mostly split
the evidence — a paper carrying both kinds of cue scored half in each and
cleared the confidence bar in neither. Merging them moved 13 registry papers
into cohort and, more usefully, gave 25 previously untagged papers a confident
one.

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

When no cue fires at all, the card shows **no design tag** rather than an
"Unclassified?" chip — reporting our own miss on every uncertain paper is noise,
and absence is the honest signal.

## How accurate is it

NLM's indexers are the only ground truth available, and the classifier uses
their tags — so to measure the cue reader on its own, the tags are suppressed
and it is asked to reproduce the human label. That is also the day-one case the
feeds depend on, weeks before a paper is indexed at all.

On a 14-day window, 155 papers carried an unambiguous NLM design tag:

| | |
| --- | --- |
| Assigns a design | 81% |
| Correct when it does | **85%** |
| High-confidence calls | **93%** correct |
| Medium | 57% |
| Low | 50% |

**Only high-confidence calls are shown.** A medium call was right little more
than half the time, and a literature tool that is wrong about what kind of study
something is teaches people to stop believing the tag at all — which costs more
than the coverage is worth. Below the bar the card shows nothing, the design
chips don't offer it, and it never reaches a feed. That takes tagged papers from
88% of a window to 56%; the withheld calls are mostly *Clinical cohort*. The
full call survives as `study_raw`, and `--audit` marks the withheld ones, so the
bar can be tuned without flying blind.

Two limits worth stating. The graded set is 75 case reports and 39 editorials,
because those are what NLM tags unambiguously — cohort studies, the bulk of the
feed, are almost never gradeable this way, so **the accuracy of a cohort tag is
not measured by this**. And in production any indexed paper also gets the human
tag as evidence, so the tags on older papers are better than the blind number.

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
- **Structured, but with no conclusion** — a trial protocol, a case report, a
  narrative review — → takes `DISCUSSION`, `EXPERT OPINION`, `CLINICAL
  RELEVANCE` or `OBSERVATIONS`, then `RESULTS`, in that order. It used to fall
  straight through to sentence scoring, and since that rewards whatever comes
  last, a protocol's summary was its ethics statement: *"This study received
  approval from the French Committee of Person Protection North-West II."* A
  protocol now leads with what the trial is testing, which is the only news it
  has.
- **Unstructured abstract** → scores every sentence (numbers and result language
  score up; "the purpose of this study was…" and registration numbers score
  down; late sentences get a positional bonus), keeps the best two in reading
  order. Trailing `LEVEL OF EVIDENCE:` and `TRIAL REGISTRATION:` boilerplate is
  cut first, because in an otherwise unstructured abstract it isn't a section —
  it's just the last sentence, where the summariser is looking.

Sections that are administration rather than findings — ethics, registration,
funding, data availability, conflicts — are never summary material, and
`SUMMARY` only counts as a conclusion when that is the whole label: *Spine*
calls its background `SUMMARY OF BACKGROUND DATA`, and reading that as the
finding inverts the paper.

Measured over a 14-day window (1168 abstracts): section labels leaking into the
summary 13 → 0, administrative text leading the summary 2 → 0, no summary made
worse, and no change to any study-design or subspecialty tag.

They're labelled *Summary* in the UI and capped at ~340 characters. Nothing is
paraphrased, so nothing is hallucinated — but they're a triage aid, not a
substitute for the abstract, which is one click away on every card.

To swap in an LLM instead, replace `summarize()` in `app.py`; it takes
`(abstract, title)` and returns a string. Everything else is unchanged.

## Using it

- **The header is the monogram and the promise, nothing else.** The wordmark is
  gone: an OB built from long bones, then *Your daily orthopaedic updates*. The
  mark stands without a tile, because it carries four bones and eight condyles
  per letter and a tile spends a third of the available size on padding before
  the drawing starts — inside one at 26px the B's outer bones fell below a pixel
  and read as clipped. The name still appears in the page title, the share card
  and every feed.
- **The title leads the card.** Only the study-design tag sits above it — that's
  the one thing on the card a reader can't get from PubMed. Journal,
  subspecialty and date follow the authors in a single grey line, because above
  the title they competed with it for the same glance. An untagged paper that
  isn't new drops the row above the title entirely rather than leaving a gap.
- **On a phone the controls collapse into one menu.** The desktop toolbar
  doesn't shrink, it wraps, which used to stack six rows and a third of the
  screen above the first paper. Below 620px there is a single bar — logo, search,
  ☰ — with All/Unread/Saved on the row beneath it, and everything else that
  narrows the feed lives in a bottom sheet: the window, the subspecialties and
  the study designs, which were always the same idea split across a popover and
  two pill rows for no reason but desktop space. The first paper starts about
  60px down instead of 250.

  The ☰ is three little bones, traced from the same artwork as the monogram and
  stacked with a `<use>` so the path is stored once. At 22px the knuckles are
  barely a pixel each, which is the intent: it reads as a menu first, and
  rewards a second look.

  Two decisions worth keeping. The ☰ carries a **badge counting the narrowings
  in force**, because hiding controls is only honest if the reader can see that
  something is hidden. And *which papers* — All/Unread/Saved — stays on screen
  rather than going in the sheet: Unread is the reason to come back, and a mode
  nobody can see is a mode nobody uses. The sheet slides up rather than
  appearing: `hidden` is `display:none` and display can't be transitioned, so
  open state is a class driving a transform, with visibility delayed by the
  animation on the way out so a shut sheet stays out of the tab order. It closes
  on Done, on the scrim, on Escape and on a downward swipe, and honours
  `prefers-reduced-motion`.

  The controls are **moved, not duplicated**: one set of nodes, one set of
  handlers, relocated by a `matchMedia` listener and moved back above the
  breakpoint. A second copy would drift the moment either changed.
- **The card footer loses its printed DOI and PMID on a phone.**
  `@media (hover:none)` means it's always open on touch, and
  two lines of monospace identifiers on every card is not what a phone reader
  came for. The buttons stay, Copy DOI among them, and an *unregistered* DOI
  still shows, because that one is a warning rather than an identifier.
- **Window pills** — Today / 1 week / 2 weeks / 1 month. Journals publish in bursts, so a
  quiet Sunday is normal; widen the window rather than assuming it's broken.
- **A window is when a paper reached the indexes, not when it was published.**
  Crossref registers a DOI the day a paper goes online, but PubMed adds records
  days to months later — so *Today* routinely holds papers published weeks ago,
  and on one day carried a February paper indexed 184 days after publication.
  That is deliberate: filtering on publication date instead would drop a
  late-indexed paper into a window you have already read past, and you would
  never see it at all. But dating a February paper "today" is a lie, so a paper
  that predates the window it sits in shows `published Feb 4, 2026` rather than
  a bare date, and the count line says how many arrived that way.
- **Design chips** — filter to one study type; click again to clear. Counts
  respect the search box and journal filter, so they always describe what you're
  actually looking at.
- **Actions appear on the paper you stop at.** Save (★), Share, Not interested
  (⊘) and Copy DOI fade in on hover, so scanning forty papers isn't forty rows
  of controls. On touch, where there's no hover, they stay visible. They're
  inline SVG — no icon font, no sprite request, and `currentColor` means they
  follow the theme — with an `aria-label` and tooltip on each, because an icon
  alone is a guess.
- **`/`** focuses the search box, which says *Search OrthoBrief…* rather than
  listing what it matches. It still searches title, authors, abstract, journal
  and study type — but a placeholder is read by someone deciding whether to
  bother, not by someone who wants the field list.
- **All / Unread / Saved** decide *which* papers are listed. *Unread* is what
  this page hasn't shown you before, so a return visit is a delta rather than
  the same window again — it's about you, not about publication date. *Saved*
  is your own shelf: papers stay after they drop out of the feed, and export as
  **RIS** or **BibTeX** for Zotero.
- **Ordered by newest / what matches you** decides the *order* of whichever list
  you're in. Sorting by match is not a filter — the count doesn't change, and
  the line says so. It used to be a fourth tab called "For you", which made a
  re-ordering look like a different set of papers.
- **Fields** — pick the subspecialties you follow; counts show what's in the
  current window. Saved per device.
- **Journal dropdown** — counts per journal for the current window.
- **More** — appearance, Refresh, Edit your interests and Subscribe. These used
  to sit in the footer, which meant scrolling past a hundred papers to change
  the theme. The header is sticky, so they hang off it now: a popover beside
  Filters on a desktop, the last group in the sheet on a phone. The subscribe
  panel moved with them, or tapping Subscribe would have sent you to the footer
  you were trying to avoid.
- **Refresh** — bypasses the 30-minute cache and re-queries both APIs.
- **Appearance** (footer) — Auto follows your system setting; Light and Dark
  override it. Saved per device, and applied before first paint so an explicit
  choice never flashes the other theme on the way in.

Filters live in the popover rather than on a settings page on purpose: which
fields and designs you're looking at is working state, not a preference. The
counts next to them (`Arthroplasty 30`) describe the *current window*, which is
context a separate page would lose.

## Adding it to a home screen

`apple-touch-icon.png` (180px), `icon-192.png`, `icon-512.png` and a
`manifest.webmanifest` are written next to the page on every build, so adding
OrthoBrief to a phone's home screen gives the bone monogram rather than a
screenshot of the page.

They are drawn, not exported. iOS ignores an SVG for `apple-touch-icon` and
won't take a data URI, so a real PNG at a real URL is required — and rather than
add a dependency to make one, note that the monogram is polygons: the tracer
that produced it emits only moves and lines. A supersampled scanline fill and a
zlib stream are the entire renderer, about eighty lines, and it takes 0.1s for
the 180px icon. The geometry is read out of `template.html` at build time, so
the icon cannot drift from the mark in the header.

No rounded corners on purpose: iOS masks the icon to its own shape, and rounding
it here would show as a dark ring inside that mask. The workflow refuses to
publish if any icon is missing, isn't a PNG, or is the wrong size.

## Standing queries (RSS)

The one thing this classifier can do that a PubMed alert cannot: say *an RCT
appeared in shoulder arthroplasty* on the day the DOI registers. NLM assigns
publication types weeks later; the cue classifier reads the abstract
immediately. That is worth nothing while it sits on a page someone has to
remember to open, so every subspecialty × design pair is also a feed:

```
feeds/all.xml                       everything, newest 100
feeds/evidence.xml                  RCTs and systematic reviews, all fields
feeds/design/rct.xml                one design, all fields          (13 of these)
feeds/field/shoulder.xml            one field, any design           (10)
feeds/field/shoulder-evidence.xml   one field, RCTs and SRs         (10)
feeds/field/shoulder-rct.xml        one field, one design           (130)
```

165 files, ~4 MB, written by the same daily Actions run that builds the page —
from the 14-day window it has already fetched, so they cost no extra calls to
Crossref or NCBI. **Subscribe to this search** in the footer composes the URL
for you and opens pre-filled with whatever you're currently looking at.

**Opened in a browser, a feed is a readable page**, not a wall of angle
brackets. `feeds/feed.xsl` is an XSLT stylesheet the browser applies and every
feed reader ignores, so the same URL is both a subscribable feed and a page
worth sending someone: what the query is, the address to paste into a reader,
and the papers it currently holds. Items therefore carry their journal, design,
summary and authors twice — once as HTML in `description` for readers, once as
plain `ob:` elements for the stylesheet, because rendering the former would
need `disable-output-escaping`, which Firefox has never supported.

Most of them are empty most days, and that is the point of a standing query:
nothing arrives until the thing you asked about appears. Items carry the title,
journal, design tag, fields, authors and the extractive summary, and link to the
DOI — or to PubMed when the DOI was never registered. GUIDs are stable across
rebuilds, so a daily build doesn't re-notify anyone about papers they've seen.

No accounts, no server, no email infrastructure, nothing added to the dependency
list — static XML in a reader the user already checks. The dev server generates
the same feeds on demand at `/feeds/…`, so a query can be tested locally exactly
as it will be published.

## Other entry points

```bash
python3 app.py --json --days 3      # feed as JSON on stdout (cron / piping)
python3 app.py --refresh            # bypass cache on startup
python3 app.py --port 8080          # different port
python3 app.py --check              # validate the journal ISSN table
python3 app.py --audit --days 14    # spot-check the study-type classifier
```

The published build writes the month beside the page as `windows/30.json` and
fetches it when the pill is pressed. A month is ~2,700 papers, and embedding it
took the page from 3 MB to 6 MB — paid on first load by everyone, including the
reader who never leaves Today. Split out, the page is 2.7 MB and the month costs
3.1 MB only if asked for. The offline `--snapshot` keeps everything inline,
because it's opened from `file://`, where a browser won't let a page fetch its
neighbour.

`--check` counts each ISSN's Crossref output over the last 90 days and flags any
returning zero — those are typos or ISSN changes worth fixing in the `JOURNALS`
table. Worth running once. A wrong ISSN degrades gracefully rather than
failing: PubMed still covers that journal by name.

```bash
python3 tools/phone.py shot.png          # the page at 390px, as a phone renders it
python3 tools/phone.py --measure         # anything wider than the viewport, worst first
python3 tools/phone.py s.png --css=x.css # preview a CSS change before writing it
```

`tools/phone.py` exists because Chrome will not open a window narrower than
500px: a screenshot taken at 390 is really a 500px render with the right-hand
side cropped, which reads as a broken layout that isn't broken — a mistake that
cost an hour once already. It loads the page in an iframe of the width being
tested instead, which is a real viewport, so media queries fire and genuine
overflow is genuinely visible. `--css` and `--js` inject a variant, which is how
a design change gets looked at before it is written into the template.

`GET /api/papers?days=N&fields=spine,trauma&refresh=1` returns the same JSON the
page uses. `fields` narrows the payload server-side for API and cron use; the
page itself loads every field and filters in the browser, so toggling a field is
instant and never re-queries anything.

## Onboarding and tailoring

First visit asks three skippable questions — what you do, which fields, anything
you're chasing — and the answers drive a **For you** ordering.

Pick **Student researcher** and you get a fourth: *who do you work with*. Search
your PI, and OrthoBrief reads their recent PubMed record and starts your feed
from their work — their subspecialties become your fields, the phrases that
recur in their titles become your interests. Everything is editable on the next
screen; it's a starting point, not a verdict.

Two details make that step work rather than merely exist:

- **It takes a name however you write it.** "Young-Min Kwon", "young min kwon"
  and "Kwon YM" all resolve to the same person, honorifics are stripped, and the
  author's surname is recognised wherever it sits in what you typed — the first
  version assumed surname-first and quietly filed all 41 of his MGH papers under
  "Affiliation not listed".
- **Disambiguation by affiliation.** "Bedair H" is an MGH arthroplasty surgeon
  *and* a clinical pathologist at Tanta University. Papers are grouped by where
  that author worked, so you pick a person, not a name. Affiliation strings are
  normalised first — one person writes "Columbia University", "Columbia
  University Medical Center" and "Columbia University Irving…", which would
  otherwise arrive as three different people.
- **The lab's papers go through the feed's own field cues.** The classifier's
  patterns are injected into the page from `app.py`, so a PI's back catalogue is
  read by exactly the same rules as this morning's papers — one source of truth,
  not a second implementation that drifts.

Interests come from two places, and both are pre-selected on the last screen so
you can start reading rather than filling in a form. **Recurring phrases in
their titles** give the specifics; **their papers' MeSH headings** give the
topics a librarian would have assigned. MeSH needs two corrections to be usable:
the generic headings (*Humans*, *Male*, *Retrospective Studies*) say nothing
about a lab, and an inverted heading like *Arthroplasty, Replacement, Knee*
appears in no abstract — so it would rank nothing. Only terms the papers
themselves actually write are kept, and the ones that don't fit are offered as
one-click suggestions instead of being forced on you.

Words that are useful inside a phrase but meaningless alone are dropped from the
final picks: *knee arthroplasty* is an interest, *knee* is the whole field.
Phrases are collapsed against each other too (otherwise "minimal clinically important difference" arrives as three
fragments plus its own words) and the vocabulary every clinical paper shares
filtered out. Real examples: *Kocher M → anterior cruciate, osteochondritis
dissecans, ligament reconstruction*; *Lenke L → adult spinal, proximal
junctional, deformity*.

PubMed rather than OpenAlex, deliberately: the published page is a static file,
so every visitor's search leaves their own browser. OpenAlex now meters usage
and there'd be no shared cache to absorb it; E-utilities are free, unmetered and
CORS-enabled. They do cap one IP at three requests a second — a whole hospital
shares one address — so the lookup backs off and retries, and says so plainly if
it still can't get through. Type a project or
technique ("periprosthetic joint infection", "robotic") and matching papers rise.

Two rules the ranking keeps:

- **It ranks, it never hides.** Every paper is still in the list; only the order
  changes. Missing a paper is the one failure a literature tool can't afford, so
  personalisation is not allowed to become a filter.
- **It says why.** Each ranked paper carries its reason in words — *Matches
  Spine · "pedicle screw"* — so you can tell whether the ranking is working
  instead of trusting it.

It also learns, quietly: saving a paper adds weight to its field, journal and
study design; opening an abstract adds less; un-saving subtracts. **Not
interested** is the explicit counterweight — it moves the same three signals
down and collapses the paper to a single undoable line. Set aside rather than
deleted: you chose to hide it, so you can change your mind, and in *For you* a
dismissed paper sinks to the bottom rather than vanishing. Weights are
capped so no single habit can run away with the feed, and everything lives in
one `orthobrief.profile` object you can inspect or delete. No model, no service,
no data leaving the device — the same rules-not-magic approach as the
classifiers. **Edit your interests** in the footer reopens the questions.

## Sharing

**Share** on any paper draws a card — title, what kind of study it is, the
finding, journal and DOI — on a `<canvas>` at 1200×630, and offers it as an
image to save or copy, alongside plain-text forms: a chat-shaped summary, a
citation, or just the link. On a phone it hands the image to the system share
sheet.

A bare link loses the two things that made the paper worth sending: what kind of
study it was, and what it found. The card carries both, and still says where it
came from, so it stays checkable rather than becoming a screenshot with no
provenance. Everything is drawn locally — no image service, no tracking, works
offline like the rest of the page.

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

## Publishing

`.github/workflows/publish.yml` rebuilds the page daily and deploys it to GitHub
Pages. It runs `--snapshot public/index.html --public --refresh`, then refuses to
publish if the build came back with fewer than 20 papers or with template tokens
left unfilled — a quiet failure would be worse than a missing day.

The published build omits publishers' abstracts (`--public`). Reading them
locally is ordinary use; republishing thousands of them is not, so the public
page carries titles, tags, the extractive summary and a link to the source.
Your local build still has everything.

`ORTHOBRIEF_EMAIL` is a repository *secret* rather than a variable, so the
contact address is redacted from the public build logs.

**One gotcha:** GitHub disables scheduled workflows after 60 days without
repository activity. If the feed stops updating, that's why — any commit, or a
manual run from the Actions tab, restarts the clock.

## Notes

- **The page paints one screenful, not the window.** Fourteen days of
  orthopaedics is well over a thousand papers, and the list used to be rebuilt in
  full on every keystroke, chip and tab. Three things fixed it: the search
  haystack and the match score are each derived once per paper instead of once
  per comparison (`textOf`, `SCORES`), `inScope()` sorts once per render rather
  than three times, and the feed paints 40 cards plus an `IntersectionObserver`
  sentinel that asks for the next 40 as you approach. On a 612-paper window
  sorted by match that is 69 ms of JavaScript per keystroke down to 1.7 ms, and
  ~1.7 MB of HTML per render down to ~110 KB. The painted depth survives a
  re-render of the *same* list, so dismissing a paper 400 deep doesn't return
  you to the top, and the swap is a single write so the page never collapses to
  nothing and takes your scroll position with it.
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

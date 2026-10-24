# JP-BS-Bench

A benchmark of consolidated balance sheets from real Japanese annual reports (有価証券報告書), with
ground truth built automatically from each filing's XBRL. It is the planned test bed for Kenzan's
central claim: accounting identities can decide, without labels, which extracted pages may skip human
review, and can guide extraction.

**Status:** the tooling is built and tested. One real filing (Nintendo, S100Y9NX, dev) has been
processed and independently verified ([RESULTS.md §4](RESULTS.md#4-real-document-results-jp-bs-bench-extractor)).
The full set isn't collected yet.

## 1. Design

| Aspect | Decision | Why |
|---|---|---|
| Source | EDINET 有価証券報告書, fiscal years ending 2025–2026; one filing per company | public, born-digital, every filing ships inline XBRL |
| Exclusions | banks (銀行業), insurers (保険業), securities firms (unless `--keep-securities`); unlisted filers; filers without consolidated statements; US GAAP | different statement formats; each exclusion is logged with its reason |
| Strata (API sampler) | IFRS ≥ 60 (cap 70), 千円 ≥ 50, one-page ≥ 40, two-page ≥ 60, ≤ 20 per industry; target 200 | covers the formats that break parsers without letting one stratum dominate |
| Split | by **company** (EDINET code), salted sha256, 70% test; companies in `bench/forced_dev.txt` always dev | one company's filings share a layout, so a document-level split would leak; anything inspected during development can never be test |
| Lock | `bench/split_spec.json` and `bench/FREEZE_LOG.txt`; locked before any document existed, amended once (forced-dev list) before any was seen | the test set must not be chosen after looking at results |

## 2. Ground truth from XBRL

`bench/build_gt_xbrl.py` builds ground truth from the filing's **inline-XBRL table** (the HTML the
PDF is rendered from), never from a parser of the PDF:

| Field | Source |
|---|---|
| rows, labels, order | the balance-sheet `<tr>` rows as filed |
| label / level | first non-empty cell; level = number of leading empty columns (EDINET indents with colspan), CSS padding as tie-breaker |
| values | printed cell text, sign as printed (△ → negative); magnitude from the tagged fact's own text, so note markers like 「※1 65,180」 can't fuse into the number; |fact| must equal printed × 10^scale |
| footnotes | note markers split out of label and value cells as refs |
| calc parent | the company's XBRL calculation linkbase (consolidated balance-sheet role) |
| statement checks | the same identity registry as the pipeline |

**XBRL conventions.** These are recorded per cell and counted; the page as printed stays the truth:

- 「－」 can be tagged as an explicit zero (`fixed-zero`), as `xsi:nil`, or not at all. It is always
  nil in the ground truth.
- △ values can be stored negative (`sign="-"`) or positive, with the subtraction carried in the
  calculation weight instead.

**Alignment with the PDF.** A filing is kept if ≥ 95% of its rows align. A row aligns when its label
is found in the page text, in order, and every printed value is found in the page's numeric-token
sequence. Only rows with values move the label search forward, because a header label such as
流動資産 also occurs inside a later 流動資産合計. This matcher is independent of the parser.

Filings whose text layer fails the quality gate are kept in an **unverifiable** bucket, not dropped,
so the filter can't remove exactly the pages that are hard for the text-layer route.

## 3. Collecting filings

`bench/fetch_edinet.py` needs only `requests`, `pymupdf` and `lxml` (`bench/requirements-fetch.txt`).

### Local mode: hand-downloaded filings, no API key (current path)

For each company, download two files from the public EDINET site (書類検索):

- the **XBRL zip**, which contains `XbrlSearchDlInfo.csv` and `S100XXXX/XBRL/PublicDoc/`;
- the **annual-report PDF**.

Name each PDF `<docID>.pdf`, or put each pair in its own folder. Then:

```bash
python bench/fetch_edinet.py --local downloads/ --out jpbs_dev --pilot
python bench/fetch_edinet.py --local downloads/ --out jpbs_dev --pilot --allow-missing-pdf   # XBRL first
```

- The extraction, page finding, end-total check and outputs are the same as in the API path.
- `--pilot` writes `forced_dev.txt`, so every hand-picked company goes to dev.
- Runs resume, and one bad filing never stops a run.
- Each filing gets a `bs_rows.csv` (label, level, values, notes, element) for comparison with the PDF.

### Checking a run

```bash
python bench/check_pilot.py jpbs_dev.zip report.md
python bench/build_gt_xbrl.py jpbs_dev gt_out      # ground truth + alignment report (gt_report.csv)
python bench/split.py force-dev jpbs_dev/forced_dev.txt
python bench/split.py annotate jpbs_dev/manifest.csv
```

`check_pilot.py` reports, per filing:

- how the balance sheet was found (heading or fallback);
- whether `bs_pages.pdf` holds exactly the balance-sheet pages: title on the first page, total on the
  last, no other statement inside, no page without balance-sheet values;
- facts vs printed numbers;
- alignment, and cells per XBRL convention.

For the whole run it reports exclusion and error reasons, and pilot → dev status.

### API mode (optional, needs an EDINET API v2 key)

EDINET API key issuance failed at its final confirmation step in several browsers, so collection
currently uses local mode. The API path is kept for later use:

```bash
export EDINET_API_KEY=...        # read from the environment only; never printed or written
python bench/fetch_edinet.py --out jpbs_raw --list-only                         # candidate pool sizes
python bench/fetch_edinet.py --out jpbs_raw --prioritize tekiyou.pdf,shinki.pdf   # stratified sample
```

- **Listing.** Lists submissions per day (`documents.json`), cached. Keeps one 有価証券報告書 per
  listed company with consolidated statements.
- **Sampling.** Visits candidates in a seeded order to fill the strata. `--prioritize` reads JPX's
  IFRS-adopter PDFs; `--max-ifrs` caps IFRS.
- **Robustness.** Logs are written incrementally. A restart resumes and refuses changed selection
  arguments. Any exception in one filing is logged and the run continues.
- **Key safety.** Output is scrubbed of the key, and the run refuses to finish if the key appears in
  any file.
- **Colab.** Write `--out` to Google Drive so a disconnect doesn't lose the run. Step-by-step
  instructions: `bench/PHASE_A_INSTRUCTIONS.md`.

## 4. Outputs per filing

`filings/<docID>/` contains:

- `bs_pages.pdf` (balance-sheet pages only)
- `ixbrl_bs.json` (rows and every tagged fact)
- `bs_rows.csv`
- `contexts.json`, `dei.json`
- `cal_bs.json` / `pre_bs.json` (linkbase arcs)
- `meta.json`

Each run also writes `manifest.csv`, `candidates_log.csv`, `exclusions_summary.json` and
`run_info.json`.

The benchmark release will contain the scripts, document IDs and metadata, not the filings
themselves. The filings are rebuilt from EDINET.

## 5. Evaluation plan

Routes to compare:

- text layer;
- OCR;
- VLM with N = 1, best-of-3 and best-of-5 reads, selected by the verifier;
- hybrid: text layer when the gate passes, else VLM with the verifier.

**Headline metric:** automation rate at zero false accepts, plus a coverage–accuracy curve. Also cell
F1, structure accuracy, sign recovery, cost and latency per page, and a failure taxonomy.

**Measurement rules:**

- Report the upper confidence bound on the false-accept rate, not just "0 observed". With 0 errors
  in *n* accepted pages, the 95% bound is about 3/n.
- Count **plausible-but-wrong** reads: reads that pass every check but disagree with XBRL.
  Verifier-guided selection optimizes exactly the acceptance criterion, so this is the main risk.
  One mitigation is to select on one subset of identities and accept on another.
- EDINET PDFs are born-digital, so scan-path claims need a rasterized track of the same test pages.
  Its degradation spec must be locked before development.

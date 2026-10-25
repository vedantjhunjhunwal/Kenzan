# Real-PDF evaluation: what to download

All files come from the public EDINET site (書類検索, no key needed). For each filing, download:

- the **XBRL zip** (keep EDINET's file name);
- the **有価証券報告書 PDF**, saved as `<docID>.pdf`. The docID (書類管理番号, e.g. `S100Y9NX`) is shown
  in the search results and in the PDF's URL.

Put everything in one folder, e.g. `downloads/`. Don't open the files for anything beyond saving them.

## The filings

The split comes from `python bench/split.py which <EDINET code>`, which reads nothing but the code.
The docIDs come from the EDINET source links on edinetdb.jp, not from EDINET itself. Check each one
in EDINET's search results when you download; triage reads the real docID from the zip anyway.

| # | docID | Company (EDINET code) | Standard | Unit | BS pages | Split | Why |
|---|---|---|---|---|---|---|---|
| 1 | **S100Y9NX** | 任天堂 Nintendo (E02367) | J-GAAP (XBRL) | 百万円 (XBRL) | unknown: PDF not in hand | dev (forced) | XBRL already processed; PDF needed |
| 2 | **S100Y992** | 味の素 Ajinomoto (E00436), FY 2026-03 | IFRS* | unknown | unknown | dev (hash) | drives the IFRS template work |
| 3 | **S100YB5L** | 武田薬品工業 Takeda (E00919), FY 2026-03 | IFRS* | not looked up | not looked up | **test** | IFRS in test |
| 4 | **S100XT6G** | 花王 Kao (E00883), FY 2025-12 | IFRS* | not looked up | not looked up | **test** | IFRS in test, December year-end |
| 5 | not verified† | 中外製薬 Chugai (E00932), FY 2025-12 | IFRS* | not looked up | not looked up | **test** | third IFRS filer |
| 6 | **S100YI72** | 電業社機械製作所 Dengyosha (E01544), FY 2026-03 | J-GAAP expected | not looked up | not looked up | **test** | small-cap machinery: likely 千円 and 減価償却累計額 rows |
| 7 | not verified† | 東京自働機械製作所 Tokyo Automatic Machinery (E01534), FY 2026-03 | J-GAAP expected | not looked up | not looked up | **test** | same |
| 8 | not verified† | 東京機械製作所 Tokyo Kikai (E01539), FY 2026-03 | J-GAAP expected | not looked up | not looked up | **test** | same |

\* IFRS adopter status is public company information. It is confirmed from each filing's XBRL at
triage.

† edinetdb.jp only showed an older filing for this company. Take the latest 有価証券報告書 from
EDINET's search.

**Why unit and page count are blank for the test rows.** The locked split rule
(`bench/split_spec.json`, amendment 1.1) sends any filing inspected before the test freeze to dev.
Opening a test filing to read its unit or count its balance-sheet pages would therefore move it to
dev. Instead, the extractor (`bench/fetch_edinet.py --local`) will read both automatically, and
only that metadata will be recorded before the single evaluation, without anyone viewing the pages.
`triage.py` doesn't do this yet; it is the next thing to wire in. For the dev rows the values aren't known
yet either: Ajinomoto's PDF couldn't be read from here, and Nintendo's PDF isn't in hand.

**Coverage is not guaranteed in advance.** IFRS is covered in test three times. Whether test also
gets a 千円 filing, a two-page balance sheet, and liabilities/equity on its own page will only be
known after triage, and is reported as found. If one of those is missing, more filings are chosen
by split only, without opening them.

## What happens next (on my side)

```bash
python bench/triage.py downloads/ realdocs      # dev -> realdocs/dev, test -> realdocs/sealed (unopened, hashed)
```

1. **Dev filings** (Nintendo, Ajinomoto, and any of 3–8 that split to dev) are used to build what's
   missing: cross-page stitching and the IFRS template. They are fair game for debugging.
2. The code is then **frozen** and its fingerprint logged in `bench/FREEZE_LOG.txt`.
3. The **sealed test filings** are evaluated once: parser end to end vs XBRL ground truth, reporting
   cell F1, calc-parent accuracy, accept/review, and every failure with its cause.

A test filing that breaks the frozen code is reported as a failure. It is not fixed and re-run.

# Real-PDF evaluation: what to download

All files come from the public EDINET site (書類検索, no key needed). For each filing, download:

- the **XBRL zip** (keep EDINET's file name);
- the **有価証券報告書 PDF**, saved as `<docID>.pdf`. The docID (書類管理番号, e.g. `S100Y9NX`) is shown
  in the search results and in the PDF's URL.

Put everything in one folder, e.g. `downloads/`. Don't open the files for anything beyond saving them.

## The filings

| # | Company | EDINET code | Document | Why |
|---|---|---|---|---|
| 1 | 任天堂 (Nintendo) | E02367 | 有価証券報告書, FY ending 2026-03-31 (docID **S100Y9NX**): **PDF**; the XBRL zip is already in hand | J-GAAP, 百万円; balance sheet expected over two pages with the liabilities/equity side on its own page; already **dev** (forced) |
| 2 | 味の素 (Ajinomoto) | E00436 | 有価証券報告書, FY ending 2026-03-31: XBRL zip + PDF | **IFRS** (連結財政状態計算書); splits to **dev** by hash, so it can drive the IFRS template work |
| 3–4 | two more **IFRS** filers of your choice | — | latest 有価証券報告書 (FY ending 2025-12 or 2026-03): XBRL zip + PDF | an IFRS filing in the **test** split (two picks give about a 91% chance at least one lands in test) |
| 5–8 | four **J-GAAP small caps**: TSE Standard or Growth, manufacturing (機械 / 金属製品 / 化学), FY ending 2026-03 | — | 有価証券報告書: XBRL zip + PDF | small filers usually report in **千円**, and manufacturers usually print **減価償却累計額** rows (the contra-stored-positive case still untested on real data) |

Choose 3–8 without looking inside the filings beyond picking them from the search list. Their
properties (IFRS / unit / pages / contra rows) are read from the XBRL by the extractor after triage.
The coverage actually achieved is reported, not assumed.

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

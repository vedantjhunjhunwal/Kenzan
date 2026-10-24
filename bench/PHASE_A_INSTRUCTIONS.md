# JP-BS-Bench, Phase A: collecting filings

## 0. Local mode: hand-downloaded filings, no API key (current path)

For each company, download two files from the public EDINET site (書類検索):

- the **XBRL zip**: the 「XBRL」 download, e.g. `Xbrl_Search_20261025_032117.zip`, which contains
  `XbrlSearchDlInfo.csv` and `S100XXXX/XBRL/PublicDoc/...`;
- the **annual-report PDF** (有価証券報告書).

Put them in one folder. Either name each PDF `<docID>.pdf` (for example `S100Y9NX.pdf`, the
書類管理番号), or put each zip + PDF pair in its own subfolder; any file names work then. Run:

```bash
pip install requests pymupdf lxml
python fetch_edinet.py --local downloads/ --out jpbs_dev --pilot              # all hand-picked filings -> dev
python fetch_edinet.py --local downloads/ --out jpbs_dev --pilot --allow-missing-pdf   # XBRL first, PDFs later
```

- No key and no download; the extraction, page finding, end-total check and outputs are the same
  as in the API path.
- `--pilot` writes `forced_dev.txt`, which forces every one of these companies into **dev**.
- `--allow-missing-pdf` keeps filings whose PDF hasn't been added yet, as `xbrl_only`. Re-running
  with the same `--out` skips filings already processed. To pick up PDFs added later, use a new
  `--out`.
- Each filing gets `bs_rows.csv`: label, indent level (leading columns), printed values with note
  markers split out, and the XBRL element. Use it to compare against the PDF.
- Upload the resulting `jpbs_dev.zip`. I run `check_pilot.py` and the ground-truth builder on it.

Sections 1–7 describe the **API mode**, kept as optional tooling for when an EDINET API key is available.

# API mode (optional)

`fetch_edinet.py` runs on a machine that can reach EDINET (Colab or your own machine). It needs an
EDINET API v2 key in the environment variable `EDINET_API_KEY`. The key is never printed or written
to disk: logs and errors are scrubbed, and the run refuses to finish if the key shows up in any
output file.

**Run the pilot first and upload `jpbs_pilot.zip`.** The extractor was tested on synthetic filings
modelled on EDINET's inline-XBRL layout, not on real EDINET files. Ten real filings check the
parsing before the long run.

---

## 1. Colab setup (every session)

Write everything to **Google Drive**. Colab disconnects on long runs and wipes session storage,
while the run on Drive resumes where it stopped.

```python
# cell 1: mount Drive and install (upload fetch_edinet.py + requirements-fetch.txt to the Files panel)
from google.colab import drive
drive.mount("/content/drive")
OUT = "/content/drive/MyDrive/jpbs"            # all runs live here
!mkdir -p "{OUT}"
!pip -q install -r requirements-fetch.txt

# cell 2: the key, typed and never echoed (re-run after every reconnect)
import os, getpass
os.environ["EDINET_API_KEY"] = getpass.getpass("EDINET API key: ")
```

## 2. Pilot: exact commands

```python
# cell 3: PILOT, 10 filings, no IFRS list (IFRS filers appear naturally, ~8% of the pool).
# The first run lists ~600 days of submissions (~15 min); lists are cached on Drive.
!python fetch_edinet.py --out "{OUT}/jpbs_pilot" --pilot \
    --target 10 --min-ifrs 2 --max-ifrs 4 --min-thousand-yen 3 --min-one-page 2 --min-two-page 3

# cell 4: download the result
from google.colab import files
files.download(f"{OUT}/jpbs_pilot.zip")
```

`--pilot` writes `forced_dev.txt` and marks the manifest rows `pilot=True`. When you upload, I merge
that list into `bench/forced_dev.txt`, so the split puts these companies in **dev**, never test.

**If the session disconnects:** run cells 1–2 again, then run cell 3 unchanged. The run reloads
`candidates_log.csv` and `manifest.csv` from Drive, skips every docID already processed, and
continues with the next candidate. The arguments must stay the same; the script refuses to resume
otherwise and says which argument changed.

## 3. Full run (after I confirm the pilot)

**With an IFRS list (recommended).** Download JPX's two IFRS lists from
https://www.jpx.co.jp/equities/improvements/ifrs/02.html (IFRSを適用している会社一覧 = `tekiyou.pdf`,
IFRSを適用して新規上場した会社一覧 = `shinki.pdf`) and upload them to Colab. The script reads the
securities codes straight from the PDFs.

```python
# cell 5: reuse the pilot's cached daily lists and code list, so the full run doesn't list again
!mkdir -p "{OUT}/jpbs_raw/_cache" && cp -rn "{OUT}/jpbs_pilot/_cache/lists" "{OUT}/jpbs_raw/_cache/" \
    && cp -n "{OUT}/jpbs_pilot/_cache/codelist.bin" "{OUT}/jpbs_raw/_cache/"

# cell 6: pool sizes first (seconds), then the run (~1-2 h; resumable)
!python fetch_edinet.py --out "{OUT}/jpbs_raw" --prioritize tekiyou.pdf,shinki.pdf --list-only
!python fetch_edinet.py --out "{OUT}/jpbs_raw" --prioritize tekiyou.pdf,shinki.pdf
files.download(f"{OUT}/jpbs_raw.zip")
```

`--prioritize` only changes the order candidates are visited in. The quotas still decide, and
`--max-ifrs` (default 70) stops IFRS filers from dominating the sample. Codes are read with a loose
pattern, so a stray number in the PDF can only move a non-IFRS filer earlier in the queue. That's
harmless, because the standard is always read from the filing's own XBRL.

**Without an IFRS list,** pass `--min-ifrs 40`. IFRS filers are about 8% of the pool (JPX lists
317 adopting or deciding companies as of September 2026), so 40 IFRS filings take about 500
candidate visits, and 60 would take about 750. The run skips the PDF download for filings that
can't fill an open quota, but each visit still costs one XBRL download. The default
`--max-candidates 1500` allows for either.

The pilot's companies may be picked again in the full run, since the visiting order is seeded. That's
expected: they stay in dev.

## 4. Local machine (alternative)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-fetch.txt
read -s EDINET_API_KEY && export EDINET_API_KEY
.venv/bin/python fetch_edinet.py --out jpbs_pilot --pilot \
    --target 10 --min-ifrs 2 --max-ifrs 4 --min-thousand-yen 3 --min-one-page 2 --min-two-page 3
# interrupted? run the same command again
```

## 5. What the run does

| Behaviour | Detail |
|---|---|
| One bad filing never stops the run | Any exception while processing a filing is logged in `candidates_log.csv` (`decision=error`, exception type + message, key scrubbed) and the run continues. Only a failure to list a submission day stops it, because the candidate pool must be complete; re-running resumes from the cache. |
| Incremental, resumable | `candidates_log.csv` and `manifest.csv` get one row per filing as it's processed, synced to disk. A restart rebuilds the quota counts from `manifest.csv` and skips logged docIDs. `run_state.json` stores the selection arguments and the end date, so a restart on a later day sees the same candidate pool. |
| End-of-range check | The page range must end on the page showing the liabilities + equity total. If the XBRL total value isn't on that page, the filing is excluded (`bs_end_total_not_on_page`). If no total value was found in the XBRL, it's kept and flagged `unknown`. Both counts are in `exclusions_summary.json`. |
| Exclusions | Banks (銀行業) and insurers (保険業) by the code list's industry (XBRL industry code as a second check); securities firms unless `--keep-securities`; unlisted filers; filers without consolidated statements; US-GAAP filers; older filings of the same company; balance sheet not found in the XBRL or the PDF. Each excluded filing is logged with its reason. |

## 6. Options

| Option | Default | Meaning |
|---|---|---|
| `--start` / `--end` | 2025-03-01 / yesterday (stored on the first run) | submission dates scanned |
| `--years` | 2025,2026 | year of `periodEnd` kept |
| `--target` | 200 | filings to accept |
| `--min-ifrs` / `--max-ifrs` | 60 / 70 | IFRS floor and cap |
| `--min-thousand-yen` | 50 | minimum filings in 千円 |
| `--min-one-page` / `--min-two-page` | 40 / 60 | minimum one- / two-page balance sheets |
| `--industry-cap` | 20 | at most this many per industry |
| `--max-candidates` | 1500 | stop after this many processed |
| `--prioritize` | none | codes visited first (.txt/.csv/.pdf, comma-separated) |
| `--pilot` | off | write `forced_dev.txt`; mark rows `pilot=True` |
| `--keep-raw` | off | keep full XBRL zips and PDFs (several GB) |
| `--force-resume-args` | off | resume despite changed selection arguments (not recommended) |

## 7. What to upload

`jpbs_pilot.zip` now, then `jpbs_raw.zip`. Each holds:

- `manifest.csv`, `candidates_log.csv`, `exclusions_summary.json`, `pool_summary.json`,
  `run_state.json`, `run_info.json`, `excluded_industry.csv` (and `forced_dev.txt` for the pilot);
- `filings/<docID>/` with:
  - `bs_pages.pdf` (balance-sheet pages only)
  - `ixbrl_bs.json`, `contexts.json`, `dei.json`, `cal_bs.json`, `pre_bs.json`, `meta.json`.

Expected size: about 0.3–0.5 MB per filing.

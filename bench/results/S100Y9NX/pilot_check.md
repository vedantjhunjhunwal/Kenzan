# Pilot check

Processed 1 filings: 0 accepted, 1 XBRL-only (PDF pending), 0 excluded, 0 errors.

## Per filing

| docID | std | unit | pages | found by | first/last page ok | extra pages | facts ≠ printed | alignment | GT checks failed / evaluable | indent levels | issues |
|---|---|---|--:|---|---|---|--:|---|---|--:|---|
| S100Y9NX | jgaap | 百万円 | None | heading | None/None | [] | 0 | pdf_missing None | 0/28 | 4 | PDF missing: page checks and alignment pending |

## Candidate outcomes

- xbrl_only: pdf_missing: 1

## XBRL conventions (cells)

- dash_tagged_nil: 1
- positive: 83
- printed_negative_xbrl_negative: 4

## Pilot → dev

- all pilot companies in forced_dev.txt: True
- split with the forced list: {'E02367': 'dev'}
- split by hash alone (would have been): {'E02367': 'test'}

## Problems

- S100Y9NX: PDF missing: page checks and alignment pending

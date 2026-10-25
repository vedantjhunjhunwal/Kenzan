# Re-score of `stress/out_v2_unseen` under the final acceptance rule

Original pipeline fingerprint `888416e5df3fbc7c`, re-score `31be56cdd4707f60`; spec sha256 `54e6b44c663d9567` / `54e6b44c663d9567`.

| Variant | Cell F1 (orig = new) | Fully correct | Old decision | Old reasons | New decision | New reasons |
|---|--:|---|---|---|---|---|
| born_digital | 100.0% | yes | accept | – | accept | – |
| raster_200 | 83.3% | no | review | A2, A3 | review | A2, A3, A6 |
| raster_120 | 18.2% | no | review | A2, A3 | review | A10, A2, A3 |
| rot_+0.5 | 85.5% | no | review | A2, A3 | review | A2, A3, A6 |
| rot_-0.5 | 27.8% | no | review | A2, A3 | review | A10, A2, A3, A6 |
| rot_+1.5 | 83.0% | no | review | A2, A3 | review | A2, A3, A6 |
| rot_-1.5 | 84.1% | no | review | A2, A3 | review | A2, A3 |
| noise_s10 | 45.3% | no | review | A2, A3, A5 | review | A10, A2, A3, A5 |
| jpeg_q50 | 69.4% | no | review | A2, A3, A5 | review | A10, A2, A3, A5 |
| scan_200 | 51.9% | no | review | A2, A3 | review | A10, A2, A3 |

Accepted: 1 → 1. False accepts under the final rule: 0.

**Extraction metrics identical: yes.**

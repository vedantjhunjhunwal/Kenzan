# Re-score of `stress/out_heldout_v3` under the final acceptance rule

Original pipeline fingerprint `888416e5df3fbc7c`, re-score `31be56cdd4707f60`; spec sha256 `23362c8eaf200ad1` / `23362c8eaf200ad1`.

| Variant | Cell F1 (orig = new) | Fully correct | Old decision | Old reasons | New decision | New reasons |
|---|--:|---|---|---|---|---|
| born_digital | 100.0% | yes | accept | – | accept | – |
| raster_250 | 75.5% | no | review | A2, A3, A5 | review | A10, A2, A3, A5 |
| raster_175 | 81.1% | no | review | A2, A3 | review | A10, A2, A3 |
| rot_+0.8 | 74.6% | no | review | A2, A3 | review | A10, A2, A3, A6 |
| rot_-1.2 | 81.9% | no | review | A2, A3 | review | A2, A3, A6 |
| noise_s12 | 67.2% | no | review | A2, A3, A5 | review | A10, A2, A3, A5 |
| jpeg_q40 | 67.6% | no | review | A2, A3, A5 | review | A10, A2, A3, A5 |
| scan_250 | 39.4% | no | review | A2, A3 | review | A10, A2, A3 |
| scan_175 | 78.5% | no | review | A2, A3 | review | A10, A2, A3 |

Accepted: 1 → 1. False accepts under the final rule: 0.

**Extraction metrics identical: yes.**

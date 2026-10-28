# Release snapshot

`Kenzan_final.zip` is a `git archive` of commit `f956101d4383288687d6a951a8199730cbbb66ff` (the final source commit). It is added
in the commit right after it, so the archive never contains itself or an older archive. It holds the
tracked files only: no EDINET downloads, OCR models, caches or keys.

| | |
|---|---|
| Source commit | `f956101d4383288687d6a951a8199730cbbb66ff` |
| Built with | `git archive --format=zip --prefix=Kenzan/ f956101d4383288687d6a951a8199730cbbb66ff` (git 2.43.0) |
| Size | 2700260 bytes |
| sha256 | `b50ce9d8a0621d9a369e96ebe25de6832934df3637afe8c21b4e9c1dad0237ad` |

To check it: `git archive --format=zip --prefix=Kenzan/ f956101d4383288687d6a951a8199730cbbb66ff | sha256sum` should give the same hash,
using the same git version (zip output can differ between git versions).

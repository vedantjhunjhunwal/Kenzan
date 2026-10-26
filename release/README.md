# Release snapshot

`Kenzan_final.zip` is a `git archive` of commit `3fb23bacb8858ffcb97d77235c9e2ddbd174213d` (the final source commit). It is added
in the commit right after it, so the archive never contains itself or an older archive. It holds the
tracked files only: no EDINET downloads, OCR models, caches or keys.

| | |
|---|---|
| Source commit | `3fb23bacb8858ffcb97d77235c9e2ddbd174213d` |
| Built with | `git archive --format=zip --prefix=Kenzan/ 3fb23bacb8858ffcb97d77235c9e2ddbd174213d` (git 2.43.0) |
| Size | 2684276 bytes |
| sha256 | `aeceb958f5f77af8c0b3ca6019fd3036e62e40da22599c3350c3c9a34b766567` |

To check it: `git archive --format=zip --prefix=Kenzan/ 3fb23bacb8858ffcb97d77235c9e2ddbd174213d | sha256sum` should give the same hash,
using the same git version (zip output can differ between git versions).

The archive does **not** contain the original AI chat log. That log is a required external attachment
to the submission ([docs/INTEGRITY.md](../docs/INTEGRITY.md)).

Previous snapshot (superseded): commit `b9fbc7009ca7426ff52bc7cec309d7deb16ce575`, 2603145 bytes,
sha256 `38ca8eb2c9535594516fc6e75fcfbd356553b7516c635b95fb2b3f02fae6c511` (in git history at commit
ed59e96).

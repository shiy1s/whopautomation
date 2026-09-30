# Universal Adapter recovery — 2026-10-01

The existing GitHub Phase Runner and n8n orchestration are retained. All executions in this recovery are manual TEST evidence extraction; no publishing workflow is dispatched. Backyard Breaks is excluded.

## Fixes

- Both `GoogleDriveFile` and `GoogleDrive` select the dedicated Drive handler. URL parsing determines file/folder semantics; file aliases, resource keys, invalid URLs and duplicate IDs are handled consistently by n8n and the worker.
- Drive errors and empty results never enter BrowserFallback or yt-dlp. Explicit files no longer suppress unrelated folders.
- Drive listing is metadata-only, obvious non-media entries are filtered, and extensionless entries are resolved using file metadata before downloading. Downloads are bounded by asset count and process timeouts. Evidence preserves original filenames, file IDs and parent-folder provenance.
- n8n Cloud lacks both global `URL` and access to Node's `url` module. The shared normalizer therefore parses the accepted HTTP URL subset without either. Regression tests execute Code bodies in a similarly restricted VM.
- Direct-file temporary paths no longer collide. Successful MediaSilo/Frame.io files are no longer deleted before frame extraction. Browser fallback respects the requested asset limit, removes the actual failed candidate, and supplies stream referers without requiring a browser cookie context.
- Resource discovery syntax and shell pipeline exit propagation were fixed; artifacts are validated before being accepted.
- Phase Runner failures retain the actual GitHub conclusion and exact run ID. They no longer infer that the source is unavailable or recommend an automatic retry.

## Real folder test

- Campaign: [Boxabl Official Clipping](https://contentrewards.com/discover/188c3e39-7850-4896-94df-e7a5be0cfec3), accepting clips when inspected.
- Source: official Drive folder `1XrfBdpDgkQb_Dq6ZVZaPeOuLa6ZElwK7`; metadata listing contained 13 entries.
- n8n manual execution `23`, TEST harness `C7uGn5C72PjdBDkd`, successfully normalized and dispatched the source. Earlier executions `21` and `22` exposed sandbox incompatibility and stopped before dispatch.
- [GitHub run 36772058657](https://github.com/shiy1s/whopautomation/actions/runs/36772058657), commit `b059918126c229652c33d8a61ba671ebf7f3aed7`, completed successfully.
- Asset limit 1. Downloaded `Joe Rogan and Rick Caruso (1).mp4`, file ID `1jVAUxzeUHfwUzebLyohWr5dRvILvTWBB`, 72,756,984 bytes, 54.706009 seconds, 2302 × 1440. ffprobe validation and all 12 frame extractions passed.
- Manifest: `complete: true`, `fallbackUsed: false`, `selection.limited: true`, 12 entries remaining. Artifact ID `11124452122`, SHA-256 `5a7a0f15c76785c8f107bc82da7711d9623d450c19bfe699dabe01670da724df`.

## n8n draft changes

Reproducible operations are in `docs/n8n/universal-adapter-operations.json` and `docs/n8n/phase-runner-diagnostics-operations.json`.

- Main `ea7zEdbiinHSnzQz`: four Code nodes updated; draft version `918bfcfb-ee6a-4419-ab93-80234bd6b96c`.
- Runner `cL4ho6w6HOe8uyya`: one validation Code node updated; draft version `98b0e58c-69c8-43c0-8d45-a7e85de616a2`.
- Readback verified exact code, unchanged connections/settings and unchanged other nodes. Both remain inactive and unpublished. No old execution timeout was restored.
- Whole-workflow SDK validation passed. Static expression-path warnings reflect unspecified sample outputs in the exported SDK. Live update warnings were pre-existing canvas grouping warnings; layout was intentionally preserved.

## Verification scope

29 local regression tests pass. They cover normalization, Drive routing, metadata filtering, duplicate handling, empty folders, timeouts, source provenance, restricted n8n execution and Phase Runner error reporting. Synthetic fixtures are unit-test inputs, not campaign evidence.

Other provider routing and fallback behavior are regression-tested; their authenticated live services and the downstream publishing pipeline have not been exercised during this recovery. Evidence extraction success does not certify campaign compliance or approval to publish.

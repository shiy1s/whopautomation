# Phase 12 — Publication Tracking

## Purpose

Phase 12 is a read-only platform reconciliation stage. It does **not** publish, edit, delete, or re-upload media.

For the exact successful Phase 11 run supplied at dispatch time, it:

1. Verifies the run belongs to this repository and is the **Phase 11 Platform Publishing** workflow.
2. Requires the run to be completed with conclusion `success`.
3. Reads the persistent Phase 11 publication ledger.
4. Selects published YouTube records and requires a real `videoId`.
5. Refreshes the existing YouTube OAuth credential.
6. Calls YouTube Data API `videos.list` for each published video.
7. Records platform state and current public metrics in `state/phase12-publication-tracking.json`.
8. Appends an immutable timestamped snapshot rather than overwriting prior observations.

## Tracked fields

For each YouTube video:

- video ID
- clip file
- source SHA256
- title
- published timestamp
- channel ID
- privacy status
- upload status
- ISO 8601 duration
- definition
- dimension
- view count
- like count
- comment count
- tracking timestamp

## Safety boundaries

- Phase 12 never calls `videos.insert`.
- Phase 12 never changes YouTube video state.
- Phase 12 does not alter the Phase 11 publication ledger.
- Phase 12 only writes its own tracking state file.
- Phase 7 remains untouched.
- Only the exact Phase 11 run supplied at dispatch is accepted.
- The workflow currently enables YouTube only because TikTok/Instagram credentials and tracking contracts have not been verified.

## Authentication

Phase 12 reuses the existing YouTube OAuth client ID, client secret, and refresh token already established for Phase 11. It does not request a new OAuth grant.

The worker uses the same least-privilege `youtube.upload` grant. If the YouTube API rejects `videos.list` with an insufficient-scope response, Phase 12 must stop and report that fact rather than expanding OAuth scope automatically.

## Output

`state/phase12-publication-tracking.json` contains:

```json
{
  "schemaVersion": 1,
  "snapshots": [
    {
      "phase": 12,
      "status": "verified",
      "phase11RunId": 0,
      "trackedAtUtc": "...",
      "platforms": ["youtube"],
      "videoCount": 2,
      "snapshots": []
    }
  ]
}
```

A successful Phase 12 run is evidence that the published YouTube IDs recorded by Phase 11 were successfully reconciled against YouTube at that time. It is not a permanent guarantee about future availability or metrics.

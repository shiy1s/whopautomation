# Phase 11 Platform Publishing

Phase 11 publishes only an exact, verified Phase 10 publishing package.

## Required manual GitHub Actions secrets

### YouTube
- YOUTUBE_CLIENT_ID
- YOUTUBE_CLIENT_SECRET
- YOUTUBE_REFRESH_TOKEN

Optional repository variable:
- YOUTUBE_PRIVACY_STATUS (defaults to public)

The OAuth grant must include `https://www.googleapis.com/auth/youtube.upload`.

### TikTok
Preferred:
- TIKTOK_CLIENT_KEY
- TIKTOK_CLIENT_SECRET
- TIKTOK_REFRESH_TOKEN

Optional alternative:
- TIKTOK_ACCESS_TOKEN

The authorized TikTok user must have the `video.publish` scope and the Direct Post product must be configured. The workflow queries creator information before posting and refuses to override unavailable privacy settings.

### Instagram
- INSTAGRAM_ACCESS_TOKEN
- INSTAGRAM_USER_ID

Optional repository variable:
- INSTAGRAM_GRAPH_VERSION (defaults to v24.0)

The account must be an eligible Instagram professional account and the token must have content-publishing permission.

## Safety controls

- Exact Phase 10 run ID is supplied manually.
- Phase 10 run must be completed successfully.
- Phase 10 artifact is downloaded by exact artifact name.
- Manifest, video checksums and metadata are validated before publishing.
- Publishing requires the literal confirmation value `PUBLISH`.
- Duplicate protection is persisted in `state/phase11-publication-ledger.json`.
- Phase 11 serializes publishing with GitHub Actions concurrency.
- Instagram uses a temporary public GitHub Release only while its container is being created/processed; the release is deleted after the transaction.
- No Phase 7 renderer or earlier phase is modified.

## Platform behavior

YouTube uses the Data API `videos.insert`.

TikTok uses Content Posting API Direct Post with a local file upload.

Instagram uses the Reels container -> status check -> publish flow and requires a publicly accessible media URL during container creation.

Phase 11 does not create Stories or paid boosts and does not alter campaign metadata.

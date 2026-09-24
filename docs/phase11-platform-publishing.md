# Phase 11 Platform Publishing

Phase 11 publishes only an exact, verified Phase 10 publishing package.

## Required manual GitHub Actions secrets

### YouTube
- YOUTUBE_CLIENT_ID
- YOUTUBE_CLIENT_SECRET
- YOUTUBE_REFRESH_TOKEN

Optional repository variable:
- YOUTUBE_PRIVACY_STATUS (defaults to public)

The OAuth grant must include `https://www.googleapis.com/auth/youtube.upload`. Phase 11 preflight validates the refresh-token exchange, token audience, token expiry, and this upload scope. It intentionally does not call `channels.list` because that endpoint requires broader account/channel authorization than the upload-only grant; the real `videos.insert` operation is the authoritative YouTube publishing check.

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
- INSTAGRAM_GRAPH_VERSION (defaults to v25.0)

The account must be an eligible Instagram professional account. For Instagram Login, Phase 11 verifies the token's `user_id` (the professional-account ID); the `id` field returned by `/me` is app-scoped and is not used as the publishing account ID. The real publishing transaction remains the authoritative check for `instagram_business_content_publish`.

## Safety controls

- Exact Phase 10 run ID is supplied manually.
- Phase 10 run must be completed successfully.
- Phase 10 artifact is downloaded by exact artifact name.
- Manifest, video checksums and metadata are validated before publishing.
- `TEST` is a non-publishing dry run; it performs selected-platform preflight and never creates the temporary Instagram media release.
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

### GitHub publication ledger authentication
The publication ledger is written through the GitHub Contents API using the workflow `GITHUB_TOKEN` (`GH_TOKEN` in the worker environment) with `contents: write`. Ledger reads and writes must both pass that token explicitly; public-repository reads may work anonymously, but authenticated writes do not.

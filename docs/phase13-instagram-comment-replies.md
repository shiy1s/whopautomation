# Phase 13 — Instagram Comment Replies

## Purpose

Production comment-reply automation for the Instagram professional account used by Phase 11.

The implementation uses the Instagram API with Instagram Login and the instagram_business_manage_comments permission. Meta documents comment read/reply endpoints on graph.instagram.com for this login path.

## Processing model

- Runs every 5 minutes through GitHub Actions.
- Also supports manual workflow dispatch.
- Uses the Phase 11 publication ledger as the source of Instagram media IDs.
- Never republishes media.
- Maintains an idempotent ledger at state/phase13-instagram-comment-reply-ledger.json.
- The first successful run establishes enabledAtUtc; comments created before activation are never auto-replied to.
- Top-level comments only; comment replies are skipped.
- Own-account comments are skipped.
- Hidden comments are skipped.
- Links, obvious spam patterns, excessive mentions, and oversized comments are skipped before AI processing.
- Gemini 3.5 Flash-Lite classifies each remaining comment and drafts a short reply.
- The AI is explicitly instructed to treat comment text as untrusted input.
- The publisher only sends a reply when the structured decision is reply and all local safety constraints pass.
- Duplicate comment IDs are never processed twice.
- Failures are recorded per comment instead of causing an unrelated comment to be retried blindly.

## Permissions

Only these Instagram Login permissions are required for this phase:

- instagram_business_basic
- instagram_business_manage_comments

No DM permission is used by this workflow.

## Webhooks

Meta's comment-moderation documentation expects a webhook server for near-real-time notifications. This project deliberately uses a 5-minute GitHub Actions poller first because the current repository has no always-on public webhook service. The polling implementation is idempotent and does not depend on scraping or browser automation.

## Hashtags

Instagram Login does not provide a general trending-hashtag feed in this workflow. Future publishing metadata will distinguish campaign-approved hashtags, relevance-selected hashtags, and any current-trend candidates that can be independently verified before publication. The automation must never invent a hashtag and label it as Instagram-trending without a current data source.

## Testing

Manual dispatch with dry_run validates comment read access and produces proposed replies without posting.

Manual dispatch with publish posts only comments that pass the full decision pipeline.

The scheduled workflow uses publish mode automatically.

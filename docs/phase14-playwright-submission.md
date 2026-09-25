# Phase 14 — Content Rewards Playwright Submission

## Purpose

Phase 14 processes the complete submission queue generated from the Phase 11 publication ledger. It is platform/clip cardinality agnostic: every eligible YouTube or Instagram publication is an independent submission job.

## Current architecture

1. Phase 11 publishes clips and records one immutable publication record per successful platform/clip.
2. Phase 14 queue preparation selects every publication belonging to the exact Phase 11 run, verifies the 30-minute window, campaign status, URL provenance, and duplicate state.
3. The Playwright worker processes every 'queued' record independently.
4. The worker opens the exact contentrewards.com/discover/<campaignId> page, verifies authentication and campaign routing, fills the public post URL, identifies the requirements checkbox unambiguously, and submits only when the UI is unambiguous.
5. Successful submissions become 'submitted'.
6. Expired jobs become 'expired'.
7. Ambiguous UI/auth/application problems become 'blocked'.
8. Any other uncertain result becomes 'needs_manual_verification'. These states are intentionally not auto-retried, preventing duplicate submissions after an ambiguous click.
9. Screenshots and HTML are retained as GitHub Actions artifacts for diagnosis.

## Security and platform boundary

This worker targets the current Content Rewards web application at contentrewards.com. It does not automate the legacy Whop-hosted submission UI, does not use private/undocumented APIs, does not bypass CAPTCHA or anti-bot controls, and does not alter fraud/engagement signals.

Current Content Rewards Creator Terms define a Submission as submitting a posted Clip URL, and state that Content Rewards does not post on a creator's behalf. They also prohibit fraudulent engagement and botting. This worker only performs the creator's submission action after the post already exists and never automates views, likes, comments, or other engagement.

The legacy Whop Terms prohibit automated access to Whop. Therefore the worker must not be pointed at a Whop-hosted submission page.

## Authentication

CONTENT_REWARDS_STORAGE_STATE_B64 is a GitHub Actions secret containing a Playwright storageState JSON object for the user's authorized Content Rewards creator session.

Never commit this state to the repository. Never print it in logs. If the session expires, the worker must fail closed and the secret must be re-provisioned.

## Dry run

Run the Playwright workflow with dry_run=true first. Dry run opens each queued campaign and validates authentication, campaign routing, platform visibility, Submit control uniqueness, and evidence capture without clicking the final Submit action.

Only after a successful dry run should dry_run=false be enabled.

## Cardinality

The worker does not assume two clips or two platforms. Examples:

- 1 clip × YouTube = 1 job
- 1 clip × YouTube + Instagram = 2 jobs
- 3 clips × YouTube + Instagram = up to 6 jobs
- partial platform failures create only the publications that actually succeeded

## Failure policy

A click whose result cannot be verified is not automatically retried. It becomes needs_manual_verification. This is deliberate: an ambiguous post-click state could mean the submission succeeded, and blind retrying could duplicate the submission.

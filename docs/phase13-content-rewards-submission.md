# Phase 13 — Content Rewards Submission Gate

## Purpose

Phase 13 adds a production-safe Content Rewards submission gate after Phase 11 platform publishing.

It does not replace or modify the proven rendering or publishing phases.

## Safety model

A submission candidate is accepted only when all of these are true:

1. The supplied Phase 11 run exists, is the exact Phase 11 Platform Publishing workflow, and completed successfully.
2. The selected clip exists as a published record in state/phase11-publication-ledger.json.
3. The campaign is explicitly supplied as active.
4. The platform is YouTube or Instagram.
5. The post URL matches the exact published remote object.
6. The publication is no more than 30 minutes old.
7. The exact campaign + platform + clip + URL combination has not already been recorded.
8. SUBMIT_READY is explicitly supplied.

## Important boundary

Content Rewards submission is deliberately manual-gated. No unsupported private API, hidden endpoint, browser automation, Browserbase, or credential scraping is used.

The workflow prepares and records a verified submission candidate. The actual Whop/Content Rewards Submit Clip action remains a human-controlled UI step until an official supported submission API is verified.

This is intentional: a false submission, wrong campaign submission, duplicate submission, or expired 30-minute submission window is worse than requiring one final UI action.

## TikTok

TikTok remains disabled in Phase 13, matching the current project decision.

## Existing campaign

The Call of Duty — RICOCHET campaign is currently treated as unavailable because its remaining budget is exhausted. Phase 13 therefore refuses exhausted, closed, or unknown campaign status.

When a new eligible Content Rewards campaign is available, supply its exact campaign ID and name and verify it is active before preparing a submission.

## No test run against production

Do not dispatch this workflow against the old September 23/24 publications: those posts are outside the 30-minute submission window. The workflow was intentionally added without a production execution so that no predictable failure run is created.

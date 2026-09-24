# Phase 14 — Content Rewards Submission Gate

Phase 13 remains reserved for the paused Instagram comment-reply automation. Content Rewards submission is therefore Phase 14.

## Purpose

Phase 14 adds a production-safe Content Rewards submission gate after Phase 11 platform publishing. It does not replace or modify the proven rendering, QC, metadata, publishing, or tracking phases.

## Safety gates

A candidate is accepted only when:

1. The supplied Phase 11 run is the exact Phase 11 Platform Publishing workflow and completed successfully.
2. The selected clip exists as a published record in state/phase11-publication-ledger.json.
3. The selected publication record carries the same Phase 11 run ID supplied to Phase 14; future Phase 11 publications now record this provenance automatically.
4. The Content Rewards campaign is explicitly verified as active.
5. The platform is YouTube or Instagram.
6. The submitted URL matches the exact published remote object.
7. The publication is no more than 30 minutes old.
8. The exact campaign + platform + clip + URL combination is not already recorded.
9. SUBMIT_READY is explicitly supplied.

## Manual final submission

Content Rewards submission is deliberately manual-gated. No unsupported private API, hidden endpoint, browser automation, Browserbase, or credential scraping is used.

The workflow prepares and records a verified candidate. The final Submit Clip action remains a human-controlled UI step until an official supported submission API is verified.

## TikTok

TikTok remains deferred and is not part of Phase 14.

## RICOCHET

The Call of Duty — RICOCHET campaign is currently unavailable because its remaining budget is exhausted. Phase 14 refuses exhausted, closed, and unknown campaign states.

When a new eligible campaign is available, verify the exact campaign in Content Rewards, confirm it is active, then use its exact campaign ID/name.

## No production run was dispatched

The existing September 23/24 publications are outside the 30-minute submission window. Running this workflow against them would intentionally fail, so no such run was created.

"""Fail-closed campaign execution guard.

This repository may render and analyse test footage, but it must never treat an
inactive campaign as eligible for live publishing.  The guard is deliberately
independent of any model output so a hallucinated analysis cannot override a
known campaign status.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_rules(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("campaign"), dict):
        raise ValueError("Campaign rules must contain a campaign object.")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate campaign execution mode.")
    parser.add_argument("--rules", default="campaign_rules.json")
    parser.add_argument("--mode", choices=("dry-run", "publish"), required=True)
    args = parser.parse_args()

    rules = load_rules(Path(args.rules))
    campaign = rules["campaign"]
    status = str(campaign.get("status", "unknown")).strip().lower()
    fixture = bool(campaign.get("test_fixture", False))

    result = {
        "campaign": campaign.get("name", "unknown"),
        "campaign_status": status,
        "mode": args.mode,
        "allowed": False,
        "reason": "",
    }

    if args.mode == "publish":
        result["reason"] = (
            "Live publishing is disabled by this repository. A separate, "
            "human-approved publishing integration is required."
        )
    elif status == "active":
        result["allowed"] = True
        result["reason"] = "Active campaign approved for non-publishing dry run."
    elif fixture:
        result["allowed"] = True
        result["reason"] = "Inactive campaign allowed only as an explicitly marked test fixture."
    else:
        result["reason"] = "Campaign is not active and is not an approved test fixture."

    print(json.dumps(result, indent=2))
    return 0 if result["allowed"] else 2


if __name__ == "__main__":
    sys.exit(main())

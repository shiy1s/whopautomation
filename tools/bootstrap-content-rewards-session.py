#!/usr/bin/env python3
"""One-time interactive bootstrap for the Content Rewards Playwright session.

This script intentionally does not automate login. The user completes the normal
Content Rewards/Whop login in a visible browser, then the authenticated browser
state is exported for GitHub Actions.
"""
import base64
import gzip
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("content-rewards-storage-state.json")
B64 = Path("content-rewards-storage-state.b64")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://contentrewards.com/creators", wait_until="domcontentloaded")
    print("Complete the normal Content Rewards login in the opened browser.")
    input("After you are fully logged in and can see the creator dashboard, press Enter here...")
    # The current Content Rewards UI may keep a hidden/overlay "Sign in" element
    # in the DOM even after authentication. Confirm the authenticated dashboard
    # using visible navigation and/or the authenticated Whop app URL instead.
    visible_sign_in = page.get_by_text("Sign in", exact=True).filter(visible=True).count()
    dashboard_markers = (
        page.get_by_text("Discover", exact=True).filter(visible=True).count()
        + page.get_by_text("Campaigns", exact=True).filter(visible=True).count()
        + page.get_by_text("Submissions", exact=True).filter(visible=True).count()
        + page.get_by_text("Drafts", exact=True).filter(visible=True).count()
    )
    authenticated_url = "/app/" in page.url or "/app" in page.url

    if dashboard_markers < 2 and not authenticated_url:
        browser.close()
        raise RuntimeError(
            "Authenticated session was not confirmed. "
            f"URL={page.url!r}, visible_sign_in={visible_sign_in}, "
            f"dashboard_markers={dashboard_markers}"
        )

    state = context.storage_state()
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    compressed = b"GZIP:" + gzip.compress(OUT.read_bytes(), compresslevel=9)
    encoded = base64.b64encode(compressed).decode()
    B64.write_text(encoded, encoding="ascii")
    print("\nCreated:", OUT.resolve())
    print("Compressed Base64 length:", len(encoded))
    print("\nAdd the base64 value as the GitHub Actions repository secret:")
    print("CONTENT_REWARDS_STORAGE_STATE_B64")
    print("\nDo not commit or paste the secret value into chat.")
    browser.close()

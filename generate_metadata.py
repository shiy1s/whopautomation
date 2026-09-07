import os
import sys
import json
import time
from pathlib import Path

from google import genai
from google.genai import types


# ============================================================
# CONFIG
# ============================================================

OUTPUT_FILE = "clip_metadata.json"

MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]

MAX_ATTEMPTS_PER_MODEL = 3

CAMPAIGN_HASHTAGS = [
    "#CallOfDuty",
    "#RICOCHET",
]

MAX_RELATED_HASHTAGS = 1


# ============================================================
# HELPERS
# ============================================================

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_api_key():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set."
        )

    return api_key


def clean_json_response(text):
    """
    Gemini sometimes wraps JSON in markdown fences.
    Remove those fences before parsing.
    """

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:]

    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    return text.strip()


def validate_metadata(data):
    required_fields = [
        "title",
        "description",
        "topic",
        "hook",
        "audience",
        "event",
        "emotion",
        "search_terms",
        "content_hashtags",
        "discovery_hashtags",
    ]

    for field in required_fields:
        if field not in data:
            raise ValueError(
                f"Missing required metadata field: {field}"
            )

    if not isinstance(data["content_hashtags"], list):
        raise ValueError("content_hashtags must be a list.")

    if not isinstance(data["discovery_hashtags"], list):
        raise ValueError("discovery_hashtags must be a list.")

    return True


def build_publishing_caption(data):
    """
    Build the final campaign-safe caption.

    #Ad is intentionally placed alone on the first line.
    """

    description = data["description"].strip()

    # Select at most one content-related hashtag.
    content_tags = data.get("content_hashtags", [])

    selected_content_tag = None

    for tag in content_tags:
        if isinstance(tag, str) and tag.startswith("#"):
            selected_content_tag = tag
            break

    hashtags = list(CAMPAIGN_HASHTAGS)

    if selected_content_tag:
        if selected_content_tag.lower() not in [
            x.lower() for x in hashtags
        ]:
            hashtags.append(selected_content_tag)

    hashtags = hashtags[:2 + MAX_RELATED_HASHTAGS]

    caption = (
        "#Ad\n"
        "@Callofduty\n"
        f"{description}\n\n"
        + " ".join(hashtags)
    )

    return caption


# ============================================================
# GEMINI ANALYSIS
# ============================================================

def analyze_clip(client, clip_path, campaign_rules):
    """
    Analyze the actual rendered clip.

    The model is explicitly instructed to avoid inventing
    dialogue, events, claims, or campaign information.
    """

    uploaded_file = client.files.upload(
        file=str(clip_path)
    )

    prompt = f"""
You are a professional short-form gaming content strategist
working on a Call of Duty RICOCHET Anti-Cheat campaign.

Your job is to analyze the ACTUAL VIDEO FILE provided to you.

Do NOT invent:
- dialogue
- events
- statistics
- dates
- people
- accusations
- gameplay actions
- campaign claims
- facts that cannot be seen or heard in the video

Only describe things that are genuinely supported by the video.

CAMPAIGN RULES:

{json.dumps(campaign_rules, indent=2)}

IMPORTANT CAMPAIGN REQUIREMENTS:

1. The content must be relevant to Call of Duty RICOCHET
   Anti-Cheat enforcement.

2. The description must accurately represent what is actually
   shown or said in the clip.

3. Never fabricate dialogue.

4. Never claim that a specific cheat provider or seller was
   caught unless the actual video explicitly establishes it.

5. Do not manufacture statistics or enforcement numbers.

6. The content should feel natural for TikTok, YouTube Shorts,
   and Instagram Reels.

7. The title should be concise and attention-grabbing without
   being misleading.

8. The description should explain the actual point of the clip
   in natural English.

9. Generate content-related hashtags based on the actual video.

10. Generate discovery/trending HASHTAG CANDIDATES separately.
    These are NOT confirmed live trending hashtags.
    Do not claim that they are currently trending.

11. Never use irrelevant viral hashtags merely to chase views.

12. Avoid excessive hashtags.

13. The final campaign caption will separately include:
    #Ad
    @Callofduty

RETURN ONLY VALID JSON.

USE EXACTLY THIS STRUCTURE:

{{
  "title": "short engaging title",
  "description": "accurate description of the actual clip",
  "topic": "main topic",
  "hook": "main hook of the clip",
  "audience": "target audience",
  "event": "specific event or moment shown",
  "emotion": "dominant emotion",
  "search_terms": [
    "search term 1",
    "search term 2",
    "search term 3"
  ],
  "content_hashtags": [
    "#relevanttag1",
    "#relevanttag2",
    "#relevanttag3"
  ],
  "discovery_hashtags": [
    "#discoverycandidate1",
    "#discoverycandidate2",
    "#discoverycandidate3"
  ]
}}

The JSON must contain no markdown and no explanation.
"""


    last_error = None

    for model in MODELS:

        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):

            try:
                print(
                    f"Trying {model} "
                    f"(attempt {attempt}/{MAX_ATTEMPTS_PER_MODEL})..."
                )

                response = client.models.generate_content(
                    model=model,
                    contents=[
                        uploaded_file,
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )

                text = response.text

                if not text:
                    raise ValueError(
                        "Gemini returned an empty response."
                    )

                text = clean_json_response(text)

                data = json.loads(text)

                validate_metadata(data)

                return data

            except Exception as e:

                last_error = e

                print(
                    f"Model {model} failed: {e}"
                )

                if attempt < MAX_ATTEMPTS_PER_MODEL:
                    time.sleep(5)

        print(
            f"Moving to fallback model after {model} failures."
        )

    raise RuntimeError(
        f"All Gemini models failed. Last error: {last_error}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if len(sys.argv) < 2:
        print(
            "Usage: python generate_metadata.py <clip.mp4> "
            "[campaign_rules.json]"
        )
        sys.exit(1)

    clip_path = Path(sys.argv[1])

    if not clip_path.exists():
        print(
            f"ERROR: Clip does not exist: {clip_path}"
        )
        sys.exit(1)

    rules_path = (
        Path(sys.argv[2])
        if len(sys.argv) >= 3
        else Path("campaign_rules.json")
    )

    if not rules_path.exists():
        print(
            f"ERROR: Campaign rules file does not exist: "
            f"{rules_path}"
        )
        sys.exit(1)

    print("")
    print("=" * 60)
    print("AI CLIP METADATA GENERATOR")
    print("=" * 60)
    print("")
    print(f"Clip: {clip_path}")
    print(f"Rules: {rules_path}")
    print("")

    campaign_rules = load_json(rules_path)

    api_key = get_api_key()

    client = genai.Client(
        api_key=api_key
    )

    metadata = analyze_clip(
        client,
        clip_path,
        campaign_rules,
    )

    # Add campaign-safe publishing metadata.
    metadata["campaign"] = {
        "name": campaign_rules["campaign"]["name"],
        "brand": campaign_rules["campaign"]["brand"],
        "required_account": campaign_rules["campaign"][
            "required_account"
        ],
    }

    metadata["publishing"] = {
        "disclosure": "#Ad",
        "account_tag": "@Callofduty",
        "campaign_hashtags": CAMPAIGN_HASHTAGS,
        "selected_hashtags": [],
        "live_trending_check_required": True,
    }

    # Build campaign hashtags.
    content_tags = metadata.get(
        "content_hashtags",
        []
    )

    selected_hashtags = list(
        CAMPAIGN_HASHTAGS
    )

    for tag in content_tags:

        if not isinstance(tag, str):
            continue

        if not tag.startswith("#"):
            continue

        if tag.lower() in [
            x.lower() for x in selected_hashtags
        ]:
            continue

        selected_hashtags.append(tag)

        if len(selected_hashtags) >= (
            2 + MAX_RELATED_HASHTAGS
        ):
            break

    metadata["publishing"][
        "selected_hashtags"
    ] = selected_hashtags

    metadata["publishing"][
        "caption"
    ] = build_publishing_caption(metadata)

    # Store the actual source clip filename.
    metadata["source_clip"] = clip_path.name

    # Timestamp generated locally for traceability.
    metadata["generated_at"] = (
        time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime()
        )
    )

    save_json(
        metadata,
        OUTPUT_FILE
    )

    print("")
    print("=" * 60)
    print("METADATA GENERATED SUCCESSFULLY")
    print("=" * 60)
    print("")
    print(f"Output: {OUTPUT_FILE}")
    print("")
    print("TITLE:")
    print(metadata["title"])
    print("")
    print("DESCRIPTION:")
    print(metadata["description"])
    print("")
    print("HASHTAGS:")
    print(
        " ".join(
            metadata["publishing"][
                "selected_hashtags"
            ]
        )
    )
    print("")
    print("FINAL CAPTION:")
    print("-" * 60)
    print(
        metadata["publishing"]["caption"]
    )
    print("-" * 60)
    print("")


if __name__ == "__main__":
    main()

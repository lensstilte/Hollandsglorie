import os
import time
import random
import logging
from typing import List, Optional

from atproto import Client

# --------------------------------------------------
# Config
# --------------------------------------------------
AUTHOR_FEED_LIMIT = 100   # max 100 (API limit)
FEED_LIMIT = 100          # max 100 (API limit)
DELAY_SECONDS = 1         # 1 seconde delay tussen acties

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# --------------------------------------------------
# Bot accounts (env suffixen)
# --------------------------------------------------
ACCOUNT_KEYS = [
    "BEAUTYFAN",
    "HOTBLEUSKY",
    "DMPHOTOS",
]

# --------------------------------------------------
# 10 TARGETS (leeg = skip)
# --------------------------------------------------
TARGET_HANDLE_1 = "dmphotos.bsky.social"
TARGET_HANDLE_2 = "theysaidnothing.bsky.social"
TARGET_HANDLE_3 = "wsimonde.bsky.social"
TARGET_HANDLE_4 = "velvetdesire.bsky.social"
TARGET_HANDLE_5 = "sensushots.bsky.social"
TARGET_HANDLE_6 = "steefschrijber1970.bsky.social"
TARGET_HANDLE_7 = "damienmanson.bsky.social"
TARGET_HANDLE_8 = "boxy0075.bsky.social"
TARGET_HANDLE_9 = ""
TARGET_HANDLE_10 = ""

# Target volgorde 10 -> 1 (zodat 1 als laatste komt, dus “bovenaan”)
TARGET_HANDLES: List[str] = [
    TARGET_HANDLE_10,
    TARGET_HANDLE_9,
    TARGET_HANDLE_8,
    TARGET_HANDLE_7,
    TARGET_HANDLE_6,
    TARGET_HANDLE_5,
    TARGET_HANDLE_4,
    TARGET_HANDLE_3,
    TARGET_HANDLE_2,
    TARGET_HANDLE_1,
]

# --------------------------------------------------
# 3 FEEDS (leeg = skip)
# Je mag hier:
# - at://did:.../app.bsky.feed.generator/xxxx
# - of https://bsky.app/profile/<did>/feed/<rkey>
# plakken. Script maakt er zelf een at:// URI van.
# --------------------------------------------------
FEED_1 = "https://bsky.app/profile/did:plc:jaka644beit3x4vmmg6yysw7/feed/aaadqdb77ba62"
FEED_2 = ""
FEED_3 = ""

# Feed volgorde 3 -> 1 (zodat feed 1 later komt dan feed 3)
FEEDS: List[str] = [FEED_3, FEED_2, FEED_1]


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def normalize_feed_uri(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""

    if s.startswith("at://"):
        return s

    # bsky.app URL:
    # https://bsky.app/profile/did:plc:XXXX/feed/YYYY
    if "bsky.app/profile/" in s and "/feed/" in s:
        try:
            after = s.split("bsky.app/profile/", 1)[1]
            profile_part, feed_part = after.split("/feed/", 1)
            did_or_handle = profile_part.strip("/").split("/", 1)[0]
            rkey = feed_part.strip("/").split("/", 1)[0]
            # feed generator URI:
            return f"at://{did_or_handle}/app.bsky.feed.generator/{rkey}"
        except Exception:
            return s  # laat hem dan maar falen met duidelijke error

    return s


def get_client_for_account(label: str) -> Optional[Client]:
    username = os.getenv(f"BSKY_USERNAME_{label}")
    password = os.getenv(f"BSKY_PASSWORD_{label}")

    if not username or not password:
        logging.warning("Geen credentials voor %s, skip.", label)
        return None

    client = Client()
    try:
        client.login(username, password)
        logging.info("Ingelogd als *** (label=%s)", label)
        return client
    except Exception as e:
        logging.error("Login mislukt voor %s: %s", label, e)
        return None


def fetch_author_feed(client: Client, actor_handle: str):
    logging.info("Author feed ophalen van %s (limit=%d)...", actor_handle, AUTHOR_FEED_LIMIT)
    feed = client.get_author_feed(
        actor=actor_handle,
        limit=AUTHOR_FEED_LIMIT,
        filter="posts_no_replies",
    )
    return list(feed.feed or [])


def fetch_generator_feed(client: Client, feed_uri: str):
    feed_uri = normalize_feed_uri(feed_uri)
    logging.info("Generator feed ophalen: %s (limit=%d)...", feed_uri, FEED_LIMIT)

    # Sommige versies hebben client.get_feed, andere client.app.bsky.feed.get_feed
    try:
        resp = client.app.bsky.feed.get_feed({"feed": feed_uri, "limit": FEED_LIMIT})
        return list(getattr(resp, "feed", []) or [])
    except Exception:
        resp = client.get_feed(feed=feed_uri, limit=FEED_LIMIT)  # fallback
        return list(getattr(resp, "feed", []) or [])


def is_quote_post(post_view) -> bool:
    """
    Quote-posts hebben meestal een embed met 'record' of 'record_with_media' (of varianten).
    We sluiten die uit.
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    for attr in ["record", "record_with_media", "recordWithMedia", "recordWithMediaView", "record_view"]:
        if getattr(embed, attr, None) is not None:
            return True

    # Sommige types hebben embed.record.* genest; bovenstaande vangt de meest voorkomende
    return False


def has_media(post_view) -> bool:
    """
    Alleen foto/video (geen text-only).
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    # images
    images = getattr(embed, "images", None)
    if isinstance(images, list) and images:
        return True

    # video/playlist (verschilt per model)
    for attr in ["playlist", "video", "aspect_ratio", "aspectRatio"]:
        if getattr(embed, attr, None):
            return True

    # media container (recordWithMedia/video cases)
    media = getattr(embed, "media", None)
    if media:
        media_images = getattr(media, "images", None)
        if isinstance(media_images, list) and media_images:
            return True
        for attr in ["playlist", "video", "aspect_ratio", "aspectRatio"]:
            if getattr(media, attr, None):
                return True

    return False


def is_own_post_item(feed_item, target_handle: str) -> bool:
    """
    Voor targets: NIET reposts/reason items pakken, en author moet target zijn.
    """
    # feed_item.reason != None betekent “repost item in feed”
    if getattr(feed_item, "reason", None) is not None:
        return False

    post = feed_item.post
    author = getattr(post, "author", None)
    if not author:
        return False

    return (author.handle or "").lower() == target_handle.lower()


def valid_for_repost(feed_item, mode: str, target_handle: str = "") -> bool:
    """
    mode:
      - "target": alleen echte eigen posts van die handle
      - "feed": we nemen post items uit generator feed, maar géén repost/reason items
    """
    post = feed_item.post

    if is_quote_post(post):
        return False

    if not has_media(post):
        return False

    if mode == "target":
        return is_own_post_item(feed_item, target_handle)

    if mode == "feed":
        # in generator feed ook geen repost-items
        if getattr(feed_item, "reason", None) is not None:
            return False
        return True

    return False


def unrepost_like_and_repost(client: Client, feed_item) -> None:
    post = feed_item.post
    viewer = getattr(post, "viewer", None)

    repost_uri = getattr(viewer, "repost", None) if viewer else None
    like_uri = getattr(viewer, "like", None) if viewer else None

    # eerst oude repost weg, dan opnieuw
    if repost_uri:
        logging.info("  Oude repost verwijderen...")
        try:
            client.delete_repost(repost_uri)
            logging.info("  Oude repost verwijderd.")
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen: %s", e)

    try:
        client.repost(uri=post.uri, cid=post.cid)
        logging.info("  Repost gelukt: %s", post.uri)
    except Exception as e:
        logging.error("  Repost mislukt: %s", e)
        return

    if not like_uri:
        try:
            client.like(uri=post.uri, cid=post.cid)
            logging.info("  Like gelukt.")
        except Exception as e:
            logging.warning("  Like mislukt: %s", e)


def pick_one_random(valid_items: List) -> Optional:
    if not valid_items:
        return None
    return random.choice(valid_items)


def process_account(label: str) -> None:
    logging.info("=== Start account %s ===", label)
    client = get_client_for_account(label)
    if not client:
        return

    # 1) eerst FEEDS (3->1)
    for feed in FEEDS:
        feed = (feed or "").strip()
        if not feed:
            continue

        feed_uri = normalize_feed_uri(feed)
        logging.info("=== Account %s: FEED %s ===", label, feed_uri)

        try:
            items = fetch_generator_feed(client, feed_uri)
        except Exception as e:
            logging.error("Feed ophalen mislukt (%s): %s", feed_uri, e)
            continue

        valid = [it for it in items if valid_for_repost(it, mode="feed")]
        chosen = pick_one_random(valid)

        if not chosen:
            logging.info("Geen geldige media-posts in FEED, skip.")
            continue

        unrepost_like_and_repost(client, chosen)
        time.sleep(DELAY_SECONDS)

    # 2) daarna TARGETS (10->1)
    for target_handle in TARGET_HANDLES:
        target_handle = (target_handle or "").strip()
        if not target_handle:
            continue

        logging.info("=== Account %s: TARGET %s ===", label, target_handle)

        try:
            items = fetch_author_feed(client, target_handle)
        except Exception as e:
            logging.error("Author feed ophalen mislukt (%s): %s", target_handle, e)
            continue

        valid = [it for it in items if valid_for_repost(it, mode="target", target_handle=target_handle)]
        chosen = pick_one_random(valid)

        if not chosen:
            logging.info("Geen geldige media-posts voor %s, skip.", target_handle)
            continue

        unrepost_like_and_repost(client, chosen)
        time.sleep(DELAY_SECONDS)


def main():
    logging.info("=== Start Hollands Glorie RANDOM (targets+feeds) run ===")
    for label in ACCOUNT_KEYS:
        process_account(label)
    logging.info("=== Hollands Glorie run voltooid ===")


if __name__ == "__main__":
    main()
import os
import random
import time
import logging
from typing import List, Optional

from atproto import Client

# --------------------------------------------------
# Config
# --------------------------------------------------
AUTHOR_FEED_LIMIT = 100          # max 100 (Bluesky API limiet)
FEED_LIMIT = 100                # max 100
DELAY_SECONDS = 1               # 1 seconde tussen acties
RANDOM_PER_TARGET = 1           # 1 random media post per target
RANDOM_PER_FEED = 1             # 1 random media post per feed

# --------------------------------------------------
# Logging
# --------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# --------------------------------------------------
# Bot-accounts (environment suffixen)
# --------------------------------------------------
ACCOUNT_KEYS = [
    "BEAUTYFAN",
    "HOTBLEUSKY",
    "DMPHOTOS",
]

# --------------------------------------------------
# 10 TARGET HANDLES (leeg = skip)
# Vul hier je accounts in
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

# Volgorde: 10 -> 1 (1 eindigt bovenaan)
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
# Gebruik de "at://.../app.bsky.feed.generator/..." URI
# --------------------------------------------------
FEED_URI_1 = ""  # bijv: "at://did:plc:.../app.bsky.feed.generator/abcdef"
FEED_URI_2 = ""
FEED_URI_3 = ""

# Volgorde: 3 -> 1 (1 eindigt bovenaan)
FEED_URIS: List[str] = [
    FEED_URI_3,
    FEED_URI_2,
    FEED_URI_1,
]

# --------------------------------------------------
# Helpers
# --------------------------------------------------
def get_client_for_account(label: str) -> Optional[Client]:
    username = os.getenv(f"BSKY_USERNAME_{label}")
    password = os.getenv(f"BSKY_PASSWORD_{label}")

    if not username or not password:
        logging.warning("Geen credentials voor %s, skip.", label)
        return None

    client = Client()
    try:
        client.login(username, password)
        logging.info("Ingelogd als %s (label=%s)", username, label)
        return client
    except Exception as e:
        logging.error("Login mislukt voor %s: %s", label, e)
        return None


def has_media(post_view) -> bool:
    """
    True als er echt media is (foto/video/external thumb).
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    # Images embed
    images = getattr(embed, "images", None)
    if isinstance(images, list) and images:
        return True

    # Video embed (sommige builds)
    playlist = getattr(embed, "playlist", None)
    if playlist:
        return True

    # RecordWithMedia kan ook 'media' hebben (maar dat is quote post -> wordt elders geblokt)
    media = getattr(embed, "media", None)
    if media:
        media_images = getattr(media, "images", None)
        if isinstance(media_images, list) and media_images:
            return True
        media_playlist = getattr(media, "playlist", None)
        if media_playlist:
            return True

    # External (thumbnail)
    external = getattr(embed, "external", None)
    if external and getattr(external, "thumb", None):
        return True

    return False


def is_quote_post(post_view) -> bool:
    """
    Quote posts zijn embed types met 'record' (AppBskyEmbedRecord of RecordWithMedia).
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False
    record = getattr(embed, "record", None)
    return record is not None


def is_valid_post_item(feed_item) -> bool:
    """
    Regels:
    - Geen reposts (dus reason == None)
    - Geen replies (record.reply)
    - Moet media hebben
    - Geen quote post
    """
    # reposts overslaan
    if getattr(feed_item, "reason", None) is not None:
        return False

    post_view = feed_item.post
    record = getattr(post_view, "record", None)

    # replies overslaan
    if record and getattr(record, "reply", None):
        return False

    # quote overslaan
    if is_quote_post(post_view):
        return False

    # tekst-only overslaan
    if not has_media(post_view):
        return False

    return True


def fetch_author_feed(client: Client, actor_handle: str):
    logging.info("Author feed ophalen van %s (limit=%d)...", actor_handle, AUTHOR_FEED_LIMIT)
    feed = client.get_author_feed(
        actor=actor_handle,
        limit=AUTHOR_FEED_LIMIT,
        filter="posts_no_replies",
        include_pins=False,
    )
    return list(feed.feed or [])


def fetch_custom_feed(client: Client, feed_uri: str):
    logging.info("Custom feed ophalen %s (limit=%d)...", feed_uri, FEED_LIMIT)
    # low-level call (stabiel over meerdere atproto versies)
    res = client.app.bsky.feed.get_feed({"feed": feed_uri, "limit": FEED_LIMIT})
    return list(getattr(res, "feed", []) or [])


def pick_random_posts(valid_items: List, k: int) -> List:
    if not valid_items or k <= 0:
        return []
    if len(valid_items) <= k:
        chosen = list(valid_items)
    else:
        chosen = random.sample(valid_items, k=k)

    # oud -> nieuw reposten, zodat nieuw bovenaan komt
    chosen.sort(
        key=lambda it: (
            getattr(it.post, "indexed_at", None)
            or getattr(it.post, "created_at", "")
        )
    )
    return chosen


def unrepost_like_and_repost(client: Client, feed_item) -> None:
    post = feed_item.post
    viewer = getattr(post, "viewer", None)

    repost_uri = getattr(viewer, "repost", None) if viewer else None
    like_uri = getattr(viewer, "like", None) if viewer else None

    # eerst oude repost weg (als hij bestaat)
    if repost_uri:
        logging.info("  Oude repost verwijderen...")
        try:
            client.delete_repost(repost_uri)
            logging.info("  Oude repost verwijderd.")
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen: %s", e)

    # opnieuw reposten
    try:
        client.repost(uri=post.uri, cid=post.cid)
        logging.info("  Repost gelukt: %s", post.uri)
    except Exception as e:
        logging.error("  Repost mislukt: %s", e)
        return

    # like toevoegen (als nog niet geliked)
    if not like_uri:
        try:
            client.like(uri=post.uri, cid=post.cid)
            logging.info("  Like gelukt.")
        except Exception as e:
            logging.warning("  Like mislukt: %s", e)


def process_account(label: str) -> None:
    logging.info("=== Start account %s ===", label)
    client = get_client_for_account(label)
    if not client:
        return

    # 1) Feeds (3 -> 1)
    for feed_uri in FEED_URIS:
        feed_uri = (feed_uri or "").strip()
        if not feed_uri:
            continue

        logging.info("=== Account %s: FEED %s ===", label, feed_uri)
        try:
            items = fetch_custom_feed(client, feed_uri)
        except Exception as e:
            logging.error("Feed ophalen mislukt: %s", e)
            continue

        valid = [it for it in items if is_valid_post_item(it)]
        if not valid:
            logging.info("Geen geldige media-posts in feed, skip.")
            continue

        chosen = pick_random_posts(valid, RANDOM_PER_FEED)
        logging.info("Account %s: %d random post(s) uit feed.", label, len(chosen))

        for it in chosen:
            unrepost_like_and_repost(client, it)
            time.sleep(DELAY_SECONDS)

    # 2) Targets (10 -> 1)
    for target_handle in TARGET_HANDLES:
        target_handle = (target_handle or "").strip()
        if not target_handle:
            continue

        logging.info("=== Account %s: TARGET %s ===", label, target_handle)

        try:
            items = fetch_author_feed(client, target_handle)
        except Exception as e:
            logging.error("Author feed ophalen mislukt: %s", e)
            continue

        valid = [it for it in items if is_valid_post_item(it)]
        if not valid:
            logging.info("Geen geldige media-posts voor %s, skip.", target_handle)
            continue

        chosen = pick_random_posts(valid, RANDOM_PER_TARGET)
        logging.info("Account %s: %d random post(s) uit target %s.", label, len(chosen), target_handle)

        for it in chosen:
            unrepost_like_and_repost(client, it)
            time.sleep(DELAY_SECONDS)


def main():
    logging.info("=== Start Hollands Glorie RANDOM run ===")
    for label in ACCOUNT_KEYS:
        process_account(label)
    logging.info("=== Hollands Glorie RANDOM run voltooid ===")


if __name__ == "__main__":
    main()
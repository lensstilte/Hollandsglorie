import os
import time
import random
import logging
from typing import List, Optional

from atproto import Client

# --------------------------------------------------
# Config
# --------------------------------------------------
AUTHOR_FEED_LIMIT = 100          # max is 100 (atproto validatie)
FEED_LIMIT = 100                # max is 100
DELAY_SECONDS = 1               # door jou gevraagd
RANDOM_PER_SOURCE = 1           # 1 random post per target/feed

# Optioneel: als je een account hebt waarbij eigen reposts wél mogen (zoals bleuskybeauty)
ALLOW_SELF_REPOSTS_FOR = {"bleuskybeauty.bsky.social"}

# --------------------------------------------------
# Logging
# --------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# --------------------------------------------------
# Bot-accounts (env suffixen)
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

# Volgorde: 10 -> 1 (zodat 1 als laatste komt en bovenaan eindigt)
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
# Gebruik AT-URI als je 'm hebt, anders mag ook de bsky.app link (we proberen te parsen).
# --------------------------------------------------
FEED_URI_1 = "at://did:plc:jaka644beit3x4vmmg6yysw7/app.bsky.feed.generator/aaadqdb77ba62"
FEED_URI_2 = ""
FEED_URI_3 = ""

# Volgorde: 3 -> 1 (optioneel; zo komt FEED_URI_1 het laatst)
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
        logging.warning("Geen credentials gevonden voor %s, skip.", label)
        return None

    client = Client()
    try:
        client.login(username, password)
        logging.info("Ingelogd als %s (label=%s)", username, label)
        return client
    except Exception as e:
        logging.error("Login mislukt voor %s: %s", label, e)
        return None


def normalize_feed_uri(feed_uri_or_url: str) -> str:
    """
    Accepteert:
    - at://.../app.bsky.feed.generator/<rkey>
    - https://bsky.app/profile/<did-of-handle>/feed/<rkey>
    """
    s = (feed_uri_or_url or "").strip()
    if not s:
        return ""

    if s.startswith("at://"):
        return s

    # Probeer bsky.app link te parsen
    # Voorbeeld: https://bsky.app/profile/did:plc:XXX/feed/YYY
    marker = "/profile/"
    if marker in s and "/feed/" in s:
        try:
            after_profile = s.split(marker, 1)[1]
            actor_part, feed_part = after_profile.split("/feed/", 1)
            actor = actor_part.strip("/")
            rkey = feed_part.strip("/").split("/", 1)[0]
            return f"at://{actor}/app.bsky.feed.generator/{rkey}"
        except Exception:
            pass

    return s  # fallback


def fetch_author_feed(client: Client, actor_handle: str):
    feed = client.get_author_feed(
        actor=actor_handle,
        limit=AUTHOR_FEED_LIMIT,
        filter="posts_no_replies",
    )
    return list(feed.feed or [])


def fetch_generator_feed(client: Client, feed_uri_or_url: str):
    feed_uri = normalize_feed_uri(feed_uri_or_url)
    if not feed_uri:
        return []

    # Gebruik de low-level app call (werkt stabiel bij feeds)
    res = client.app.bsky.feed.get_feed({"feed": feed_uri, "limit": FEED_LIMIT})
    return list(getattr(res, "feed", []) or [])


def is_repost_item(feed_item) -> bool:
    return getattr(feed_item, "reason", None) is not None


def has_media(post_view) -> bool:
    """
    True als er images/video/external thumb is.
    We checken vooral op 'embed' aanwezigheid met media-velden.
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    # Veel voorkomende: embed.images
    images = getattr(embed, "images", None)
    if isinstance(images, list) and images:
        return True

    # Soms: embed.media.images (recordWithMedia)
    media = getattr(embed, "media", None)
    if media:
        media_images = getattr(media, "images", None)
        if isinstance(media_images, list) and media_images:
            return True

    # External met thumb (link preview)
    external = getattr(embed, "external", None)
    if external and getattr(external, "thumb", None):
        return True

    # Video embed kan per lib-versie anders heten; als er iets als "playlist" of "cid" zit:
    if getattr(embed, "playlist", None) or getattr(embed, "cid", None):
        return True

    return False


def is_quote_post(post_view) -> bool:
    """
    Quote posts hebben meestal embed.record of embed.record + media.
    We skippen alles waar embed een 'record' (quoted record) bevat.
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False
    if getattr(embed, "record", None) is not None:
        return True
    return False


def is_valid_post(feed_item, source_handle: Optional[str] = None) -> bool:
    """
    Regels:
    - moet media hebben
    - geen quote-post
    - geen repost-items (tenzij allow-self-reposts en post.author == source_handle)
    """
    post_view = feed_item.post

    if not has_media(post_view):
        return False

    if is_quote_post(post_view):
        return False

    if is_repost_item(feed_item):
        if source_handle and source_handle.lower() in ALLOW_SELF_REPOSTS_FOR:
            author = getattr(post_view, "author", None)
            if author and getattr(author, "handle", "").lower() == source_handle.lower():
                return True
        return False

    return True


def pick_random_posts(valid_items: List, k: int) -> List:
    if not valid_items or k <= 0:
        return []
    k = min(k, len(valid_items))
    return random.sample(valid_items, k=k)


def unrepost_like_and_repost(client: Client, feed_item) -> None:
    post = feed_item.post
    viewer = getattr(post, "viewer", None)

    repost_uri = getattr(viewer, "repost", None) if viewer else None
    like_uri = getattr(viewer, "like", None) if viewer else None

    # Eerst oude repost verwijderen (als die bestaat)
    if repost_uri:
        try:
            client.delete_repost(repost_uri)
            logging.info("  Oude repost verwijderd.")
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen: %s", e)

    # Repost opnieuw
    try:
        client.repost(uri=post.uri, cid=post.cid)
        logging.info("  Repost gelukt.")
    except Exception as e:
        logging.error("  Repost mislukt: %s", e)
        return

    # Like (alleen als nog niet geliked)
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

    # 1) Eerst FEEDS (3 -> 1)
    for feed_uri in FEED_URIS:
        if not feed_uri.strip():
            continue

        logging.info("=== Account %s: feed %s ===", label, feed_uri)
        try:
            items = fetch_generator_feed(client, feed_uri)
        except Exception as e:
            logging.error("Feed ophalen mislukt: %s", e)
            continue

        valid = [it for it in items if is_valid_post(it, source_handle=None)]
        if not valid:
            logging.info("Geen geldige media-posts in feed, skip.")
            continue

        chosen = pick_random_posts(valid, RANDOM_PER_SOURCE)
        for it in chosen:
            logging.info("  -> Repost+Like (random uit feed)")
            unrepost_like_and_repost(client, it)
            time.sleep(DELAY_SECONDS)

    # 2) Dan TARGET HANDLES (10 -> 1, zodat 1 als laatste komt)
    for target_handle in TARGET_HANDLES:
        if not target_handle.strip():
            continue

        logging.info("=== Account %s: target %s ===", label, target_handle)
        try:
            items = fetch_author_feed(client, target_handle)
        except Exception as e:
            logging.error("Author feed ophalen mislukt: %s", e)
            continue

        valid = [it for it in items if is_valid_post(it, source_handle=target_handle)]
        if not valid:
            logging.info("Geen geldige media-posts voor %s, skip.", target_handle)
            continue

        chosen = pick_random_posts(valid, RANDOM_PER_SOURCE)
        for it in chosen:
            logging.info("  -> Repost+Like (random uit target)")
            unrepost_like_and_repost(client, it)
            time.sleep(DELAY_SECONDS)


def main():
    logging.info("=== Start Hollands Glorie multi-target+feed run ===")
    for label in ACCOUNT_KEYS:
        process_account(label)
    logging.info("=== Hollands Glorie run voltooid ===")


if __name__ == "__main__":
    main()
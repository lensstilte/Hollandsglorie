import os
import random
import logging
from typing import List, Optional

from atproto import Client

# --------------------------------------------------
# Config
# --------------------------------------------------
AUTHOR_FEED_LIMIT = 100  # ⬅️ HIER pas je het aantal terug te kijken posts aan

# --------------------------------------------------
# Logging basic setup
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
# TARGET HANDLES (10 slots, leeg = skip)
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
# Helpers
# --------------------------------------------------
def get_client_for_account(label: str) -> Optional[Client]:
    username = os.getenv(f"BSKY_USERNAME_{label}")
    password = os.getenv(f"BSKY_PASSWORD_{label}")

    if not username or not password:
        logging.warning(
            "Geen credentials gevonden voor %s, account wordt geskipt.",
            label,
        )
        return None

    client = Client()
    try:
        client.login(username, password)
        logging.info("Ingelogd als %s (label=%s)", username, label)
    except Exception as e:
        logging.error("Login mislukt voor %s: %s", label, e)
        return None

    return client


def fetch_recent_posts(client: Client, actor_handle: str):
    logging.info(
        "Posts ophalen van %s (limit=%d)...",
        actor_handle,
        AUTHOR_FEED_LIMIT,
    )
    feed = client.get_author_feed(
        actor=actor_handle,
        limit=AUTHOR_FEED_LIMIT,
        filter="posts_no_replies",
    )
    return list(feed.feed or [])


def has_media(post_view) -> bool:
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    images = getattr(embed, "images", None)
    if isinstance(images, list) and images:
        return True

    media = getattr(embed, "media", None)
    if media:
        media_images = getattr(media, "images", None)
        if isinstance(media_images, list) and media_images:
            return True

    external = getattr(embed, "external", None)
    if external and getattr(external, "thumb", None):
        return True

    return False


def is_valid_post_for_target(feed_post, target_handle: str) -> bool:
    post_view = feed_post.post

    if not has_media(post_view):
        return False

    is_repost = getattr(feed_post, "reason", None) is not None
    if not is_repost:
        return True

    target_lower = target_handle.lower()
    if target_lower == "bleuskybeauty.bsky.social":
        author = getattr(post_view, "author", None)
        return (
            author
            and author.handle.lower() == target_lower
        )

    return False


def choose_posts_for_run(feed_posts: List, num_random_older: int = 2) -> List:
    if not feed_posts:
        return []

    newest = feed_posts[0]
    older = feed_posts[1:]

    selected = [newest]

    if older:
        selected.extend(
            random.sample(older, k=min(num_random_older, len(older)))
        )

    selected.sort(
        key=lambda fp: (
            getattr(fp.post, "indexed_at", None)
            or getattr(fp.post, "created_at", "")
        )
    )
    return selected


def unrepost_like_and_repost(client: Client, feed_post) -> None:
    post = feed_post.post
    viewer = getattr(post, "viewer", None)

    repost_uri = getattr(viewer, "repost", None) if viewer else None
    like_uri = getattr(viewer, "like", None) if viewer else None

    if repost_uri:
        logging.info("  Oude repost verwijderen...")
        try:
            client.delete_repost(repost_uri)
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen: %s", e)

    try:
        client.repost(uri=post.uri, cid=post.cid)
        logging.info("  Repost gelukt.")
    except Exception as e:
        logging.error("  Repost mislukt: %s", e)
        return

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

    for target_handle in TARGET_HANDLES:
        if not target_handle:
            continue

        logging.info("=== Account %s: target %s ===", label, target_handle)

        try:
            feed_posts = fetch_recent_posts(client, target_handle)
        except Exception as e:
            logging.error("Feed ophalen mislukt: %s", e)
            continue

        valid_posts = [
            fp for fp in feed_posts
            if is_valid_post_for_target(fp, target_handle)
        ]

        if not valid_posts:
            logging.info("Geen geldige media-posts, skip.")
            continue

        to_repost = choose_posts_for_run(valid_posts)

        logging.info(
            "Account %s repost %d posts voor %s.",
            label,
            len(to_repost),
            target_handle,
        )

        for fp in to_repost:
            unrepost_like_and_repost(client, fp)


def main():
    logging.info("=== Start Hollands Glorie multi-target run ===")
    for label in ACCOUNT_KEYS:
        process_account(label)
    logging.info("=== Hollands Glorie run voltooid ===")


if __name__ == "__main__":
    main()
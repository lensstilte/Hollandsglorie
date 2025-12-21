import os
import random
import time
import logging
from typing import List, Optional

from atproto import Client

# -----------------------------
# CONFIG
# -----------------------------
LIST_URI = "at://did:plc:cxrt7ggxkamgzxa47cggtees/app.bsky.graph.list/3majejgaw3m2q"

# Per listed account: pak random uit de laatste N geldige (eigen) posts
LAST_N_PER_USER = 10

# Hoeveel author-feed items ophalen om aan LAST_N_PER_USER te komen
AUTHOR_FEED_LIMIT = 100  # max 100

DELAY_SECONDS = 1

# Bot-accounts (secrets suffixen)
ACCOUNT_KEYS = [
    "BEAUTYFAN",
    "HOTBLEUSKY",
    "DMPHOTOS",
]

# -----------------------------
# LOGGING
# -----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# -----------------------------
# HELPERS
# -----------------------------
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
    """True als post een image/video/external thumb embed heeft."""
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    # Images embed
    images = getattr(embed, "images", None)
    if isinstance(images, list) and images:
        return True

    # Media embed variant (soms in embed.media.images)
    media = getattr(embed, "media", None)
    if media:
        media_images = getattr(media, "images", None)
        if isinstance(media_images, list) and media_images:
            return True

    # External embed met thumb (link preview met afbeelding)
    external = getattr(embed, "external", None)
    if external and getattr(external, "thumb", None):
        return True

    return False


def is_original_own_post(feed_item, expected_handle: str) -> bool:
    """
    Alleen echte eigen posts:
    - geen repost (feed_item.reason is None)
    - author.handle == expected_handle
    - geen reply (record.reply is None)
    """
    if getattr(feed_item, "reason", None) is not None:
        return False

    post_view = feed_item.post
    author = getattr(post_view, "author", None)
    if not author or getattr(author, "handle", "").lower() != expected_handle.lower():
        return False

    record = getattr(post_view, "record", None)
    if record and getattr(record, "reply", None):
        return False

    return True


def fetch_list_members_handles(client: Client) -> List[str]:
    """
    Haal alle members uit de Bluesky lijst.
    Let op: paginatie met cursor.
    """
    handles: List[str] = []
    cursor = None

    while True:
        params = {"list": LIST_URI, "limit": 100}
        if cursor:
            params["cursor"] = cursor

        resp = client.app.bsky.graph.get_list_members(params)
        items = getattr(resp, "items", None) or []
        for it in items:
            subject = getattr(it, "subject", None)
            h = getattr(subject, "handle", None)
            if h:
                handles.append(h)

        cursor = getattr(resp, "cursor", None)
        if not cursor:
            break

    # uniek, maar volgorde behouden
    seen = set()
    uniq = []
    for h in handles:
        if h not in seen:
            uniq.append(h)
            seen.add(h)

    return uniq


def fetch_candidate_posts_for_handle(client: Client, handle: str) -> List:
    """
    Pak author feed en filter:
    - echte eigen posts (geen repost/reply)
    - met media
    Neem de laatste LAST_N_PER_USER van die geldige posts.
    """
    feed = client.get_author_feed(
        actor=handle,
        limit=AUTHOR_FEED_LIMIT,
        filter="posts_no_replies",
        include_pins=False,
    )
    items = list(feed.feed or [])

    valid = []
    for it in items:
        if not is_original_own_post(it, expected_handle=handle):
            continue
        if not has_media(it.post):
            continue
        valid.append(it)
        if len(valid) >= LAST_N_PER_USER:
            break

    return valid


def unrepost_like_and_repost(client: Client, feed_item) -> None:
    post = feed_item.post
    viewer = getattr(post, "viewer", None)

    repost_uri = getattr(viewer, "repost", None) if viewer else None
    like_uri = getattr(viewer, "like", None) if viewer else None

    # eerst oude repost weg (zodat opnieuw bovenaan kan)
    if repost_uri:
        try:
            logging.info("  Oude repost verwijderen...")
            client.delete_repost(repost_uri)
            logging.info("  Oude repost verwijderd.")
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen: %s", e)

    # repost
    try:
        client.repost(uri=post.uri, cid=post.cid)
        logging.info("  Repost gelukt: %s", post.uri)
    except Exception as e:
        logging.error("  Repost mislukt: %s", e)
        return

    # like
    if not like_uri:
        try:
            client.like(uri=post.uri, cid=post.cid)
            logging.info("  Like gelukt.")
        except Exception as e:
            logging.warning("  Like mislukt: %s", e)


# -----------------------------
# MAIN FLOW
# -----------------------------
def process_account(label: str) -> None:
    logging.info("=== Start bot-account %s ===", label)
    client = get_client_for_account(label)
    if not client:
        return

    # 1) lijst members ophalen
    try:
        members = fetch_list_members_handles(client)
        logging.info("Lijst bevat %d accounts.", len(members))
    except Exception as e:
        logging.error("Kon lijst members niet ophalen: %s", e)
        return

    # 2) per member: random uit laatste 10 eigen media posts
    for member_handle in members:
        try:
            candidates = fetch_candidate_posts_for_handle(client, member_handle)
        except Exception as e:
            logging.warning("Kon posts niet ophalen voor %s: %s", member_handle, e)
            continue

        if not candidates:
            logging.info("Geen geldige posts voor %s (geen eigen media in laatste %d), skip.", member_handle, LAST_N_PER_USER)
            continue

        pick = random.choice(candidates)
        logging.info("Target %s -> random pick uit %d kandidaten.", member_handle, len(candidates))

        unrepost_like_and_repost(client, pick)
        time.sleep(DELAY_SECONDS)


def main():
    logging.info("=== Start PHOTO ACCOUNTS run ===")
    for label in ACCOUNT_KEYS:
        process_account(label)
    logging.info("=== PHOTO ACCOUNTS run klaar ===")


if __name__ == "__main__":
    main()
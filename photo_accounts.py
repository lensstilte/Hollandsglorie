import os
import time
import random
import logging
from typing import List, Optional, Iterable, Set
from urllib.parse import urlparse

from atproto import Client

# ----------------------------
# CONFIG
# ----------------------------
# Jouw lijst URL:
LIST_URL = "https://bsky.app/profile/did:plc:cxrt7ggxkamgzxa47cggtees/lists/3majejgaw3m2q"

# Hoeveel reposts per bot-account per run
MAX_REPOSTS_PER_RUN = 25

# Pak per list-member random uit laatste N eigen posts (minder zwaar)
PICK_FROM_LAST_N = 5

# Hoeveel author feed items we ophalen om die laatste N eigen+media posts te vinden
AUTHOR_FEED_LIMIT = 50  # veilig; we filteren daarna terug naar laatste 5 geschikte

# kleine delay (jij wilde 1 seconde)
DELAY_SECONDS = 1

# Bot accounts (secrets suffixen)
ACCOUNT_KEYS = ["BEAUTYFAN", "HOTBLEUSKY", "DMPHOTOS"]

# ----------------------------
# LOGGING
# ----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ----------------------------
# LIST URI helpers
# ----------------------------
def list_url_to_at_uri(list_url: str) -> str:
    """
    Zet bsky.app lijst-URL om naar at://... list URI dat de API verwacht.
    Voorbeeld:
    https://bsky.app/profile/did:plc:XXXX/lists/YYYY
    -> at://did:plc:XXXX/app.bsky.graph.list/YYYY
    """
    p = urlparse(list_url)
    parts = [x for x in p.path.split("/") if x]
    # verwacht: ["profile", "<did>", "lists", "<rkey>"]
    if len(parts) < 4 or parts[0] != "profile" or parts[2] != "lists":
        raise ValueError(f"Onverwachte LIST_URL vorm: {list_url}")

    did = parts[1]
    rkey = parts[3]
    return f"at://{did}/app.bsky.graph.list/{rkey}"


def iter_list_members(client: Client, list_uri: str, page_limit: int = 100) -> Iterable[str]:
    """
    Yield member handles uit een Bluesky lijst (paginated).
    We geven handles terug (string).
    """
    cursor = None
    while True:
        resp = client.app.bsky.graph.get_list({"list": list_uri, "limit": page_limit, "cursor": cursor})
        items = getattr(resp, "items", []) or []
        for it in items:
            subj = getattr(it, "subject", None)
            if subj and getattr(subj, "handle", None):
                yield subj.handle

        cursor = getattr(resp, "cursor", None)
        if not cursor:
            break


# ----------------------------
# Post filters
# ----------------------------
def has_media(post_view) -> bool:
    """
    True als er embed media is (images / media.images / external.thumb).
    """
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


def is_quote_post(post_view) -> bool:
    """
    Quote posts hebben meestal een embed.record / embed.record.record / embed.record.uri structuur.
    We checken breed zodat het blijft werken bij modelverschillen.
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False
    record = getattr(embed, "record", None)
    if record:
        return True
    # soms zit het onder embed.media of andere subtypes; voorzichtig:
    maybe_record = getattr(getattr(embed, "media", None), "record", None)
    return bool(maybe_record)


def is_original_post(feed_item) -> bool:
    """
    True als item géén repost is in author feed.
    In get_author_feed zie je reposts via item.reason != None.
    """
    return getattr(feed_item, "reason", None) is None


def is_valid_candidate(feed_item) -> bool:
    """
    Kandidaten:
    - original (geen repost)
    - heeft media (geen text-only)
    - geen quote post
    """
    post_view = feed_item.post
    if not is_original_post(feed_item):
        return False
    if not has_media(post_view):
        return False
    if is_quote_post(post_view):
        return False
    return True


# ----------------------------
# Client / auth
# ----------------------------
def get_client_for_account(label: str) -> Optional[Client]:
    username = os.getenv(f"BSKY_USERNAME_{label}")
    password = os.getenv(f"BSKY_PASSWORD_{label}")

    if not username or not password:
        logging.warning("Geen credentials voor %s, skip.", label)
        return None

    client = Client()
    try:
        client.login(username, password)
        logging.info("Ingelogd (label=%s)", label)
        return client
    except Exception as e:
        logging.error("Login mislukt %s: %s", label, e)
        return None


def fetch_author_feed(client: Client, handle: str):
    return client.get_author_feed(
        actor=handle,
        limit=AUTHOR_FEED_LIMIT,
        filter="posts_no_replies",
    ).feed or []


def pick_random_from_last_n_valid(client: Client, handle: str, n: int) -> Optional[object]:
    """
    Haal author feed en pak random uit de laatste n geldige posts.
    """
    try:
        feed = fetch_author_feed(client, handle)
    except Exception:
        return None

    valid = [it for it in feed if is_valid_candidate(it)]
    if not valid:
        return None

    pool = valid[: max(1, min(n, len(valid)))]
    return random.choice(pool)


def unrepost_if_needed(client: Client, post_view) -> None:
    viewer = getattr(post_view, "viewer", None)
    repost_uri = getattr(viewer, "repost", None) if viewer else None
    if repost_uri:
        try:
            client.delete_repost(repost_uri)
        except Exception:
            pass


def repost_and_like(client: Client, post_view) -> bool:
    """
    Repost + like (like alleen als nog niet geliked)
    """
    try:
        client.repost(uri=post_view.uri, cid=post_view.cid)
    except Exception:
        return False

    viewer = getattr(post_view, "viewer", None)
    like_uri = getattr(viewer, "like", None) if viewer else None
    if not like_uri:
        try:
            client.like(uri=post_view.uri, cid=post_view.cid)
        except Exception:
            pass

    return True


def process_account(label: str, list_uri: str, members: List[str]) -> None:
    client = get_client_for_account(label)
    if not client:
        return

    # Shuffle voor randomness, maar “iedereen kans” via round-robin
    members_shuffled = members[:]
    random.shuffle(members_shuffled)

    reposted_count = 0
    tried_pairs: Set[str] = set()

    # Round-robin: 1 per member per ronde, tot 25
    while reposted_count < MAX_REPOSTS_PER_RUN:
        progressed_this_round = False

        for handle in members_shuffled:
            if reposted_count >= MAX_REPOSTS_PER_RUN:
                break

            key = f"{handle}"
            # per run max 1 poging per member per ronde; maar we kunnen meerdere rondes doen
            # om aan 25 te komen als lijst klein is.
            if key in tried_pairs and len(members_shuffled) >= MAX_REPOSTS_PER_RUN:
                # bij grote lijst: 1 kans is genoeg
                continue

            tried_pairs.add(key)

            item = pick_random_from_last_n_valid(client, handle, PICK_FROM_LAST_N)
            if not item:
                continue

            post_view = item.post

            # unrepost -> repost -> like
            unrepost_if_needed(client, post_view)
            ok = repost_and_like(client, post_view)
            if ok:
                reposted_count += 1
                progressed_this_round = True

            time.sleep(DELAY_SECONDS)

        if not progressed_this_round:
            # Niemand leverde nog een geldige post op -> stop
            break

    logging.info("Account %s klaar: %d reposts.", label, reposted_count)


def main():
    list_uri = list_url_to_at_uri(LIST_URL)
    logging.info("=== Photo Accounts run ===")
    logging.info("List URI: %s", list_uri)

    # We halen members één keer op en hergebruiken voor alle bot-accounts
    # (scheelt calls en is sneller/goedkoper)
    # Hiervoor gebruiken we een tijdelijke client: eerste account die werkt, of we maken een anonieme login nodig?
    # Bluesky list lezen kan login vereisen -> we gebruiken gewoon de eerste account die werkt.
    tmp_client = None
    for label in ACCOUNT_KEYS:
        tmp_client = get_client_for_account(label)
        if tmp_client:
            break

    if not tmp_client:
        logging.error("Geen enkele bot-account kon inloggen, stop.")
        return

    members = list(dict.fromkeys(iter_list_members(tmp_client, list_uri)))  # unique, behoud volgorde
    logging.info("Lijst members gevonden: %d", len(members))

    if not members:
        logging.warning("Geen members in lijst, stop.")
        return

    # Nu runnen we voor elk bot-account.
    # (We loggen niet alle handles voor privacy; alleen aantallen.)
    for label in ACCOUNT_KEYS:
        process_account(label, list_uri, members)

    logging.info("=== Photo Accounts run voltooid ===")


if __name__ == "__main__":
    main()
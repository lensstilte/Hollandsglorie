import os
import random
import logging
from typing import List, Optional

from atproto import Client

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
# Je hebt deze zelf al ingevuld; pas ze hier aan wanneer nodig.
# --------------------------------------------------
TARGET_HANDLE_1 = "@dmphotos.bsky.social"
TARGET_HANDLE_2 = "theysaidnothing.bsky.social"
TARGET_HANDLE_3 = "wsimonde.bsky.social"
TARGET_HANDLE_4 = "velvetdesire.bsky.social"
TARGET_HANDLE_5 = "sensushots.bsky.social"
TARGET_HANDLE_6 = "steefschrijber1970.bsky.social"
TARGET_HANDLE_7 = "damienmanson.bsky.social"
TARGET_HANDLE_8 = "boxy0075.bsky.social"
TARGET_HANDLE_9 = ""
TARGET_HANDLE_10 = ""

# Volgorde: 10 -> 1, zodat 1 als laatste wordt gepost en dus bovenaan staat.
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
    """
    Haal username/password uit env en log in.
    Als er geen secrets zijn ingevuld voor dit account: skippen.
    """
    username = os.getenv(f"BSKY_USERNAME_{label}")
    password = os.getenv(f"BSKY_PASSWORD_{label}")

    if not username or not password:
        logging.warning(
            "Geen credentials gevonden voor %s (username/password), account wordt geskipt.",
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


def fetch_recent_posts(client: Client, actor_handle: str, limit: int = 50):
    """
    Haal recente posts van de target-acteur op.
    We gebruiken 'posts_no_replies' zodat je alleen eigen posts / reposts krijgt, geen replies.
    """
    logging.info("Posts ophalen van %s (limit=%d)...", actor_handle, limit)
    feed = client.get_author_feed(
        actor=actor_handle,
        limit=limit,
        filter="posts_no_replies",
    )
    return list(feed.feed or [])


def has_media(post_view) -> bool:
    """
    True als de post media bevat (foto / video / embed),
    False als het tekst-only is.
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    # Direct images
    images = getattr(embed, "images", None)
    if isinstance(images, list) and images:
        return True

    # Sommige embed-varianten hebben .media.images
    media = getattr(embed, "media", None)
    if media is not None:
        media_images = getattr(media, "images", None)
        if isinstance(media_images, list) and media_images:
            return True

    # Externe embed met thumbnail (kan bijv. video zijn)
    external = getattr(embed, "external", None)
    if external is not None:
        thumb = getattr(external, "thumb", None)
        if thumb:
            return True

    return False


def is_valid_post_for_target(feed_post, target_handle: str) -> bool:
    """
    Filter:
    - Geen tekst-only -> moet media hebben.
    - Geen reposts, behalve:
        * target = bleuskybeauty.bsky.social
          -> repost is toegestaan als de originele auteur dezelfde handle is.
    """
    post_view = feed_post.post

    # 1) vereist media
    if not has_media(post_view):
        return False

    # 2) check of het een repost is
    reason = getattr(feed_post, "reason", None)
    is_repost = reason is not None

    # Normaal: géén reposts
    if not is_repost:
        return True

    # Uitzondering: bleuskybeauty.bsky.social
    target_lower = target_handle.lower()
    if target_lower == "bleuskybeauty.bsky.social":
        author = getattr(post_view, "author", None)
        author_handle = getattr(author, "handle", "").lower() if author else ""
        # Alleen toestaan als originele auteur dezelfde handle is
        if author_handle == target_lower:
            return True
        return False

    # Voor alle andere targets: reposts skippen
    return False


def choose_posts_for_run(feed_posts: List, num_random_older: int = 2) -> List:
    """
    input: lijst feed_posts (nieuwste eerst, zoals Bluesky ze geeft)
    - kies nieuwste (index 0)
    - plus num_random_older willekeurige oudere uit de rest
    - sorteert de selectie van oud -> nieuw zodat de oudste als eerste gepost wordt
    """
    if not feed_posts:
        return []

    newest = feed_posts[0]
    older = feed_posts[1:]

    selected = [newest]

    if older:
        k = min(num_random_older, len(older))
        selected.extend(random.sample(older, k=k))

    # Nu sorteren we ze van oud -> nieuw op indexed_at (fallback: created_at/empty string)
    def _sort_key(fp):
        pv = fp.post
        return (
            getattr(pv, "indexed_at", None)
            or getattr(pv, "created_at", None)
            or ""
        )

    selected.sort(key=_sort_key)
    return selected


def unrepost_like_and_repost(client: Client, feed_post) -> None:
    """
    - Als deze post al is gerepost door deze bot: oude repost verwijderen.
    - Daarna opnieuw repost-en.
    - Als er nog geen like is, ook liken.
    """
    post_view = feed_post.post
    uri = post_view.uri
    cid = post_view.cid

    viewer = getattr(post_view, "viewer", None)
    repost_uri = getattr(viewer, "repost", None) if viewer else None
    like_uri = getattr(viewer, "like", None) if viewer else None

    # Oude repost verwijderen
    if repost_uri:
        logging.info("  Oude repost gevonden (%s), verwijderen...", repost_uri)
        try:
            client.delete_repost(repost_uri)
            logging.info("  Oude repost verwijderd.")
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen (%s): %s", repost_uri, e)

    # Nieuwe repost
    logging.info("  Nieuwe repost van %s...", uri)
    try:
        client.repost(uri=uri, cid=cid)
        logging.info("  Repost gelukt.")
    except Exception as e:
        logging.error("  Repost mislukt voor %s: %s", uri, e)
        return

    # Like (alleen als nog geen like)
    if like_uri:
        logging.info("  Post was al geliked, laten we zo.")
    else:
        logging.info("  Nog geen like, nu liken...")
        try:
            client.like(uri=uri, cid=cid)
            logging.info("  Like gelukt.")
        except Exception as e:
            logging.warning("  Like mislukt voor %s: %s", uri, e)


def process_account(label: str) -> None:
    """
    Verwerk één bot-account (BEAUTYFAN / HOTBLEUSKY / DMPHOTOS):
    - login
    - per niet-lege target handle:
        * posts ophalen
        * filteren op 'geldige' posts (media + geen repost, behalve special-case BleuskyBeauty)
        * nieuwste + 2 random oudere kiezen
        * unrepost + repost + like
    """
    logging.info("=== Start account %s ===", label)
    client = get_client_for_account(label)
    if not client:
        logging.warning("Account %s wordt overgeslagen (geen login).", label)
        return

    for target_handle in TARGET_HANDLES:
        if not target_handle:
            continue  # lege slot overslaan

        logging.info("=== Account %s: target %s ===", label, target_handle)

        try:
            feed_posts = fetch_recent_posts(client, target_handle)
        except Exception as e:
            logging.error(
                "Kon feed voor %s niet ophalen bij account %s: %s",
                target_handle,
                label,
                e,
            )
            continue

        # Filter op media + (geen repost, behalve BleuskyBeauty)
        valid_posts = [
            fp for fp in feed_posts
            if is_valid_post_for_target(fp, target_handle)
        ]

        if not valid_posts:
            logging.info(
                "Geen geldige media-posts voor %s (geen reposts/geen tekst-only), skip.",
                target_handle,
            )
            continue

        to_repost = choose_posts_for_run(valid_posts, num_random_older=2)

        logging.info(
            "Account %s gaat %d posts (nieuwste + random oudere, van oud->nieuw) (opnieuw) repost-en voor target %s.",
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
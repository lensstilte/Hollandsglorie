import os
import random
import logging
from typing import List, Optional

from atproto import Client

# ====== INSTELLINGEN ======

# Bot-accounts (zorg dat je hiervoor secrets aanmaakt!)
ACCOUNT_KEYS = [
    "BEAUTYFAN",
    "HOTBLEUSKY",
    "DMPHOTOS",  # je moet BSKY_USERNAME_DMPHOTOS / BSKY_PASSWORD_DMPHOTOS toevoegen in GitHub
]

# Targets 1 t/m 10: vul hier de Bluesky handles in.
# Laat leeg ("") als je die target (nog) niet wilt gebruiken.
TARGET_HANDLE_1 = ""   # bv: "creator1.bsky.social"
TARGET_HANDLE_2 = ""
TARGET_HANDLE_3 = ""
TARGET_HANDLE_4 = ""
TARGET_HANDLE_5 = ""
TARGET_HANDLE_6 = ""
TARGET_HANDLE_7 = ""
TARGET_HANDLE_8 = ""
TARGET_HANDLE_9 = ""
TARGET_HANDLE_10 = ""

TARGET_HANDLES: List[str] = [
    TARGET_HANDLE_1,
    TARGET_HANDLE_2,
    TARGET_HANDLE_3,
    TARGET_HANDLE_4,
    TARGET_HANDLE_5,
    TARGET_HANDLE_6,
    TARGET_HANDLE_7,
    TARGET_HANDLE_8,
    TARGET_HANDLE_9,
    TARGET_HANDLE_10,
]

# Hoeveel random oudere posts naast de nieuwste
NUM_RANDOM_OLDER = 2

# ====== LOGGING ======

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ====== HULPFUNCTIES ======

def get_client_for_account(label: str) -> Optional[Client]:
    """
    Login voor één botaccount via env vars:
    BSKY_USERNAME_<LABEL>, BSKY_PASSWORD_<LABEL>
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
    Haal recente posts van de target op.
    - 'posts_no_replies' => geen replies
    - later filteren we nog op:
        * geen reposts
        * wél media
    """
    logging.info("Posts ophalen van %s (limit=%d)...", actor_handle, limit)
    feed = client.get_author_feed(
        actor=actor_handle,
        limit=limit,
        filter="posts_no_replies",
    )
    return list(feed.feed or [])


def is_original_post(feed_post) -> bool:
    """
    True als dit géén repost is.
    Bij een repost is feed_post.reason meestal gezet (reasonRepost).
    """
    reason = getattr(feed_post, "reason", None)
    if reason is not None:
        # Dit is een repost (reden in feed bijv. reasonRepost)
        return False
    return True


def has_media(post_view) -> bool:
    """
    True als de post media bevat (foto of video).
    We checken embed.images / embed.video of embed.media.images / embed.media.video.
    """
    embed = getattr(post_view, "embed", None)
    if not embed:
        return False

    # direct images / video
    if getattr(embed, "images", None):
        return True
    if getattr(embed, "video", None):
        return True

    # record-with-media varianten
    media = getattr(embed, "media", None)
    if media:
        if getattr(media, "images", None):
            return True
        if getattr(media, "video", None):
            return True

    return False


def filter_valid_posts(feed_posts):
    """
    Filter:
    - alleen originele posts (geen repost)
    - mét media (geen tekst-only)
    """
    valid = []
    for feed_post in feed_posts:
        post_view = feed_post.post
        if not is_original_post(feed_post):
            continue
        if not has_media(post_view):
            continue
        valid.append(feed_post)
    return valid


def sort_old_to_new(posts):
    """
    Sorteer posts van oud -> nieuw op indexed_at,
    zodat we die volgorde kunnen posten (nieuwste komt dan als laatste / bovenaan).
    """
    def get_indexed_at(fp):
        post_view = fp.post
        return getattr(post_view, "indexed_at", "") or ""

    return sorted(posts, key=get_indexed_at)


def choose_posts_for_run(feed_posts, num_random_older: int = 2):
    """
    Kies:
    - altijd de nieuwste post (index 0)
    - plus num_random_older willekeurige oudere posts
    - daarna sorteren we de selectie van oud -> nieuw,
      zodat de laatste daadwerkelijk de nieuwste is (en bovenaan in de timeline verschijnt).
    """
    if not feed_posts:
        return []

    selected = []

    newest = feed_posts[0]
    selected.append(newest)

    older = feed_posts[1:]
    if older:
        k = min(num_random_older, len(older))
        random_older = random.sample(older, k=k)
        selected.extend(random_older)

    # nu van oud naar nieuw
    selected_sorted = sort_old_to_new(selected)
    return selected_sorted


def unrepost_and_repost_with_like(client: Client, feed_post) -> None:
    """
    - Als deze bot-account de post al eens gerepost heeft: verwijder die repost eerst.
    - Daarna opnieuw repost.
    - Daarna meteen liken (als nog niet geliked).
    """
    post_view = feed_post.post
    uri = post_view.uri
    cid = post_view.cid
    viewer = getattr(post_view, "viewer", None)

    # bestaande repost verwijderen
    repost_uri = getattr(viewer, "repost", None) if viewer else None
    if repost_uri:
        logging.info("  Oude repost gevonden (%s), verwijderen...", repost_uri)
        try:
            client.delete_repost(repost_uri)
            logging.info("  Oude repost verwijderd.")
        except Exception as e:
            logging.warning("  Kon oude repost niet verwijderen (%s): %s", repost_uri, e)

    # opnieuw repost
    logging.info("  Nieuwe repost van %s...", uri)
    try:
        client.repost(uri=uri, cid=cid)
        logging.info("  Repost gelukt.")
    except Exception as e:
        logging.error("  Repost mislukt voor %s: %s", uri, e)
        return

    # liken als dat nog niet gebeurd is
    like_uri = getattr(viewer, "like", None) if viewer else None
    if not like_uri:
        logging.info("  Nog geen like, nu liken...")
        try:
            client.like(uri=uri, cid=cid)
            logging.info("  Like gelukt.")
        except Exception as e:
            logging.warning("  Like mislukt voor %s: %s", uri, e)
    else:
        logging.info("  Post was al geliked, laten we zo.")


def process_target_for_account(client: Client, label: str, target_handle: str) -> None:
    """
    Verwerk één target-handle voor één bot-account.
    """
    logging.info("=== Account %s: target %s ===", label, target_handle)

    try:
        feed_posts = fetch_recent_posts(client, target_handle, limit=50)
    except Exception as e:
        logging.error(
            "Kon feed voor %s niet ophalen bij account %s: %s",
            target_handle,
            label,
            e,
        )
        return

    if not feed_posts:
        logging.info("Geen posts gevonden voor %s, account %s slaat target over.", target_handle, label)
        return

    valid_posts = filter_valid_posts(feed_posts)
    if not valid_posts:
        logging.info("Geen geldige media-posts voor %s (geen reposts/geen tekst-only), skip.", target_handle)
        return

    to_repost = choose_posts_for_run(valid_posts, num_random_older=NUM_RANDOM_OLDER)

    logging.info(
        "Account %s gaat %d posts (nieuwste + random oudere, van oud->nieuw) (opnieuw) repost-en voor target %s.",
        label,
        len(to_repost),
        target_handle,
    )

    for feed_post in to_repost:
        unrepost_and_repost_with_like(client, feed_post)


def main():
    logging.info("=== Start Hollands Glorie multi-target run ===")

    # Loop over bot-accounts
    for label in ACCOUNT_KEYS:
        logging.info("=== Start account %s ===", label)
        client = get_client_for_account(label)
        if not client:
            logging.warning("Account %s wordt overgeslagen (geen client).", label)
            continue

        # Targets van 10 -> 1,
        # zodat target 1 als laatste gepost wordt en dus bovenaan komt.
        for idx in range(len(TARGET_HANDLES), 0, -1):  # 10, 9, ..., 1
            handle = TARGET_HANDLES[idx - 1]
            if not handle or not handle.strip():
                continue

            process_target_for_account(client, label, handle.strip())

    logging.info("=== Hollands Glorie run voltooid ===")


if __name__ == "__main__":
    main()

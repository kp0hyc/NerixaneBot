import math
import unicodedata

from .updates import *
from .config import MyBotState

from datetime import timezone

from telethon.utils import get_peer_id
from telethon.tl.types import (
    PeerChat, 
    PeerChannel, 
    ReactionEmoji, 
    ReactionCustomEmoji,
)

async def on_message_reaction(mc, event):
    print("got reaction")
    print(event)
    peer = event.peer
    if isinstance(peer, PeerChat):
        chat_id = peer.chat_id
    elif isinstance(peer, PeerChannel):
        chat_id = peer.channel_id
    else:
        return
    
    chat_id = get_peer_id(peer)
    print("chat_id: ", chat_id)
    if chat_id != ORIG_CHANNEL_ID:
        return

    msg_id = event.msg_id

    msg = await mc.get_messages(chat_id, ids=msg_id)
    
    if not msg:
        return
    
    print("orig_msg: ", msg)

    author_id = None
    if hasattr(msg, "from_id"):
        if msg.from_id != None:
            author_id = get_peer_id(msg.from_id)
        else:
            author_id = TARGET_USER
    else:
        return

    if not hasattr(event, "actor"):
        return
    reactor_id = get_peer_id(event.actor)

    if reactor_id == author_id:
        return

    print("author_id: ", author_id)
    print("reactor_id: ", reactor_id)
    
    if reactor_id != ORIG_CHANNEL_ID and count_total_rating(MyBotState.social_rating, reactor_id) < -100:
        print("too low social rating")
        return

    if reactor_id != ORIG_CHANNEL_ID and reactor_id != TARGET_USER:
        join_date = await get_join_date(mc, ORIG_CHANNEL_ID, reactor_id)
        if not join_date:
            print("user not in the chat to count rating")
            return
        now = datetime.now(timezone.utc)
        if (now - join_date).days <= 3:
            print("member is too new")
            return

    old = getattr(event, 'old_reactions', []) or []
    new = getattr(event, 'new_reactions', []) or getattr(event, 'new_reaction', [])

    old_set = set(extract_emojis(old))
    new_set = set(extract_emojis(new))
    
    added   = new_set - old_set
    removed = old_set - new_set

    print(f"added reactions: {added}")
    print(f"removed reactions: {removed}")

    delta = 0
    for e in added:
        delta += get_emoji_weight(e)
    for e in removed:
        delta -= get_emoji_weight(e)
        
    if delta == 0:
        print("delta is zero, we quit")
        if reactor_id in (TARGET_USER, ORIG_CHANNEL_ID):
            print("================ALARM WE MISSED NERIXANE REACTION!!!===============")

        return

    entry = MyBotState.social_rating.setdefault(author_id, {
        "reactor_counts": {},
        "total_reacts":    0,
        "additional_chat": 0,
        "additional_neri": 0,
        "additional_self": 0,
        "boosts":          0,
        "manual_rating":   0,
        "reactor_dates":   [],
    })

    if reactor_id not in (TARGET_USER, ORIG_CHANNEL_ID) and author_id != TARGET_USER:
        rc = entry["reactor_counts"]
        reactor_spec_data = rc.setdefault(
            reactor_id,
            {"count": 0, "value": 0, "reactor_dates": []}
        )

        reactor_data = MyBotState.social_rating.setdefault(reactor_id, {
            "reactor_counts": {},
            "total_reacts":    0,
            "additional_chat": 0,
            "additional_neri": 0,
            "additional_self": 0,
            "boosts":          0,
            "manual_rating":   0,
            "reactor_dates":   [],
        })

        prev_count  = reactor_spec_data["count"]
        total_count = sum(d["count"] for d in rc.values())

        if total_count >= 50:
            cap = math.floor(0.25 * total_count)
            if prev_count > cap:
                print("Too many reacts counted!")
                return

        now = datetime.utcnow()
        raw_dates = reactor_data.get("reactor_dates", [])
        parsed = []
        for ts in raw_dates:
            if isinstance(ts, str):
                try:
                    parsed.append(datetime.fromisoformat(ts))
                except ValueError:
                    continue
            elif isinstance(ts, datetime):
                parsed.append(ts)
        parsed.sort(reverse=True)

        # 3rd most recent must be ≥1 min ago
        if len(parsed) >= 3 and (now - parsed[2]) < timedelta(seconds=30):
            print("Too many reacts counted too quickly (3-reaction/minute limit)")
            return

        # 5th most recent must be ≥10 min ago
        if len(parsed) >= 20 and (now - parsed[4]) < timedelta(minutes=5):
            print("Too many reacts counted too quickly (5-reaction/10 min limit)")
            return

        # 10th most recent must be ≥1 h ago
        if len(parsed) >= 60 and (now - parsed[9]) < timedelta(minutes=30):
            print("Too many reacts counted too quickly (10-reaction/hour limit)")
            return

        parsed.insert(0, now)
        parsed = parsed[:10]
        reactor_data["reactor_dates"] = [dt.isoformat() for dt in parsed]

        reactor_spec_data["count"] += 1
        entry["total_reacts"] = total_count + 1

    print("delta:", delta)
    if delta == 0:
        return

    multiplier = 1
    if reactor_id == TARGET_USER or reactor_id == ORIG_CHANNEL_ID:
        entry["additional_neri"] = entry["additional_neri"] + delta
        multiplier = 15
    elif author_id == TARGET_USER:
        entry["additional_self"] = entry["additional_self"] + delta
    else:
        reactor_data = entry["reactor_counts"].setdefault(
            reactor_id, {"count": 0, "value": 0}
        )
        reactor_data["value"] += delta
        
    update_coins(author_id, multiplier * delta)
    MyBotState.save_social_rating()

    print(
        f"[Reactions] msg#{msg_id} for user {author_id} by user {reactor_id}: "
        f"+{len(added)} added, -{len(removed)} removed → delta={delta}"
    )

def extract_emojis(lst):
    out = []
    for r in lst:
        if isinstance(r, ReactionEmoji):
            out.append(r.emoticon)
        elif isinstance(r, ReactionCustomEmoji):
            out.append(f"<custom:{r.document_id}>")
    return out

def get_emoji_weight(e: str) -> int:
    """
    Look up e in emoji_weights, trying to normalize both
    with and without the VARIATION SELECTOR-16 (U+FE0F).
    """
    # First try exactly as‐is
    if e in MyBotState.emoji_weights:
        return MyBotState.emoji_weights[e]

    # Normalize to NFC (just in case)
    e_nfc = unicodedata.normalize("NFC", e)
    if e_nfc != e and e_nfc in MyBotState.emoji_weights:
        return MyBotState.emoji_weights[e_nfc]

    # Try adding VS16 if it’s missing
    VS16 = "\uFE0F"
    if not e_nfc.endswith(VS16):
        cand = e_nfc + VS16
        if cand in MyBotState.emoji_weights:
            return MyBotState.emoji_weights[cand]
    else:
        # Or stripping it
        cand = e_nfc.rstrip(VS16)
        if cand in MyBotState.emoji_weights:
            return MyBotState.emoji_weights[cand]

    return 0
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
        return

    entry = MyBotState.social_rating.setdefault(author_id, {
        "reactor_counts": {},
        "total_reacts":    0,
        "additional_chat": 0,
        "additional_neri": 0,
        "additional_self": 0,
        "boosts":          0,
        "manual_rating":   0,
    })
    
    if reactor_id != TARGET_USER and reactor_id != ORIG_CHANNEL_ID and author_id != TARGET_USER:
        rc = entry["reactor_counts"]
        prev = rc.get(reactor_id, 0)

        total = sum(rc.values())

        if total >= 50:
            cap = math.floor(0.1 * total)
            excess = 0
            if prev > cap:
                print("Too many reacts counted!")
                return

        rc[reactor_id] = prev + 1
        entry["total_reacts"] = total + 1
    
    print("delta: ", delta)
    if delta == 0:
        return  # nothing to change

    receiver = author_id
    entry_name = "additional_chat"
    multiplier = 1
    if reactor_id == TARGET_USER or reactor_id == ORIG_CHANNEL_ID:
        entry_name = "additional_neri"
        multiplier = 15
    elif author_id == TARGET_USER:
        entry_name = "additional_self"
        
    entry[entry_name] = entry[entry_name] + delta
    update_coins(author_id, multiplier * delta)
    MyBotState.save_social_rating()

    print(
        f"[Reactions] msg#{msg_id} for user {author_id} by user {reactor_id}: "
        f"+{len(added)} added, -{len(removed)} removed → delta={delta}, "
        f"new score={entry[entry_name]}"
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
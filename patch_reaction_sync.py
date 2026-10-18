from pathlib import Path
from datetime import datetime
import shutil

path = Path("app.py")
source = path.read_text(encoding="utf-8")

history_anchor = '''    return render_template(
        "conversation.html", person=person, history=history
    )'''

history_replacement = '''    reaction_map = {}
    history_ids = [item.id for item in history]

    if history_ids:
        saved_reactions = MessageReaction.query.filter(
            MessageReaction.message_id.in_(history_ids)
        ).all()

        for reaction in saved_reactions:
            by_emoji = reaction_map.setdefault(reaction.message_id, {})
            summary = by_emoji.setdefault(
                reaction.emoji,
                {
                    "emoji": reaction.emoji,
                    "count": 0,
                    "reacted_by_me": False
                }
            )
            summary["count"] += 1
            if reaction.user_id == current_user.id:
                summary["reacted_by_me"] = True

    reaction_map = {
        message_id: list(by_emoji.values())
        for message_id, by_emoji in reaction_map.items()
    }

    return render_template(
        "conversation.html",
        person=person,
        history=history,
        reaction_map=reaction_map,
        reaction_emojis=["❤️", "😂", "👍", "😮", "😢", "🙏"]
    )'''

poll_anchor = '''    outgoing = Message.query.filter_by(
        sender_id=current_user.id,
        recipient_id=person.id
    ).order_by(Message.id.desc()).limit(100).all()

    def message_data(item):'''

poll_replacement = '''    outgoing = Message.query.filter_by(
        sender_id=current_user.id,
        recipient_id=person.id
    ).order_by(Message.id.desc()).limit(100).all()

    recent_conversation_messages = Message.query.filter(
        conversation_filter
    ).order_by(Message.id.desc()).limit(100).all()

    recent_message_ids = [
        item.id for item in recent_conversation_messages
    ]
    grouped_reactions = {
        message_id: {} for message_id in recent_message_ids
    }

    if recent_message_ids:
        recent_reactions = MessageReaction.query.filter(
            MessageReaction.message_id.in_(recent_message_ids)
        ).all()

        for reaction in recent_reactions:
            by_emoji = grouped_reactions[reaction.message_id]
            summary = by_emoji.setdefault(
                reaction.emoji,
                {
                    "emoji": reaction.emoji,
                    "count": 0,
                    "reacted_by_me": False
                }
            )
            summary["count"] += 1
            if reaction.user_id == current_user.id:
                summary["reacted_by_me"] = True

    reaction_updates = [
        {
            "message_id": message_id,
            "reactions": list(grouped_reactions[message_id].values())
        }
        for message_id in recent_message_ids
    ]

    def message_data(item):'''

json_anchor = '''        "messages": [message_data(item) for item in new_messages],
        "receipts": [message_data(item) for item in outgoing],
        "typing": is_typing'''

json_replacement = '''        "messages": [message_data(item) for item in new_messages],
        "receipts": [message_data(item) for item in outgoing],
        "reaction_updates": reaction_updates,
        "typing": is_typing'''

replacements = [
    (history_anchor, history_replacement, "conversation history"),
    (poll_anchor, poll_replacement, "live reaction summaries"),
    (json_anchor, json_replacement, "live-update response"),
]

for old, new, label in replacements:
    count = source.count(old)
    if count != 1:
        raise SystemExit(
            f"STOP: expected exactly one {label} insertion point; found {count}. "
            "No changes were written."
        )

backup = Path(
    "app.py.before_reaction_sync_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
)
if backup.exists():
    raise SystemExit(f"STOP: backup already exists: {backup}")

updated = source
for old, new, _ in replacements:
    updated = updated.replace(old, new, 1)

shutil.copy2(path, backup)
path.write_text(updated, encoding="utf-8", newline="")

if path.read_text(encoding="utf-8") != updated:
    raise SystemExit(
        f"Verification failed. Original backup is available at {backup}"
    )

print("Backend patch applied and verified.")
print(f"Backup: {backup}")
print("Changed only: app.py")

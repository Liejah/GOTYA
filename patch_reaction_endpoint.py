from pathlib import Path

path = Path("app.py")
source = path.read_text(encoding="utf-8")

anchor = '@app.route("/presence/heartbeat", methods=["POST"])'

if "def message_reaction_api(" in source:
    print("STOP: Reaction endpoint already exists. No changes made.")
    raise SystemExit(1)

if source.count(anchor) != 1:
    print("STOP: Expected insertion point was not found exactly once.")
    raise SystemExit(1)

if "class MessageReaction(db.Model):" not in source:
    print("STOP: MessageReaction model is missing. No changes made.")
    raise SystemExit(1)

endpoint = '''@app.route("/messages/<int:message_id>/reaction", methods=["POST"])
@login_required
def message_reaction_api(message_id):
    message = db.session.get(Message, message_id)

    if message is None:
        return jsonify({"error": "Message not found."}), 404

    participant = current_user.id in (
        message.sender_id, message.recipient_id
    )
    if (
        not participant
        or message.sender_id == message.recipient_id
        or not _chat_friendship_exists(
            message.sender_id, message.recipient_id
        )
    ):
        return jsonify({"error": "Conversation unavailable."}), 403

    payload = request.get_json(silent=True) or {}
    emoji = payload.get("emoji")
    allowed_emojis = {"❤️", "😂", "👍", "😮", "😢", "🙏"}

    if emoji is not None and emoji not in allowed_emojis:
        return jsonify({"error": "Unsupported reaction."}), 400

    reaction = MessageReaction.query.filter_by(
        message_id=message.id,
        user_id=current_user.id
    ).first()

    if emoji is None:
        if reaction is not None:
            db.session.delete(reaction)
    elif reaction is not None:
        reaction.emoji = emoji
    else:
        db.session.add(MessageReaction(
            message_id=message.id,
            user_id=current_user.id,
            emoji=emoji
        ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Could not save reaction. Please retry."}), 409

    reactions = MessageReaction.query.filter_by(
        message_id=message.id
    ).all()

    grouped = {}
    for item in reactions:
        if item.emoji not in grouped:
            grouped[item.emoji] = {
                "emoji": item.emoji,
                "count": 0,
                "reacted_by_me": False
            }
        grouped[item.emoji]["count"] += 1
        if item.user_id == current_user.id:
            grouped[item.emoji]["reacted_by_me"] = True

    return jsonify({
        "ok": True,
        "message_id": message.id,
        "reactions": list(grouped.values())
    })


'''

backup = Path("app.py.before_reaction_endpoint")
if backup.exists():
    print(f"STOP: {backup} already exists. No changes made.")
    raise SystemExit(1)

backup.write_bytes(path.read_bytes())
path.write_text(source.replace(anchor, endpoint + anchor, 1), encoding="utf-8", newline="")
print("PASS: Added reaction endpoint.")
print(f"Safety copy: {backup}")

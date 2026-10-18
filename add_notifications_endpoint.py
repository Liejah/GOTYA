from pathlib import Path

path = Path("app.py")
source = path.read_text(encoding="utf-8")

marker = "# GOTYA VIDEO CALL API"
route_marker = '@app.route("/notifications/updates", methods=["GET"])'

if route_marker in source:
    raise SystemExit("STOP: Notifications endpoint already exists. No changes made.")

if source.count(marker) != 1:
    raise SystemExit("STOP: Expected one video-call section marker. No changes made.")

addition = '''@app.route("/notifications/updates", methods=["GET"])
@login_required
def notifications_updates():
    """Read-only summary of notifications for the signed-in user."""

    unread_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.read_at.is_(None)
    ).count()

    unread_messages = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.read_at.is_(None)
    ).order_by(Message.created_at.desc()).limit(20).all()

    message_items = [
        {
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_name": message.sender.username,
            "preview": (
                message.content[:160]
                if message.content else "Sent an attachment"
            ),
            "created_at": message.created_at.isoformat() + "Z",
        }
        for message in unread_messages
    ]

    pending_requests = Friendship.query.filter_by(
        recipient_id=current_user.id,
        status="pending"
    ).order_by(Friendship.created_at.desc()).limit(20).all()

    friend_request_items = [
        {
            "id": friendship.id,
            "sender_id": friendship.requester_id,
            "sender_name": friendship.requester.username,
            "created_at": friendship.created_at.isoformat() + "Z",
        }
        for friendship in pending_requests
    ]

    call = VideoCall.query.filter_by(
        recipient_id=current_user.id,
        status="ringing"
    ).order_by(VideoCall.created_at.desc()).first()

    incoming_call = None
    if call is not None and video_call_access(call):
        incoming_call = {
            "id": call.id,
            "caller_id": call.caller_id,
            "caller_name": call.caller.username,
            "created_at": call.created_at.isoformat() + "Z",
        }

    return jsonify({
        "unread_count": unread_count,
        "unread_messages": message_items,
        "pending_friend_requests": friend_request_items,
        "incoming_call": incoming_call,
    })


'''

updated = source.replace(marker, addition + marker, 1)
path.write_text(updated, encoding="utf-8", newline="")
print("Added notifications endpoint to app.py.")

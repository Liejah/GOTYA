from pathlib import Path

path = Path("app.py")
with path.open("r", encoding="utf-8", newline="") as f:
    source = f.read()

nl = "\r\n" if "\r\n" in source else "\n"

def patch(old, new, label):
    global source
    old = old.replace("\n", nl)
    new = new.replace("\n", nl)
    count = source.count(old)
    if count != 1:
        raise SystemExit(
            f"STOP: {label}: expected 1 matching block, found {count}. "
            "No changes were written."
        )
    source = source.replace(old, new, 1)
    print(f"Verified patch target: {label}")

patch(
'''    delivered_at = db.Column(db.DateTime, nullable=True)
    attachment_filename = db.Column(db.String(255), nullable=True)''',
'''    delivered_at = db.Column(db.DateTime, nullable=True)
    reply_to_id = db.Column(
        db.Integer, db.ForeignKey("message.id"), nullable=True, index=True
    )
    attachment_filename = db.Column(db.String(255), nullable=True)''',
"message reply column"
)

patch(
'''    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])''',
'''    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
    reply_to = db.relationship(
        "Message", remote_side=[id], foreign_keys=[reply_to_id], uselist=False
    )''',
"message reply relationship"
)

patch(
'''        content = request.form.get("content", "").strip()
        photo_uploads = [''',
'''        content = request.form.get("content", "").strip()
        reply_to_raw = request.form.get("reply_to_id", "").strip()
        reply_to_message = None

        if reply_to_raw:
            try:
                reply_to_id = int(reply_to_raw)
                if reply_to_id > 0:
                    reply_to_message = Message.query.filter(
                        Message.id == reply_to_id,
                        db.or_(
                            db.and_(
                                Message.sender_id == current_user.id,
                                Message.recipient_id == person.id
                            ),
                            db.and_(
                                Message.sender_id == person.id,
                                Message.recipient_id == current_user.id
                            )
                        )
                    ).first()
            except (TypeError, ValueError):
                reply_to_message = None

        photo_uploads = [''',
"reply target validation"
)

patch(
'''        if len(photo_uploads) > 30:
            flash("You can send up to 30 pictures at once.", "error")''',
'''        if reply_to_raw and reply_to_message is None:
            flash("That reply target is unavailable. Please choose a message in this conversation.", "error")
        elif len(photo_uploads) > 30:
            flash("You can send up to 30 pictures at once.", "error")''',
"reply target validation branch"
)

patch(
'''                        db.session.add(Message(
                            sender_id=current_user.id,
                            recipient_id=person.id,
                            content=content
                        ))''',
'''                        db.session.add(Message(
                            sender_id=current_user.id,
                            recipient_id=person.id,
                            content=content,
                            reply_to_id=reply_to_message.id if reply_to_message else None
                        ))''',
"text message reply saving"
)

patch(
'''                                attachment_size=file_size
                            ))''',
'''                                attachment_size=file_size,
                                reply_to_id=(
                                    reply_to_message.id
                                    if index == 0 and reply_to_message else None
                                )
                            ))''',
"first attachment reply saving"
)

patch(
'''    app.run(host="0.0.0.0", port=5000, debug=False)''',
'''        # Add reply support without replacing existing message records.
        message_columns = {
            column["name"]
            for column in db.inspect(db.engine).get_columns("message")
        }
        if "reply_to_id" not in message_columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text("ALTER TABLE message ADD COLUMN reply_to_id INTEGER")
                )
            print("Added reply_to_id without resetting existing messages.")
        else:
            print("Message reply column already exists.")

    app.run(host="0.0.0.0", port=5000, debug=False)''',
"safe reply column migration"
)

with path.open("w", encoding="utf-8", newline="") as f:
    f.write(source)

print("Backend patch applied. No database changes have been made yet.")

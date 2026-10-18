from pathlib import Path

path = Path("app.py")
source = path.read_text(encoding="utf-8")

anchor = "class PushSubscription(db.Model):"

if "class MessageReaction(db.Model):" in source:
    print("STOP: MessageReaction already exists. No changes made.")
    raise SystemExit(1)

if source.count(anchor) != 1:
    print("STOP: Expected insertion point was not found exactly once.")
    raise SystemExit(1)

model = '''class MessageReaction(db.Model):
    __tablename__ = "message_reaction"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("message.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    emoji = db.Column(db.String(16), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    message = db.relationship("Message", foreign_keys=[message_id])
    user = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.UniqueConstraint(
            "message_id", "user_id",
            name="uq_message_reaction_user"
        ),
    )


'''

updated = source.replace(anchor, model + anchor, 1)

# Create a safety copy without overwriting an existing backup.
backup = Path("app.py.before_reaction_model")
if backup.exists():
    print(f"STOP: {backup} already exists. No changes made.")
    raise SystemExit(1)

backup.write_bytes(path.read_bytes())
path.write_text(updated, encoding="utf-8", newline="")
print("PASS: Added MessageReaction model.")
print(f"Safety copy: {backup}")

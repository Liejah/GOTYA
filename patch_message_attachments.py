from pathlib import Path
from datetime import datetime
import shutil

app_path = Path("app.py")
template_path = Path("templates/conversation.html")
app_text = app_path.read_text(encoding="utf-8")
template_text = template_path.read_text(encoding="utf-8")

old_route = '''    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content:
            flash("Write a message before sending.", "error")
        elif len(content) > 2000:
            flash("Messages cannot exceed 2000 characters.", "error")
        else:
            message = Message(
                sender_id=current_user.id,
                recipient_id=person.id,
                content=content
            )
            db.session.add(message)
            db.session.commit()
            return redirect(
                url_for("conversation", user_id=person.id) + "#latest"
            )
'''

new_route = '''    if request.method == "POST":
        content = request.form.get("content", "").strip()
        upload = request.files.get("attachment")
        has_upload = bool(upload and upload.filename)

        allowed_extensions = {
            "jpg", "jpeg", "png", "gif", "webp",
            "pdf", "txt", "csv", "doc", "docx",
            "xls", "xlsx", "ppt", "pptx", "zip"
        }

        if not content and not has_upload:
            flash("Write a message or choose a file.", "error")
        elif len(content) > 2000:
            flash("Messages cannot exceed 2000 characters.", "error")
        elif has_upload and (
            "." not in upload.filename
            or upload.filename.rsplit(".", 1)[1].lower() not in allowed_extensions
        ):
            flash("That file type is not allowed.", "error")
        else:
            saved_path = None
            attachment_filename = None
            original_name = None
            mimetype = None
            file_size = None

            if has_upload:
                original_name = secure_filename(upload.filename)
                if not original_name:
                    flash("Please choose a valid filename.", "error")
                    return redirect(url_for("conversation", user_id=person.id))

                upload.stream.seek(0, os.SEEK_END)
                file_size = upload.stream.tell()
                upload.stream.seek(0)

                if file_size > 15 * 1024 * 1024:
                    flash("Files must be 15 MB or smaller.", "error")
                    return redirect(url_for("conversation", user_id=person.id))

                extension = original_name.rsplit(".", 1)[1].lower()
                attachment_filename = uuid.uuid4().hex + "." + extension
                attachment_dir = BASE_DIR / "uploads" / "message_attachments"
                attachment_dir.mkdir(parents=True, exist_ok=True)
                saved_path = attachment_dir / attachment_filename
                mimetype = upload.mimetype or "application/octet-stream"
                upload.save(saved_path)

            try:
                message = Message(
                    sender_id=current_user.id,
                    recipient_id=person.id,
                    content=content,
                    attachment_filename=attachment_filename,
                    attachment_original_name=original_name,
                    attachment_mimetype=mimetype,
                    attachment_size=file_size
                )
                db.session.add(message)
                db.session.commit()
            except Exception:
                db.session.rollback()
                if saved_path and saved_path.exists():
                    saved_path.unlink()
                raise

            return redirect(
                url_for("conversation", user_id=person.id) + "#latest"
            )
'''

old_bubble = '<div class="bubble">{{ item.content }}</div>'
new_bubble = '''<div class="bubble">
{% if item.content %}<div class="message-text">{{ item.content }}</div>{% endif %}
{% if item.attachment_filename %}
{% set attachment_url = url_for('message_attachment_file', message_id=item.id) %}
{% if item.attachment_mimetype and item.attachment_mimetype.startswith('image/') %}
<a href="{{ attachment_url }}" target="_blank" rel="noopener">
<img class="message-image" src="{{ attachment_url }}" alt="{{ item.attachment_original_name or 'Shared image' }}" loading="lazy">
</a>
{% endif %}
<div class="attachment-link">
<a href="{{ attachment_url }}">{{ item.attachment_original_name or 'Download attachment' }}</a>
{% if item.attachment_size %}<small>{{ (item.attachment_size / 1024)|round|int }} KB</small>{% endif %}
</div>
{% endif %}
</div>'''

old_form = '''<form class="composer" method="post" action="{{ url_for('conversation', user_id=person.id) }}">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<textarea id="messageInput" name="content" maxlength="2000" rows="1" placeholder="Message {{ person.username }}..." aria-label="Write a message" required></textarea>
<button class="send" type="submit">Send ?</button>
</form>'''

new_form = '''<form class="composer" method="post" enctype="multipart/form-data" action="{{ url_for('conversation', user_id=person.id) }}">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<textarea id="messageInput" name="content" maxlength="2000" rows="1" placeholder="Message {{ person.username }}..." aria-label="Write a message"></textarea>
<div class="attachment-picker">
<label for="attachmentInput" title="Attach an image or file">＋ File</label>
<input id="attachmentInput" type="file" name="attachment" accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.txt,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip">
<span id="attachmentName" aria-live="polite"></span>
</div>
<button class="send" type="submit">Send ?</button>
</form>'''

css_anchor = '.send{height:44px;border-radius:22px;padding:0 20px}'
css_addition = '''
.attachment-picker{display:flex;flex-direction:column;gap:4px;max-width:145px;font-size:12px;color:var(--muted)}
.attachment-picker label{display:inline-block;cursor:pointer;font-weight:700;color:var(--blue);padding:8px 4px}
.attachment-picker input{width:145px;max-width:100%;font-size:11px}
#attachmentName{overflow-wrap:anywhere;max-width:145px}
.message-image{display:block;max-width:min(260px,100%);max-height:300px;object-fit:contain;border-radius:10px;margin-top:8px}
.attachment-link{margin-top:8px;overflow-wrap:anywhere}
.attachment-link a{color:var(--blue);font-weight:600}
.attachment-link small{display:block;opacity:.75}
'''

checks = [
    (app_text.count(old_route) == 1, "conversation POST block"),
    ('def message_attachment_file(' not in app_text, "new attachment route is not already present"),
    (template_text.count(old_bubble) == 1, "message bubble"),
    (template_text.count(old_form) == 1, "composer form"),
    (template_text.count(css_anchor) == 1, "composer CSS"),
]
failed = [name for ok, name in checks if not ok]
if failed:
    raise SystemExit("STOP: Expected code did not match: " + ", ".join(failed) + ". No files changed.")

route = '''@app.route("/message-attachments/<int:message_id>")
@login_required
def message_attachment_file(message_id):
    message = db.session.get(Message, message_id)
    if message is None or not message.attachment_filename:
        flash("Attachment not found.", "error")
        return redirect(url_for("messages"))

    if current_user.id not in (message.sender_id, message.recipient_id):
        flash("You cannot access that attachment.", "error")
        return redirect(url_for("messages"))

    attachment_dir = BASE_DIR / "uploads" / "message_attachments"
    is_image = (message.attachment_mimetype or "").startswith("image/")
    return send_from_directory(
        attachment_dir,
        message.attachment_filename,
        as_attachment=not is_image,
        download_name=message.attachment_original_name or "attachment"
    )


'''

updated_app = app_text.replace(old_route, new_route, 1)
logout_anchor = '@app.route("/logout", methods=["POST"])'
if updated_app.count(logout_anchor) != 1:
    raise SystemExit("STOP: Logout route anchor mismatch. No files changed.")
updated_app = updated_app.replace(logout_anchor, route + logout_anchor, 1)

updated_template = template_text.replace(old_bubble, new_bubble, 1)
updated_template = updated_template.replace(old_form, new_form, 1)
updated_template = updated_template.replace(css_anchor, css_anchor + css_addition, 1)

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
app_backup = Path(f"app_before_attachment_upload_{stamp}.py")
template_backup = template_path.with_name(f"conversation_before_attachment_upload_{stamp}.html")

shutil.copy2(app_path, app_backup)
shutil.copy2(template_path, template_backup)

app_path.write_text(updated_app, encoding="utf-8", newline="\n")
template_path.write_text(updated_template, encoding="utf-8", newline="\n")

print("Attachment upload changes applied.")
print("App backup:", app_backup.name)
print("Template backup:", template_backup.name)

from pathlib import Path
from datetime import datetime
import shutil
import re

root = Path.cwd()
app_path = root / "app.py"
template_path = root / "templates" / "conversation.html"

app_text = app_path.read_text(encoding="utf-8-sig")
html = template_path.read_text(encoding="utf-8-sig")

route_start = app_text.find('@app.route("/messages/<int:user_id>"')
if route_start < 0:
    raise SystemExit("Conversation route not found. No changes made.")

post_start = app_text.find("    if request.method == \"POST\":", route_start)
unread_start = app_text.find("    unread = Message.query.filter_by(", post_start)
if post_start < 0 or unread_start < 0:
    raise SystemExit("Could not locate the message POST block. No changes made.")

if 'name="photos"' in html or 'id="photoInput"' in html:
    raise SystemExit("Photo picker changes appear to exist already. Stopping to avoid duplicate edits.")

composer_start = html.find('<form class="composer"')
composer_end = html.find('</form>', composer_start)
if composer_start < 0 or composer_end < 0:
    raise SystemExit("Composer form not found. No changes made.")
composer_end += len("</form>")

if 'id="messageInput"' not in html or 'name="attachment"' not in html:
    raise SystemExit("Expected existing composer controls not found. No changes made.")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
app_backup = root / f"app_before_gallery_composer_{stamp}.py"
html_backup = root / "templates" / f"conversation_before_gallery_composer_{stamp}.html"
shutil.copy2(app_path, app_backup)
shutil.copy2(template_path, html_backup)

new_post = '''    if request.method == "POST":
        content = request.form.get("content", "").strip()
        photo_uploads = [
            item for item in request.files.getlist("photos")
            if item and item.filename
        ]
        single_upload = request.files.get("attachment")
        has_single_upload = bool(single_upload and single_upload.filename)

        allowed_extensions = {
            "jpg", "jpeg", "png", "gif", "webp",
            "pdf", "txt", "csv", "doc", "docx",
            "xls", "xlsx", "ppt", "pptx", "zip"
        }
        image_extensions = {"jpg", "jpeg", "png", "gif", "webp"}

        uploads = photo_uploads[:]
        if has_single_upload:
            uploads.append(single_upload)

        if len(photo_uploads) > 30:
            flash("You can send up to 30 pictures at once.", "error")
        elif photo_uploads and has_single_upload:
            flash("Send pictures or a document in separate messages.", "error")
        elif not content and not uploads:
            flash("Write a message or choose a picture or file.", "error")
        elif len(content) > 2000:
            flash("Messages cannot exceed 2000 characters.", "error")
        else:
            prepared = []
            error = None

            for upload in uploads:
                original_name = secure_filename(upload.filename or "")
                if not original_name or "." not in original_name:
                    error = "Please choose a valid filename."
                    break

                extension = original_name.rsplit(".", 1)[1].lower()
                allowed = image_extensions if upload in photo_uploads else allowed_extensions
                if extension not in allowed:
                    error = "That file type is not allowed."
                    break

                upload.stream.seek(0, os.SEEK_END)
                file_size = upload.stream.tell()
                upload.stream.seek(0)
                if file_size > 15 * 1024 * 1024:
                    error = "Each file must be 15 MB or smaller."
                    break

                prepared.append((upload, original_name, extension, file_size))

            if error:
                flash(error, "error")
            else:
                attachment_dir = BASE_DIR / "uploads" / "message_attachments"
                attachment_dir.mkdir(parents=True, exist_ok=True)
                saved_paths = []

                try:
                    if not prepared:
                        db.session.add(Message(
                            sender_id=current_user.id,
                            recipient_id=person.id,
                            content=content
                        ))
                    else:
                        for index, (upload, original_name, extension, file_size) in enumerate(prepared):
                            stored_name = uuid.uuid4().hex + "." + extension
                            saved_path = attachment_dir / stored_name
                            upload.save(saved_path)
                            saved_paths.append(saved_path)

                            db.session.add(Message(
                                sender_id=current_user.id,
                                recipient_id=person.id,
                                content=content if index == 0 else "",
                                attachment_filename=stored_name,
                                attachment_original_name=original_name,
                                attachment_mimetype=(
                                    "image/" + ("jpeg" if extension in {"jpg", "jpeg"} else extension)
                                    if extension in image_extensions
                                    else (upload.mimetype or "application/octet-stream")
                                ),
                                attachment_size=file_size
                            ))

                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    for saved_path in saved_paths:
                        if saved_path.exists():
                            saved_path.unlink()
                    raise

                return redirect(
                    url_for("conversation", user_id=person.id) + "#latest"
                )

'''

new_composer = '''<form class="composer" method="post" enctype="multipart/form-data" action="{{ url_for('conversation', user_id=person.id) }}">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<label class="composer-icon" for="photoInput" title="Choose up to 30 photos" aria-label="Choose photos">
<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><circle cx="8.5" cy="9" r="1.5"></circle><path d="m21 15-5-5L5 20"></path></svg>
</label>
<input class="visually-hidden-file" id="photoInput" type="file" name="photos" accept="image/jpeg,image/png,image/gif,image/webp" multiple>
<label class="composer-icon file-icon" for="attachmentInput" title="Attach a document or file" aria-label="Attach a file">
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5a6 6 0 0 1-6-6V7a4 4 0 0 1 8 0v7.5a2 2 0 0 1-4 0V8"></path></svg>
</label>
<input class="visually-hidden-file" id="attachmentInput" type="file" name="attachment" accept=".pdf,.txt,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip">
<div class="composer-input-wrap">
<textarea id="messageInput" name="content" maxlength="2000" rows="1" placeholder="Aa" aria-label="Write a message"></textarea>
<div id="attachmentName" class="selected-files" aria-live="polite"></div>
</div>
<button class="send" type="submit" aria-label="Send message" title="Send message">
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4Z"></path><path d="M22 2 11 13"></path></svg>
</button>
</form>'''

app_text = app_text[:post_start] + new_post + app_text[unread_start:]
html = html[:composer_start] + new_composer + html[composer_end:]

# Replace visible question-mark placeholders with safe HTML entities.
html = html.replace('aria-label="Back to messages">?</a>', 'aria-label="Back to messages">&#8592;</a>')
html = html.replace("strftime('%b %d ? %I:%M %p')", "strftime('%b %d') + ' &#183; ' + item.created_at.strftime('%I:%M %p')")
html = html.replace("{{ person.username }} ??</strong>", "{{ person.username }} &#9825;</strong>")
html = html.replace("ï¼‹ File", "File")

marker = ".flash{padding:11px 14px;"
if marker not in html:
    raise SystemExit("CSS insertion point not found. Backups were created; source files not yet written.")

composer_css = r'''
/* Messenger-inspired composer, including Android sizing */
.composer{display:flex;align-items:flex-end;gap:8px;padding:9px 10px;box-sizing:border-box}
.composer-icon{width:36px;height:40px;flex:0 0 36px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--blue);border-radius:50%;-webkit-tap-highlight-color:transparent}
.composer-icon:active{background:#eaf2ff}
.composer-icon svg,.send svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.visually-hidden-file{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
.composer-input-wrap{flex:1;min-width:0;background:#f0f2f5;border-radius:22px;padding:3px 11px;display:flex;flex-direction:column;justify-content:center}
.composer textarea{display:block;width:100%;min-width:0;min-height:36px;max-height:120px;box-sizing:border-box;resize:none;border:0!important;box-shadow:none!important;background:transparent;padding:8px 2px;font:inherit;font-size:16px;line-height:20px;outline:none;overflow-y:auto}
.composer textarea::placeholder{color:#68778b}
.selected-files{font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.send{width:40px;height:40px;flex:0 0 40px;padding:0;border-radius:50%;display:flex;align-items:center;justify-content:center}
.send svg{width:22px;height:22px;fill:currentColor;stroke:currentColor}
@media(max-width:600px){
 .composer{gap:4px;padding:8px 6px;padding-bottom:max(8px,env(safe-area-inset-bottom))}
 .composer-icon{width:32px;flex-basis:32px;height:40px}
 .composer-icon svg{width:23px;height:23px}
 .composer-input-wrap{padding:2px 10px}
 .composer textarea{font-size:16px;min-height:36px;max-height:110px;padding:8px 1px}
 .send{width:38px;height:38px;flex-basis:38px}
}
'''
html = html.replace(marker, composer_css + "\n" + marker)

# Add picker feedback and ensure the Android textarea grows/shrinks naturally.
script_marker = 'input.addEventListener("input", () => {'
if script_marker not in html:
    raise SystemExit("Textarea script not found. Backups were created; source files not yet written.")

html = html.replace(
    script_marker,
    '''const photoInput = document.getElementById("photoInput");
const fileInput = document.getElementById("attachmentInput");
const attachmentName = document.getElementById("attachmentName");

function showSelection() {
  if (photoInput.files.length) {
    attachmentName.textContent = photoInput.files.length + (photoInput.files.length === 1 ? " photo selected" : " photos selected (max 30)");
  } else if (fileInput.files.length) {
    attachmentName.textContent = fileInput.files[0].name;
  } else {
    attachmentName.textContent = "";
  }
}
photoInput.addEventListener("change", () => {
  if (photoInput.files.length > 30) {
    alert("Please select up to 30 photos at a time.");
    photoInput.value = "";
  }
  fileInput.value = "";
  showSelection();
});
fileInput.addEventListener("change", () => {
  photoInput.value = "";
  showSelection();
});

input.addEventListener("input", () => {'''
)

app_path.write_text(app_text, encoding="utf-8")
template_path.write_text(html, encoding="utf-8")

print("Gallery composer and multi-photo upload changes applied.")
print("App backup:", app_backup.name)
print("Template backup:", html_backup.name)

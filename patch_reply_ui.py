from pathlib import Path

app_path = Path("app.py")
template_path = Path("templates/conversation.html")

app = app_path.read_text(encoding="utf-8")
html = template_path.read_text(encoding="utf-8")

app_new = app
html_new = html
checks = []

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly 1 match, found {count}. No files written."
        )
    checks.append(label)
    return text.replace(old, new, 1)

# --- Backend: include reply information in live-update JSON ---
app_new = replace_once(
    app_new,
    '    def message_data(item):\n        return {\n',
    '''    def message_data(item):
        reply_data = None
        replied_to = item.reply_to
        if replied_to:
            reply_preview = (
                (replied_to.content or "").strip()
                or replied_to.attachment_original_name
                or (
                    "Photo"
                    if replied_to.attachment_mimetype
                    and replied_to.attachment_mimetype.startswith("image/")
                    else "Attachment"
                )
            )
            if len(reply_preview) > 140:
                reply_preview = reply_preview[:137] + "..."
            reply_data = {
                "id": replied_to.id,
                "sender_name": (
                    replied_to.sender.username if replied_to.sender else "Message"
                ),
                "preview": reply_preview,
            }

        return {
''',
    "live reply JSON helper",
)

app_new = replace_once(
    app_new,
    '            "sender_id": item.sender_id,\n            "content": item.content or "",\n',
    '            "sender_id": item.sender_id,\n'
    '            "sender_name": item.sender.username if item.sender else "Message",\n'
    '            "content": item.content or "",\n'
    '            "reply_to_id": item.reply_to_id,\n'
    '            "reply_to": reply_data,\n',
    "live reply JSON fields",
)

# --- Template: reply preview and actions for existing messages ---
html_new = replace_once(
    html_new,
    'data-created-at="{{ item.created_at.isoformat() }}">',
    'data-created-at="{{ item.created_at.isoformat() }}" '
    'data-message-preview="{{ (item.content or item.attachment_original_name or '
    '(\'Photo\' if item.attachment_mimetype and '
    'item.attachment_mimetype.startswith(\'image/\') else \'Attachment\'))[:140] }}" '
    'data-message-sender="{{ (item.sender.username if item.sender else \'Message\') }}">',
    "history message metadata",
)

bubble_anchor = (
    '<div class="bubble{% if item.content and item.content.startswith(\'Call Ended\') %} '
    'call-summary-bubble{% endif %}{% if item.attachment_filename and '
    'item.attachment_mimetype and item.attachment_mimetype.startswith(\'image/\') '
    'and not item.content %} image-only-bubble{% endif %}">'
)
bubble_replacement = bubble_anchor + '''
{% if item.reply_to %}
<div class="reply-quote" tabindex="0" role="button" data-reply-target="{{ item.reply_to.id }}">
<span class="reply-quote-sender">{{ item.reply_to.sender.username if item.reply_to.sender else 'Message' }}</span>
<span class="reply-quote-text">{{ (item.reply_to.content or item.reply_to.attachment_original_name or ('Photo' if item.reply_to.attachment_mimetype and item.reply_to.attachment_mimetype.startswith('image/') else 'Attachment'))[:140] }}</span>
</div>
{% endif %}'''
html_new = replace_once(
    html_new, bubble_anchor, bubble_replacement, "history reply quote"
)

receipt_anchor = (
    '</div>\n{% if item.sender_id == current_user.id %}\n'
    '<div class="delivery-receipt" data-receipt-id="{{ item.id }}">'
)
receipt_replacement = (
    '</div>\n'
    '<button type="button" class="reply-message-btn" '
    'data-reply-id="{{ item.id }}" '
    'data-reply-sender="{{ (item.sender.username if item.sender else \'Message\') }}" '
    'data-reply-preview="{{ (item.content or item.attachment_original_name or '
    '(\'Photo\' if item.attachment_mimetype and '
    'item.attachment_mimetype.startswith(\'image/\') else \'Attachment\'))[:140] }}">'
    'Reply</button>\n'
    '{% if item.sender_id == current_user.id %}\n'
    '<div class="delivery-receipt" data-receipt-id="{{ item.id }}">'
)
html_new = replace_once(
    html_new, receipt_anchor, receipt_replacement, "history reply button"
)

# --- Template: hidden form field and composer preview ---
html_new = replace_once(
    html_new,
    '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">',
    '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
    '<input type="hidden" id="replyToId" name="reply_to_id" value="">',
    "hidden reply form field",
)

html_new = replace_once(
    html_new,
    '<div class="composer-input-wrap">\n<textarea id="messageInput"',
    '''<div class="composer-input-wrap">
<div id="replyPreview" class="reply-preview" hidden>
  <div class="reply-preview-copy">
    <strong id="replyPreviewSender">Replying to</strong>
    <span id="replyPreviewText"></span>
  </div>
  <button type="button" id="cancelReply" aria-label="Cancel reply">Cancel</button>
</div>
<textarea id="messageInput"''',
    "composer reply preview",
)

# --- Template: styles before the one closing style tag ---
css = '''
/* Message reply UI */
.reply-message-btn {
  display: block;
  margin: 3px 8px 5px;
  padding: 3px 8px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--muted, #68778b);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.reply-message-btn:hover { text-decoration: underline; }
.reply-quote {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 7px;
  padding: 7px 10px;
  border-left: 3px solid var(--rose, #8f5a5a);
  border-radius: 5px;
  background: rgba(127, 127, 127, .09);
  cursor: pointer;
  overflow-wrap: anywhere;
}
.reply-quote-sender { font-size: 12px; font-weight: 700; }
.reply-quote-text { font-size: 12px; opacity: .85; }
.reply-preview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
  padding: 7px 2px;
  border-bottom: 1px solid rgba(127, 127, 127, .2);
}
.reply-preview[hidden] { display: none !important; }
.reply-preview-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.reply-preview-copy span { opacity: .8; }
#cancelReply {
  flex: 0 0 auto;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
'''
if html_new.count("</style>") < 1:
    raise RuntimeError(
        f"Style closing tag: expected exactly 1 match, found {html_new.count('</style>')}. No files written."
    )
html_new = html_new.replace("</style>", css + "\n</style>", 1)
checks.append("reply UI CSS")

# --- Template: safe DOM helpers and reply selection ---
html_new = replace_once(
    html_new,
    '''  function makeElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function addMessage(message) {''',
    '''  function makeElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function addReplyQuote(container, replyTo) {
    if (!replyTo) return;
    const quote = makeElement("div", "reply-quote");
    quote.tabIndex = 0;
    quote.setAttribute("role", "button");
    quote.dataset.replyTarget = String(replyTo.id);
    quote.appendChild(makeElement(
      "span", "reply-quote-sender", replyTo.sender_name || "Message"
    ));
    quote.appendChild(makeElement(
      "span", "reply-quote-text", replyTo.preview || "Message"
    ));
    container.appendChild(quote);
  }

  function addReplyButton(row, id, sender, preview) {
    const button = makeElement("button", "reply-message-btn", "Reply");
    button.type = "button";
    button.dataset.replyId = String(id);
    button.dataset.replySender = sender || "Message";
    button.dataset.replyPreview = preview || "Message";
    row.appendChild(button);
  }

  function addMessage(message) {''',
    "safe live reply helpers",
)

html_new = replace_once(
    html_new,
    '''    if (message.content) {
      bubble.appendChild(makeElement("div", "message-text", message.content));
    }''',
    '''    addReplyQuote(bubble, message.reply_to);

    if (message.content) {
      bubble.appendChild(makeElement("div", "message-text", message.content));
    }''',
    "live reply quote",
)

html_new = replace_once(
    html_new,
    '    row.appendChild(bubble);\n    const previousRow = Array.from(list.querySelectorAll(".message-row[data-message-id]")).pop();',
    '''    row.appendChild(bubble);
    const messagePreview = message.content || message.attachment_name ||
      (isImage ? "Photo" : "Attachment");
    addReplyButton(row, message.id, message.sender_name, messagePreview);
    const previousRow = Array.from(list.querySelectorAll(".message-row[data-message-id]")).pop();''',
    "live reply button",
)

html_new = replace_once(
    html_new,
    '''  if (!list || !composer) return;

  let lastId = 0;''',
    '''  if (!list || !composer) return;

  const replyInput = document.getElementById("replyToId");
  const replyPreview = document.getElementById("replyPreview");
  const replyPreviewSender = document.getElementById("replyPreviewSender");
  const replyPreviewText = document.getElementById("replyPreviewText");
  const cancelReply = document.getElementById("cancelReply");

  function clearReply() {
    if (replyInput) replyInput.value = "";
    if (replyPreview) replyPreview.hidden = true;
    if (replyPreviewSender) replyPreviewSender.textContent = "Replying to";
    if (replyPreviewText) replyPreviewText.textContent = "";
  }

  function selectReply(id, sender, preview) {
    if (!replyInput || !replyPreview) return;
    replyInput.value = String(id);
    replyPreviewSender.textContent = "Replying to " + (sender || "Message");
    replyPreviewText.textContent = preview || "Message";
    replyPreview.hidden = false;
    if (messageInput) messageInput.focus();
  }

  list.addEventListener("click", event => {
    const button = event.target.closest(".reply-message-btn");
    if (button) {
      selectReply(
        button.dataset.replyId,
        button.dataset.replySender,
        button.dataset.replyPreview
      );
      return;
    }
    const quote = event.target.closest(".reply-quote");
    if (quote && quote.dataset.replyTarget) {
      const target = list.querySelector(
        '[data-message-id="' + quote.dataset.replyTarget + '"]'
      );
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.classList.add("reply-target-highlight");
        window.setTimeout(() => target.classList.remove("reply-target-highlight"), 1200);
      }
    }
  });

  list.addEventListener("keydown", event => {
    const quote = event.target.closest(".reply-quote");
    if (quote && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      quote.click();
    }
  });

  if (cancelReply) cancelReply.addEventListener("click", clearReply);

  composer.addEventListener("submit", event => {
    if (replyInput && replyInput.value &&
        !list.querySelector('[data-message-id="' + replyInput.value + '"]')) {
      clearReply();
    }
  });

  let lastId = 0;''',
    "reply controls and navigation",
)

# All expected anchors were checked before either file is written.
app_path.write_text(app_new, encoding="utf-8", newline="")
template_path.write_text(html_new, encoding="utf-8", newline="")

print("Reply UI patch applied.")
print("Verified changes:")
for label in checks:
    print(" - " + label)
print("Files changed: app.py, templates/conversation.html")
print("No database rows were changed. Nothing was pushed or deployed.")

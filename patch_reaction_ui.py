from pathlib import Path
from datetime import datetime
import shutil

path = Path("templates/conversation.html")
source = path.read_text(encoding="utf-8")

replacements = [
(
'''<button type="button" class="reply-message-btn" data-reply-id="{{ item.id }}" data-reply-sender="{{ (item.sender.username if item.sender else 'Message') }}" data-reply-preview="{{ (item.content or item.attachment_original_name or ('Photo' if item.attachment_mimetype and item.attachment_mimetype.startswith('image/') else 'Attachment'))[:140] }}">Reply</button>''',
'''<button type="button" class="reply-message-btn" data-reply-id="{{ item.id }}" data-reply-sender="{{ (item.sender.username if item.sender else 'Message') }}" data-reply-preview="{{ (item.content or item.attachment_original_name or ('Photo' if item.attachment_mimetype and item.attachment_mimetype.startswith('image/') else 'Attachment'))[:140] }}">Reply</button>
<div class="message-reactions" data-reaction-message-id="{{ item.id }}">
  <div class="reaction-summary">
    {% for reaction in reaction_map.get(item.id, []) %}
    <button type="button" class="reaction-chip{% if reaction.reacted_by_me %} reacted-by-me{% endif %}" data-emoji="{{ reaction.emoji }}" data-reacted-by-me="{{ 'true' if reaction.reacted_by_me else 'false' }}" aria-pressed="{{ 'true' if reaction.reacted_by_me else 'false' }}" aria-label="{{ reaction.emoji }} {{ reaction.count }} reactions">
      <span>{{ reaction.emoji }}</span><span class="reaction-count">{{ reaction.count }}</span>
    </button>
    {% endfor %}
  </div>
  <div class="reaction-picker" aria-label="React to message">
    {% for emoji in reaction_emojis %}
    <button type="button" class="reaction-button" data-emoji="{{ emoji }}" aria-label="React {{ emoji }}">{{ emoji }}</button>
    {% endfor %}
  </div>
</div>'''
),
(
'''@keyframes gotyaTyping {
  0%, 60%, 100% { opacity: .35; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
</style>''',
'''@keyframes gotyaTyping {
  0%, 60%, 100% { opacity: .35; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
.message-reactions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  margin: 3px 5px 1px;
  max-width: 100%;
}
.reaction-summary, .reaction-picker {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.reaction-picker { opacity: .78; }
.reaction-button, .reaction-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 30px;
  min-height: 28px;
  padding: 3px 6px;
  border: 1px solid #e4d9df;
  border-radius: 16px;
  background: #fff;
  color: #333;
  font: inherit;
  font-size: 15px;
  line-height: 1.2;
  cursor: pointer;
  touch-action: manipulation;
}
.reaction-button:hover, .reaction-chip:hover {
  background: #f8edf2;
  border-color: #c89aaa;
}
.reaction-chip.reacted-by-me, .reaction-button.reacted-by-me {
  background: #f6e3eb;
  border-color: #b76b88;
  box-shadow: inset 0 0 0 1px #b76b88;
}
.reaction-count {
  font-size: 11px;
  font-weight: 600;
}
.message-row.mine .message-reactions { justify-content: flex-end; }
@media (max-width: 600px) {
  .message-reactions { gap: 4px; margin-left: 3px; margin-right: 3px; }
  .reaction-button, .reaction-chip {
    min-width: 29px;
    min-height: 30px;
    padding: 4px 6px;
  }
}
</style>'''
),
(
'''  function addReplyQuote(container, replyTo) {''',
'''  const reactionEmojis = ["❤️", "😂", "👍", "😮", "😢", "🙏"];

  function renderReactionBar(row, messageId, reactions) {
    if (!row) return;

    let bar = row.querySelector(".message-reactions");
    if (!bar) {
      bar = makeElement("div", "message-reactions");
      bar.dataset.reactionMessageId = String(messageId);
      const replyButton = row.querySelector(".reply-message-btn");
      if (replyButton) replyButton.insertAdjacentElement("afterend", bar);
      else row.appendChild(bar);
    }

    bar.replaceChildren();

    const summary = makeElement("div", "reaction-summary");
    const picker = makeElement("div", "reaction-picker");
    picker.setAttribute("aria-label", "React to message");

    for (const reaction of reactions || []) {
      const chip = makeElement("button", "reaction-chip");
      chip.type = "button";
      chip.dataset.emoji = reaction.emoji;
      chip.dataset.reactedByMe = String(Boolean(reaction.reacted_by_me));
      chip.setAttribute("aria-pressed", String(Boolean(reaction.reacted_by_me)));
      chip.setAttribute(
        "aria-label",
        reaction.emoji + " " + reaction.count + " reactions"
      );
      if (reaction.reacted_by_me) chip.classList.add("reacted-by-me");
      chip.appendChild(makeElement("span", "", reaction.emoji));
      chip.appendChild(makeElement("span", "reaction-count", String(reaction.count)));
      summary.appendChild(chip);
    }

    for (const emoji of reactionEmojis) {
      const button = makeElement("button", "reaction-button", emoji);
      button.type = "button";
      button.dataset.emoji = emoji;
      button.setAttribute("aria-label", "React " + emoji);
      const mine = (reactions || []).some(
        reaction => reaction.emoji === emoji && reaction.reacted_by_me
      );
      button.dataset.reactedByMe = String(mine);
      button.setAttribute("aria-pressed", String(mine));
      if (mine) button.classList.add("reacted-by-me");
      picker.appendChild(button);
    }

    bar.appendChild(summary);
    bar.appendChild(picker);
  }

  async function toggleMessageReaction(button) {
    const bar = button.closest(".message-reactions");
    const messageId = bar && bar.dataset.reactionMessageId;
    const emoji = button.dataset.emoji;
    if (!messageId || !emoji) return;

    const removeReaction = button.dataset.reactedByMe === "true";
    button.disabled = true;

    try {
      const response = await fetch(
        `/messages/${encodeURIComponent(messageId)}/reaction`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
            "Accept": "application/json"
          },
          body: JSON.stringify({ emoji: removeReaction ? null : emoji })
        }
      );

      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Could not save reaction.");
      }

      const row = list.querySelector(
        '[data-message-id="' + messageId + '"]'
      );
      renderReactionBar(row, messageId, data.reactions || []);
    } catch (error) {
      console.warn("Gotya reaction update failed:", error);
      button.title = error.message || "Could not save reaction.";
    } finally {
      button.disabled = false;
    }
  }

  function updateReactionBar(messageId, reactions) {
    const row = list.querySelector(
      '[data-message-id="' + messageId + '"]'
    );
    if (row) renderReactionBar(row, messageId, reactions);
  }

  function addReplyQuote(container, replyTo) {'''
),
(
'''  list.addEventListener("click", event => {
    const button = event.target.closest(".reply-message-btn");''',
'''  list.addEventListener("click", event => {
    const reactionButton = event.target.closest(".reaction-button, .reaction-chip");
    if (reactionButton) {
      toggleMessageReaction(reactionButton);
      return;
    }

    const button = event.target.closest(".reply-message-btn");'''
),
(
'''    addReplyButton(row, message.id, message.sender_name, messagePreview);
    const previousRow =''',
'''    addReplyButton(row, message.id, message.sender_name, messagePreview);
    renderReactionBar(row, message.id, message.reactions || []);
    const previousRow ='''
),
(
'''      updateReceipts(data.receipts);
      if (typingIndicator) typingIndicator.hidden = !data.typing;''',
'''      for (const update of data.reaction_updates || []) {
        updateReactionBar(update.message_id, update.reactions || []);
      }
      updateReceipts(data.receipts);
      if (typingIndicator) typingIndicator.hidden = !data.typing;'''
)
]

for old, new in replacements:
    count = source.count(old)
    if count != 1:
        raise SystemExit(
            f"STOP: expected exactly one patch anchor, found {count}. "
            "No template changes were written."
        )

updated = source
for old, new in replacements:
    updated = updated.replace(old, new, 1)

backup = Path(
    "templates/conversation.html.before_reaction_ui_"
    + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
)
if backup.exists():
    raise SystemExit(f"STOP: backup already exists: {backup}")

shutil.copy2(path, backup)
path.write_text(updated, encoding="utf-8", newline="")

if path.read_text(encoding="utf-8") != updated:
    raise SystemExit(f"Verification failed. Backup: {backup}")

print("Reaction interface patch applied and verified.")
print(f"Backup: {backup}")
print(f"Anchors updated: {len(replacements)}")

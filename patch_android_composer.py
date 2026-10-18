from pathlib import Path
from datetime import datetime
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")

backup = p.with_name(
    f"conversation_android_composer_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(s, encoding="utf-8")

css = """
/* Android-friendly message composer */
.composer {
    display: flex;
    align-items: flex-end;
    gap: 6px;
    padding: 8px 8px;
    padding-bottom: max(8px, env(safe-area-inset-bottom));
    box-sizing: border-box;
}

.composer .composer-input-wrap {
    flex: 1;
    min-width: 0;
    padding: 4px 10px !important;
    border-radius: 20px !important;
    background: #f0f2f5;
}

.composer .composer-input-wrap textarea {
    display: block;
    width: 100%;
    min-width: 0;
    height: 26px;
    min-height: 26px !important;
    max-height: 54px !important;
    padding: 4px 1px !important;
    margin: 0;
    border: 0 !important;
    outline: none;
    resize: none;
    box-shadow: none !important;
    background: transparent;
    font-size: 16px !important;
    line-height: 18px !important;
    box-sizing: border-box !important;
    overflow-x: hidden;
    overflow-y: hidden;
    -webkit-appearance: none;
}

.composer .composer-icon {
    width: 40px;
    height: 42px;
    flex: 0 0 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: manipulation;
}

.composer .composer-icon svg {
    width: 25px;
    height: 25px;
}

.composer .send {
    width: 42px;
    height: 42px;
    flex: 0 0 42px;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: manipulation;
}

.composer .send svg {
    width: 25px;
    height: 25px;
}

.composer .selected-files {
    font-size: 12px;
    overflow-wrap: anywhere;
}

@media (max-width: 600px) {
    .composer {
        gap: 4px;
        padding-left: 5px;
        padding-right: 5px;
    }

    .composer .composer-input-wrap {
        padding-left: 9px !important;
        padding-right: 9px !important;
    }
}
"""

# Replace prior appended composer tuning blocks so they cannot fight this one.
start_marker = "/* Android-friendly message composer */"
if start_marker in s:
    s = s[:s.index(start_marker)].rstrip() + "\n"

if "</style>" not in s:
    print("STOP: Could not find the style section. No changes saved.")
    sys.exit(1)

s = s.replace("</style>", css + "\n</style>", 1)

old_js = '''function resizeMessageBox() {
  input.style.height = "26px";
  const maxHeight = 54; // Three lines at 18px each
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}'''

new_js = '''function resizeMessageBox() {
  input.style.height = "26px";
  const maxHeight = 54; // Three lines at 18px each
  input.style.height = Math.min(input.scrollHeight, maxHeight) + "px";
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}
'''

if old_js in s:
    s = s.replace(old_js, new_js, 1)
elif "function resizeMessageBox()" not in s:
    print("STOP: Message-box resize function not found. No changes saved.")
    sys.exit(1)

p.write_text(s, encoding="utf-8")
print("SUCCESS: Android-friendly composer styles applied.")
print("Backup:", backup)

from pathlib import Path
from datetime import datetime
import re
import sys

p = Path("templates/conversation.html")
s = p.read_text(encoding="utf-8")

backup = p.with_name(
    f"conversation_before_autogrow_fix_{datetime.now():%Y%m%d_%H%M%S}.html"
)
backup.write_text(s, encoding="utf-8")

pattern = re.compile(
    r'function resizeMessageBox\(\)\s*\{.*?\n\}',
    re.DOTALL
)

new_js = '''function resizeMessageBox() {
  // Let the browser recalculate wrapped lines before measuring.
  input.style.height = "auto";
  input.style.overflowY = "hidden";

  requestAnimationFrame(() => {
    const style = window.getComputedStyle(input);
    const lineHeight = parseFloat(style.lineHeight) || 20;
    const paddingTop = parseFloat(style.paddingTop) || 0;
    const paddingBottom = parseFloat(style.paddingBottom) || 0;
    const maxHeight = Math.ceil(lineHeight * 3 + paddingTop + paddingBottom);
    const neededHeight = input.scrollHeight;

    input.style.height = Math.min(neededHeight, maxHeight) + "px";
    input.style.overflowY = neededHeight > maxHeight ? "auto" : "hidden";
  });
}'''

if not pattern.search(s):
    print("STOP: Resize function not found. No changes saved.")
    sys.exit(1)

s = pattern.sub(new_js, s, count=1)

css = """
/* Reliable auto-growing message field */
.composer .composer-input-wrap textarea#messageInput {
    display: block !important;
    width: 100% !important;
    height: 26px;
    min-height: 26px !important;
    max-height: none !important;
    padding: 3px 1px !important;
    margin: 0 !important;
    box-sizing: border-box !important;
    line-height: 20px !important;
    font-size: 16px !important;
    resize: none !important;
    overflow-x: hidden !important;
    overflow-y: hidden;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
"""

if "</style>" not in s:
    print("STOP: Style section not found. No changes saved.")
    sys.exit(1)

s = s.replace("</style>", css + "\n</style>", 1)

# Also recalculate the box if the phone rotates or the viewport changes.
needle = 'input.addEventListener("input", resizeMessageBox);'
if needle not in s:
    print("STOP: Input listener not found. No changes saved.")
    sys.exit(1)

if 'window.addEventListener("resize", resizeMessageBox);' not in s:
    s = s.replace(
        needle,
        needle + '\nwindow.addEventListener("resize", resizeMessageBox);',
        1
    )

p.write_text(s, encoding="utf-8")
print("SUCCESS: Updated auto-growing textarea.")
print("Backup:", backup)

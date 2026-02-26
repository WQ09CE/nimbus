import re

with open("web-ui/src/app/globals.css", "r") as f:
    content = f.read()

# Fix spacing in message bubbles
content = content.replace(
    '.copilotKitMessage {\n  border-radius: 1rem !important;',
    '.copilotKitMessage {\n  border-radius: 1rem !important;\n  padding: 1rem 1.25rem !important;'
)

# Fix response buttons (copy, refresh, thumbs) overlapping/exceeding
content += '''

/* Fix response buttons (copy, refresh) clipping/overflow */
.copilotKitResponseButton {
  border-radius: 0.5rem !important;
  margin-top: 0.5rem !important;
  transform: scale(0.9);
  transform-origin: left center;
}

.copilotKitMessageContent {
  word-break: break-word;
  overflow-wrap: break-word;
}
'''

with open("web-ui/src/app/globals.css", "w") as f:
    f.write(content)


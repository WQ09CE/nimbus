import re

with open("web-ui/src/app/globals.css", "r") as f:
    content = f.read()

# Make sure buttons row does not overlap with message content by controlling action row spacing
content += '''

/* Fix spacing for the action buttons row */
.copilotKitMessageActions {
  margin-top: 0.75rem !important;
  gap: 0.5rem !important;
  opacity: 0.8;
}
.copilotKitMessageActions:hover {
  opacity: 1;
}
'''

with open("web-ui/src/app/globals.css", "w") as f:
    f.write(content)


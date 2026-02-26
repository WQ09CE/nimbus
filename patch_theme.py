import re

with open("web-ui/src/components/SubAgentCard.tsx", "r") as f:
    content = f.read()

content = content.replace(
    '"my-2 border-l-2 bg-card/80 backdrop-blur-sm"',
    '"my-3 border border-white/5 border-l-4 bg-slate-900/40 backdrop-blur-xl shadow-lg transition-all hover:bg-slate-900/60 rounded-xl overflow-hidden"'
)

# make header sleeker
content = content.replace(
    '"flex flex-row items-center gap-3 py-2 px-4 cursor-pointer hover:bg-accent/30 transition-colors"',
    '"flex flex-row items-center gap-3 py-2.5 px-4 cursor-pointer bg-black/20 hover:bg-black/40 transition-colors"'
)

with open("web-ui/src/components/SubAgentCard.tsx", "w") as f:
    f.write(content)

with open("web-ui/src/components/ToolCard.tsx", "r") as f:
    content = f.read()

content = content.replace(
    'compact ? "my-0.5 bg-transparent border-0 shadow-none" : "my-2 bg-card/80 backdrop-blur-sm"',
    'compact ? "my-1 bg-transparent border-0 shadow-none" : "my-3 border border-white/5 bg-slate-900/40 backdrop-blur-xl shadow-md rounded-xl overflow-hidden"'
)

content = content.replace(
    '"flex flex-row items-center gap-3 cursor-pointer hover:bg-accent/30 transition-colors"',
    '"flex flex-row items-center gap-3 cursor-pointer bg-white/[0.02] hover:bg-white/[0.04] transition-colors"'
)

with open("web-ui/src/components/ToolCard.tsx", "w") as f:
    f.write(content)


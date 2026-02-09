---
name: web-search
version: 1.0.0
description: Web search toolkit powered by Brave Search API - search the web and fetch page content
tools:
  - name: WebSearch
    description: "Search the web using Brave Search API. Returns titles, URLs, and descriptions for the query. Supports optional AI summary."
    entrypoint: scripts/web_search.py
    args:
      query:
        type: string
        description: "The search query string"
      count:
        type: integer
        description: "Number of results to return (default: 5, max: 20)"
      summary:
        type: boolean
        description: "Whether to include an AI-generated summary from Brave (default: false)"
  - name: WebFetch
    description: "Fetch and extract readable text content from a web page URL. Strips HTML to return clean text."
    entrypoint: scripts/web_fetch.py
    args:
      url:
        type: string
        description: "The URL of the web page to fetch"
      max_length:
        type: integer
        description: "Maximum character length of returned content (default: 10000)"
---

# Web Search — Internet Research Guidelines

You are equipped with the **Web Search** toolkit for searching the internet and fetching web content.

## When to Use

- When the user asks about recent events, news, or current information
- When you need to look up documentation, APIs, or technical references
- When the user explicitly asks you to "search" or "look up" something online
- When you need to verify facts or find authoritative sources
- When exploring unfamiliar technologies or libraries

## Recommended Workflow

1. **Start with `WebSearch`** to find relevant pages for the topic.
2. **Use `WebFetch`** to read the full content of the most promising results.
3. **Synthesize** the information and present it to the user with source citations.

## Tips

- Keep search queries concise and specific for better results.
- Use `summary=true` for quick overviews when you don't need full page content.
- When fetching pages, start with `max_length=10000` — increase only if you need more.
- Always cite your sources with URLs when presenting search results.
- For technical docs, fetch the specific documentation page rather than searching broadly.

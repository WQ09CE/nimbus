# Nimbus Web UI

A Pi-inspired terminal-style web interface for Nimbus Agent Framework.

## Features

- 🌑 **Dark Terminal Theme** - Monospace font, clean and focused
- ⚡ **Real-time Streaming** - SSE-based chat with live updates
- 🔧 **Tool Call Visualization** - Collapsible tool execution details
- 🎯 **Minimal UI** - Single-column layout, distraction-free

## Quick Start

### 1. Start Nimbus Server

```bash
cd ..
nimbus serve
# Server runs at http://localhost:4096
```

### 2. Start Web UI

```bash
npm install
npm run dev
# UI runs at http://localhost:3030
```

### 3. Open Browser

Visit [http://localhost:3030](http://localhost:3030)

## Architecture

```
┌─────────────┐
│  Next.js    │ @ :3030
│  (React)    │
└──────┬──────┘
       │ SSE Stream
       ↓
┌─────────────┐
│   Nimbus    │ @ :4096
│   Server    │ (FastAPI)
└──────┬──────┘
       │
       ↓
┌─────────────┐
│   Pi AI     │ (LLM Core)
│   (bridge)  │
└─────────────┘
```

## API Endpoints

- `POST /api/v1/sessions` - Create session
- `GET  /api/v1/sessions` - List sessions
- `POST /api/v1/sessions/{id}/chat` - Chat with SSE streaming

## Development

```bash
# Install dependencies
npm install

# Run dev server
npm run dev

# Build for production
npm run build
npm run start
```

## Configuration

Edit `.env.local` to change API URL:

```env
NEXT_PUBLIC_API_URL=http://localhost:4096
```

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **State**: Zustand
- **Styling**: Tailwind CSS
- **Transport**: SSE (Server-Sent Events)

## Design Principles

Inspired by Pi TUI:
- **Monospace** typography for clarity
- **Black background** with subtle borders
- **Minimal chrome** - focus on conversation
- **Streaming feedback** - live typing indicator
- **Collapsible details** - tool calls hidden by default

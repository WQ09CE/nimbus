import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { vi } from "vitest";
import { ChatMessage } from "../ChatMessage";

vi.mock("../MarkdownRenderer", () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-testid="markdown-content">{content}</div>,
}));

vi.mock("../tools/ToolCard", () => ({
  ToolCard: ({ tool }: { tool: any }) => <div data-testid="tool-card">{tool.name}</div>,
}));

vi.mock("../../../hooks/useTypewriter", () => ({
  useTypewriter: (text: string) => text,
}));

describe("ChatMessage", () => {
  const baseMessage = {
    id: "m1",
    role: "assistant" as const,
    content: "",
    parts: [],
    timestamp: Date.now(),
    toolCallsMap: {},
    toolResults: [],
  };

  it("renders text parts before tool parts in order", () => {
    const message = {
      ...baseMessage,
      content: "Hello with tools",
      parts: [
        { type: "text" as const, content: "Hello with tools" },
        { type: "tool" as const, toolCall: { id: "tool-1", name: "Read", arguments: { path: "/tmp/a.txt" } }, toolResult: { id: "tool-1", name: "Read", result: "ok" } },
      ],
      toolCallsMap: { "tool-1": { id: "tool-1", name: "Read", arguments: { path: "/tmp/a.txt" } } },
      toolResults: [{ id: "tool-1", name: "Read", result: "ok" }],
    };

    render(<ChatMessage message={message as any} isStreaming={false} />);

    const content = screen.getByTestId("markdown-content");
    const toolCard = screen.getByTestId("tool-card");
    expect(content.compareDocumentPosition(toolCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows reading indicator when streaming with no content", () => {
    const { container } = render(<ChatMessage message={baseMessage as any} isStreaming={true} />);

    const dots = container.querySelectorAll(".reading-indicator .dot");
    expect(dots.length).toBe(3);
  });

  it("does not show reading indicator when parts exist", () => {
    const message = {
      ...baseMessage,
      content: "Hello",
      parts: [{ type: "text" as const, content: "Hello" }],
    };

    const { container } = render(<ChatMessage message={message as any} isStreaming={true} />);

    const dots = container.querySelectorAll(".reading-indicator .dot");
    expect(dots.length).toBe(0);
    expect(screen.getByTestId("markdown-content")).toBeInTheDocument();
  });

  it("applies streaming-message class to last text part during streaming", () => {
    const message = {
      ...baseMessage,
      content: "Streaming text",
      parts: [{ type: "text" as const, content: "Streaming text" }],
    };

    const { container } = render(<ChatMessage message={message as any} isStreaming={true} />);

    const streamingEl = container.querySelector(".streaming-message");
    expect(streamingEl).toBeInTheDocument();
  });
});

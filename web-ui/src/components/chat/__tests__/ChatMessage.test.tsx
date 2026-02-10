import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { ChatMessage } from "../ChatMessage";

jest.mock("../MarkdownRenderer", () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-testid="markdown-content">{content}</div>,
}));

describe("ChatMessage", () => {
  const baseMessage = {
    id: "m1",
    role: "assistant" as const,
    content: "",
    timestamp: Date.now(),
    toolCalls: [],
    toolResults: [],
  };

  it("renders content before tools when both exist", () => {
    const message = {
      ...baseMessage,
      content: "Hello with tools",
      toolCalls: [
        {
          id: "tool-1",
          name: "Read",
          arguments: { path: "/tmp/a.txt" },
        },
      ],
      toolResults: [
        {
          id: "tool-1",
          result: { ok: true },
        },
      ],
    };

    render(<ChatMessage message={message as any} isStreaming={false} />);

    const content = screen.getByTestId("markdown-content");
    const toolsButton = screen.getByText("Used 1 Tools");
    expect(content.compareDocumentPosition(toolsButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows "Thinking..." when streaming with no content and no tools', () => {
    render(<ChatMessage message={baseMessage as any} isStreaming={true} />);

    expect(screen.getByText("Thinking...")).toBeInTheDocument();
  });

  it('shows "Generating response..." when streaming with tools but no running tool', () => {
    const message = {
      ...baseMessage,
      toolCalls: [
        {
          id: "tool-2",
          name: "Read",
          arguments: { path: "/tmp/b.txt" },
        },
      ],
      toolResults: [
        {
          id: "tool-2",
          result: { ok: true },
        },
      ],
    };

    render(<ChatMessage message={message as any} isStreaming={true} />);

    expect(screen.getByText("Generating response...")).toBeInTheDocument();
  });

  it('does not show "Generating response..." when streaming with tools and a running tool', () => {
    const message = {
      ...baseMessage,
      toolCalls: [
        {
          id: "tool-3",
          name: "Read",
          arguments: { path: "/tmp/c.txt" },
        },
      ],
      toolResults: [],
    };

    render(<ChatMessage message={message as any} isStreaming={true} />);

    expect(screen.queryByText("Generating response...")).not.toBeInTheDocument();
  });
});

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect } from "vitest";
import { SpawnAgentCard } from "../tools/SpawnAgentCard";

vi.mock("../MarkdownRenderer", () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-testid="markdown-mock">{content}</div>
}));

describe("SpawnAgentCard", () => {
    const mockSubEvents = [
        {
            type: "thinking",
            thought_preview: "I need to read the test file first",
            status: "OK"
        },
        {
            type: "tool",
            tool: "Read",
            step: 1,
            status: "OK",
            output_preview: "def test_something():..."
        },
        {
            type: "tool",
            tool: "Bash",
            step: 2,
            status: "ERROR",
            output_preview: "command not found"
        }
    ];

    const spawnAgentTool = {
        id: "call_123",
        name: "spawn_agent",
        args: { role: "Test Engineer", goal: "Fix tests" },
        status: "running" as const,
        sub_events: mockSubEvents
    };

    it("renders timeline events correctly when expanded", () => {
        // Force expanded state
        render(<SpawnAgentCard tool={spawnAgentTool} defaultState="expanded" />);
        
        // Since it's running, it starts collapsed. We click to expand.
        fireEvent.click(screen.getByText("Test Engineer"));
        
        // Check goal
        expect(screen.getByText("Fix tests")).toBeInTheDocument();

        // Check timeline events
        expect(screen.getByText("Thoughts:")).toBeInTheDocument();
        expect(screen.getByText(/I need to read the test file first/i)).toBeInTheDocument();
        
        // Tool steps
        expect(screen.getByText("[Read]")).toBeInTheDocument();
        expect(screen.getByText("Step 1")).toBeInTheDocument();
        expect(screen.getByText("def test_something():...")).toBeInTheDocument();

        expect(screen.getByText("[Bash]")).toBeInTheDocument();
        expect(screen.getByText("Step 2")).toBeInTheDocument();
        expect(screen.getByText("ERROR")).toBeInTheDocument();
        expect(screen.getByText("command not found")).toBeInTheDocument();
    });

    it("auto-expands and shows final deliverable when completed", () => {
        const completedTool = {
            ...spawnAgentTool,
            status: "completed" as const,
            result: "Tests are fixed."
        };

        render(<SpawnAgentCard tool={completedTool} />);
        
        expect(screen.getByText("Completed")).toBeInTheDocument();
        expect(screen.getByText("Final Deliverable")).toBeInTheDocument();
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("Tests are fixed.");
    });

    it("collapses/expands when header is clicked", () => {
        const collapsedTool = {
            ...spawnAgentTool,
            status: "completed" as const
        };

        render(<SpawnAgentCard tool={collapsedTool} defaultState="collapsed" />);
        
        // Content should not be visible
        expect(screen.queryByText("Fix tests")).not.toBeInTheDocument();

        // Click to expand
        fireEvent.click(screen.getByText("Test Engineer"));

        // Content should be visible now
        expect(screen.getByText("Fix tests")).toBeInTheDocument();
        expect(screen.getByText("Execution Timeline")).toBeInTheDocument();
    });
});

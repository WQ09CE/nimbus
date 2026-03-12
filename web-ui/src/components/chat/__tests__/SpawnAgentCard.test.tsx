import React from "react";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        // Use completed status so it starts expanded by default
        const completedWithEvents = {
            ...spawnAgentTool,
            status: "completed" as const,
        };
        render(<SpawnAgentCard tool={completedWithEvents} defaultState="expanded" />);
        
        // Check goal
        expect(screen.getByText("Fix tests")).toBeInTheDocument();

        // Check timeline section exists
        expect(screen.getByText("Execution Timeline")).toBeInTheDocument();

        // Check thinking event
        expect(screen.getByText("Thoughts:")).toBeInTheDocument();
        expect(screen.getByText(/I need to read the test file first/i)).toBeInTheDocument();
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

    it("collapses/expands when header is clicked", async () => {
        const user = userEvent.setup();
        const collapsedTool = {
            ...spawnAgentTool,
            status: "completed" as const
        };

        render(<SpawnAgentCard tool={collapsedTool} defaultState="collapsed" />);
        
        // Content should not be visible
        expect(screen.queryByText("Fix tests")).not.toBeInTheDocument();

        // Click to expand
        await user.click(screen.getByText("Test Engineer"));

        // Content should be visible now
        expect(screen.getByText("Fix tests")).toBeInTheDocument();
        expect(screen.getByText("Execution Timeline")).toBeInTheDocument();
    });
});

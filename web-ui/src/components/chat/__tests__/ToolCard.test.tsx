/**
 * ToolCard — Component Tests
 *
 * Tests status display, expand/collapse, summary extraction, meta-tool delegation.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import { vi, describe, it, expect } from "vitest";

// Mock child components
vi.mock("../tools/ToolDisplay", () => ({
    ToolDisplay: ({ tool, isExpanded }: any) => (
        <div data-testid="tool-display" data-expanded={isExpanded}>
            {tool.result || ""}
        </div>
    ),
}));

vi.mock("../tools/DispatchCard", () => ({
    DispatchCard: ({ tool }: any) => (
        <div data-testid="dispatch-card">{tool.name}</div>
    ),
}));

vi.mock("../tools/LiveTimer", () => ({
    LiveTimer: () => <span data-testid="live-timer">0s</span>,
}));

import { ToolCard } from "../tools/ToolCard";

// ============================================================
// Helpers
// ============================================================

const baseTool = {
    id: "tc-1",
    name: "Bash",
    args: { command: "echo hello" },
    status: "completed" as const,
    duration: 150,
};

// ============================================================
// Tests
// ============================================================

describe("ToolCard", () => {
    // ----------------------------------------------------------
    // Rendering basics
    // ----------------------------------------------------------

    it("renders tool name", () => {
        render(<ToolCard tool={baseTool} />);
        expect(screen.getByText("Bash")).toBeInTheDocument();
    });

    it("renders command summary for Bash tool", () => {
        render(<ToolCard tool={baseTool} />);
        expect(screen.getByText("echo hello")).toBeInTheDocument();
    });

    it("renders file path summary for Read tool", () => {
        const tool = {
            ...baseTool,
            name: "Read",
            args: { path: "/Users/wq/project/src/index.ts" },
        };
        render(<ToolCard tool={tool} />);
        expect(screen.getByText("src/index.ts")).toBeInTheDocument();
    });

    it("renders duration for completed tools", () => {
        render(<ToolCard tool={baseTool} />);
        expect(screen.getByText("150ms")).toBeInTheDocument();
    });

    it("renders LiveTimer for running tools", () => {
        const tool = { ...baseTool, status: "running" as const, duration: undefined };
        render(<ToolCard tool={tool} />);
        expect(screen.getByTestId("live-timer")).toBeInTheDocument();
    });

    // ----------------------------------------------------------
    // Status display
    // ----------------------------------------------------------

    it("shows completed status indicator (emerald dot)", () => {
        const { container } = render(<ToolCard tool={baseTool} />);
        expect(container.querySelector(".bg-emerald-400")).toBeInTheDocument();
    });

    it("shows running status indicator (amber dot)", () => {
        const tool = { ...baseTool, status: "running" as const };
        const { container } = render(<ToolCard tool={tool} />);
        expect(container.querySelector(".bg-amber-400")).toBeInTheDocument();
    });

    it("shows failed status indicator (red dot)", () => {
        const tool = { ...baseTool, status: "failed" as const };
        const { container } = render(<ToolCard tool={tool} />);
        expect(container.querySelector(".bg-red-400")).toBeInTheDocument();
    });

    // ----------------------------------------------------------
    // Expand / Collapse
    // ----------------------------------------------------------

    it("starts expanded for completed tools (observability default)", () => {
        render(<ToolCard tool={baseTool} />);
        expect(screen.getByTestId("tool-display")).toBeInTheDocument();
    });

    it("starts expanded for running tools", () => {
        const tool = { ...baseTool, status: "running" as const };
        render(<ToolCard tool={tool} />);
        expect(screen.getByTestId("tool-display")).toBeInTheDocument();
    });

    it("toggles expanded state on header click", () => {
        render(<ToolCard tool={baseTool} />);
        // Initially expanded (observability default)
        expect(screen.getByTestId("tool-display")).toBeInTheDocument();

        // Click to collapse
        fireEvent.click(screen.getByText("Bash"));
        expect(screen.queryByTestId("tool-display")).toBeNull();

        // Click to expand again
        fireEvent.click(screen.getByText("Bash"));
        expect(screen.getByTestId("tool-display")).toBeInTheDocument();
    });

    it("respects defaultExpanded=true", () => {
        render(<ToolCard tool={baseTool} defaultExpanded={true} />);
        expect(screen.getByTestId("tool-display")).toBeInTheDocument();
    });

    // ----------------------------------------------------------
    // Meta-tool delegation
    // ----------------------------------------------------------

    it("renders DispatchCard for Dispatch tool", () => {
        const tool = { ...baseTool, name: "Dispatch" };
        render(<ToolCard tool={tool} />);
        expect(screen.getByTestId("dispatch-card")).toBeInTheDocument();
        expect(screen.queryByTestId("tool-display")).toBeNull();
    });

    it("renders DispatchCard for Explore tool", () => {
        const tool = { ...baseTool, name: "Explore" };
        render(<ToolCard tool={tool} />);
        expect(screen.getByTestId("dispatch-card")).toBeInTheDocument();
    });

    it("renders DispatchCard for ParallelDispatch tool", () => {
        const tool = { ...baseTool, name: "ParallelDispatch" };
        render(<ToolCard tool={tool} />);
        expect(screen.getByTestId("dispatch-card")).toBeInTheDocument();
    });

    // ----------------------------------------------------------
    // Summary extraction edge cases
    // ----------------------------------------------------------

    it("truncates long command to 50 chars", () => {
        const longCmd = "a".repeat(60);
        const tool = { ...baseTool, args: { command: longCmd } };
        render(<ToolCard tool={tool} />);
        const summaryEl = screen.getByText(/\.\.\.$/);
        expect(summaryEl.textContent!.length).toBeLessThanOrEqual(50);
    });

    it("shows Grep pattern and filename", () => {
        const tool = {
            ...baseTool,
            name: "Grep",
            args: { pattern: "TODO", path: "/project/src/main.rs" },
        };
        render(<ToolCard tool={tool} />);
        expect(screen.getByText("TODO in main.rs")).toBeInTheDocument();
    });

    it("renders gracefully when args is null", () => {
        const tool = { ...baseTool, args: null };
        const { container } = render(<ToolCard tool={tool} />);
        expect(container.querySelector('[data-testid="tool-card"]')).toBeInTheDocument();
    });
});

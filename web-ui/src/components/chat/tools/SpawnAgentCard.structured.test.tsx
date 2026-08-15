import React from "react";
import { render, screen } from "@testing-library/react";
import { SpawnAgentCard } from "./SpawnAgentCard";

// Mocking dependencies
jest.mock("../MarkdownRenderer", () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-testid="markdown-mock">{content}</div>
}));

jest.mock("./LiveTimer", () => ({
  LiveTimer: () => <span>00:00</span>
}));

describe("SpawnAgentCard Structured Result", () => {
    const spawnAgentTool = {
        id: "call_123",
        name: "spawn_agent",
        args: { role: "Test Engineer", goal: "Fix tests" },
        status: "completed" as const,
        sub_events: []
    };

    it("renders structured result contract correctly", () => {
        const structuredResult = {
            summary: "Successfully fixed all identified issues.",
            key_findings: ["Found a memory leak in the database connection.", "Corrected inconsistent logging."],
            artifacts: ["log-fix.patch", "metrics-report.pdf"],
            files_touched: ["src/db/connection.ts", "src/utils/logger.ts"],
            todos_completed: ["Analyze logs", "Fix leak"],
            todos_remaining: ["Monitor production"],
            errors: ["Timeout during cleanup (ignored)"],
            scratchpad_path: "/tmp/scratchpad.md"
        };
        const toolWithStructuredResult = {
            ...spawnAgentTool,
            result: structuredResult
        };

        render(<SpawnAgentCard tool={toolWithStructuredResult} />);

        // Sections
        expect(screen.getByText("Summary")).toBeInTheDocument();
        expect(screen.getByText("Key Findings")).toBeInTheDocument();
        expect(screen.getByText("Artifacts")).toBeInTheDocument();
        expect(screen.getByText("Files Touched")).toBeInTheDocument();
        expect(screen.getByText("Completed Tasks")).toBeInTheDocument();
        expect(screen.getByText("Remaining Tasks")).toBeInTheDocument();
        expect(screen.getByText("Errors Encountered")).toBeInTheDocument();
        expect(screen.getByText("Scratchpad")).toBeInTheDocument();

        // Content
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("Successfully fixed all identified issues.");
        expect(screen.getByText("Found a memory leak in the database connection.")).toBeInTheDocument();
        expect(screen.getByText("log-fix.patch")).toBeInTheDocument();
        expect(screen.getByText("src/db/connection.ts")).toBeInTheDocument();
        expect(screen.getByText("Analyze logs")).toBeInTheDocument();
        expect(screen.getByText("Monitor production")).toBeInTheDocument();
        expect(screen.getByText("Timeout during cleanup (ignored)")).toBeInTheDocument();
        expect(screen.getByText("/tmp/scratchpad.md")).toBeInTheDocument();
    });

    it("falls back to JSON for non-standard results", () => {
        const nonStandardResult = { some: "data", and: 123 };
        const toolWithGenericResult = {
            ...spawnAgentTool,
            result: nonStandardResult
        };

        render(<SpawnAgentCard tool={toolWithGenericResult} />);

        expect(screen.getByText("Final Deliverable")).toBeInTheDocument();
        const markdownMock = screen.getByTestId("markdown-mock");
        expect(markdownMock).toHaveTextContent(/"some": "data"/);
        expect(markdownMock).toHaveTextContent(/"and": 123/);
    });

    it("extracts deliverable from result.deliverable", () => {
        const toolWithNestedResult = {
            ...spawnAgentTool,
            result: { deliverable: "Nested content" }
        };

        render(<SpawnAgentCard tool={toolWithNestedResult} />);
        expect(screen.getByText("Final Deliverable")).toBeInTheDocument();
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("Nested content");
    });

    it("extracts structured result from result.deliverable", () => {
        const toolWithNestedStructured = {
            ...spawnAgentTool,
            result: { 
                deliverable: { summary: "Nested structured summary" }
            }
        };

        render(<SpawnAgentCard tool={toolWithNestedStructured} />);
        expect(screen.getByText("Summary")).toBeInTheDocument();
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("Nested structured summary");
    });

    it("extracts deliverable from result.ui_detail.deliverable", () => {
        const toolWithDeepNestedResult = {
            ...spawnAgentTool,
            result: { 
                ui_detail: { deliverable: "Deep nested content" }
            }
        };

        render(<SpawnAgentCard tool={toolWithDeepNestedResult} />);
        expect(screen.getByText("Final Deliverable")).toBeInTheDocument();
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("Deep nested content");
    });

    it("extracts structured result from result.ui_detail", () => {
        const toolWithUIDetailStructured = {
            ...spawnAgentTool,
            result: { 
                ui_detail: { summary: "UI detail summary" }
            }
        };

        render(<SpawnAgentCard tool={toolWithUIDetailStructured} />);
        expect(screen.getByText("Summary")).toBeInTheDocument();
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("UI detail summary");
    });

    it("handles plain string results", () => {
        const toolWithStringResult = {
            ...spawnAgentTool,
            result: "Plain string result"
        };

        render(<SpawnAgentCard tool={toolWithStringResult} />);
        expect(screen.getByText("Final Deliverable")).toBeInTheDocument();
        expect(screen.getByTestId("markdown-mock")).toHaveTextContent("Plain string result");
    });
});

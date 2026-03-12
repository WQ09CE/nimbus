/**
 * TokenFooter — Component Tests
 *
 * Tests formatting logic and conditional rendering of token usage stats.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";

// Mock the zustand store
const mockTokenUsage = { current: null as any };
vi.mock("@/stores/chat-store", () => ({
    useChatStore: (selector: any) => selector({ tokenUsage: mockTokenUsage.current }),
}));

import { TokenFooter } from "../TokenFooter";

describe("TokenFooter", () => {
    beforeEach(() => {
        mockTokenUsage.current = null;
    });

    it("renders nothing when tokenUsage is null", () => {
        const { container } = render(<TokenFooter />);
        expect(container.querySelector(".token-footer")).toBeNull();
    });

    it("renders nothing when total is 0", () => {
        mockTokenUsage.current = { input: 0, output: 0, cache_read: 0, total: 0 };
        const { container } = render(<TokenFooter />);
        expect(container.querySelector(".token-footer")).toBeNull();
    });

    it("renders input and output tokens", () => {
        mockTokenUsage.current = { input: 500, output: 200, cache_read: 0, total: 700 };
        render(<TokenFooter />);
        expect(screen.getByTitle("Input tokens")).toHaveTextContent("500");
        expect(screen.getByTitle("Output tokens")).toHaveTextContent("200");
    });

    it("formats tokens with K suffix for thousands", () => {
        mockTokenUsage.current = { input: 1500, output: 2300, cache_read: 0, total: 3800 };
        render(<TokenFooter />);
        expect(screen.getByTitle("Input tokens")).toHaveTextContent("1.5K");
        expect(screen.getByTitle("Output tokens")).toHaveTextContent("2.3K");
    });

    it("formats tokens with M suffix for millions", () => {
        mockTokenUsage.current = { input: 1_500_000, output: 200, cache_read: 0, total: 1_500_200 };
        render(<TokenFooter />);
        expect(screen.getByTitle("Input tokens")).toHaveTextContent("1.5M");
    });

    it("shows cache read tokens when present", () => {
        mockTokenUsage.current = { input: 100, output: 50, cache_read: 300, total: 450 };
        render(<TokenFooter />);
        const cache = screen.getByTitle("Cache read tokens");
        expect(cache).toBeInTheDocument();
        expect(cache).toHaveTextContent("300");
    });

    it("hides cache read tokens when zero", () => {
        mockTokenUsage.current = { input: 100, output: 50, cache_read: 0, total: 150 };
        render(<TokenFooter />);
        expect(screen.queryByTitle("Cache read tokens")).toBeNull();
    });

    it("shows cost when present and > 0", () => {
        mockTokenUsage.current = {
            input: 100, output: 50, cache_read: 0, total: 150,
            cost: { input: 0.001, output: 0.0005, cache_read: 0, cache_write: 0, total: 0.0015 },
        };
        render(<TokenFooter />);
        expect(screen.getByTitle("Total cost")).toHaveTextContent("$0.002");
    });

    it("hides cost when zero", () => {
        mockTokenUsage.current = {
            input: 100, output: 50, cache_read: 0, total: 150,
            cost: { total: 0 },
        };
        render(<TokenFooter />);
        expect(screen.queryByTitle("Total cost")).toBeNull();
    });

    it("shows <$0.001 for very small costs", () => {
        mockTokenUsage.current = {
            input: 10, output: 5, cache_read: 0, total: 15,
            cost: { total: 0.0001 },
        };
        render(<TokenFooter />);
        expect(screen.getByTitle("Total cost")).toHaveTextContent("<$0.001");
    });
});

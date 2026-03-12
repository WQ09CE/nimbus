/**
 * ChatInput — Component Tests
 *
 * Tests keyboard shortcuts, send/interrupt, attachment handling, and state management.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";

import { ChatInput } from "../ChatInput";

describe("ChatInput", () => {
    let onSend: ReturnType<typeof vi.fn>;
    let onInterrupt: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        onSend = vi.fn();
        onInterrupt = vi.fn();
    });

    function renderInput(props: Partial<React.ComponentProps<typeof ChatInput>> = {}) {
        return render(
            <ChatInput
                onSend={onSend}
                onInterrupt={onInterrupt}
                disabled={false}
                isStreaming={false}
                isInterrupting={false}
                placeholder="Send a message..."
                {...props}
            />
        );
    }

    // ----------------------------------------------------------
    // Basic rendering
    // ----------------------------------------------------------

    it("renders textarea with placeholder", () => {
        renderInput();
        expect(screen.getByPlaceholderText("Send a message...")).toBeInTheDocument();
    });

    it("renders send button", () => {
        renderInput();
        // Send button should exist (may be disabled when empty)
        const buttons = screen.getAllByRole("button");
        expect(buttons.length).toBeGreaterThan(0);
    });

    // ----------------------------------------------------------
    // Text input and send
    // ----------------------------------------------------------

    it("calls onSend when Enter is pressed with content", () => {
        renderInput();
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.change(textarea, { target: { value: "Hello" } });
        fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
        expect(onSend).toHaveBeenCalledWith("Hello", undefined);
    });

    it("does NOT send on Shift+Enter (allows newline)", () => {
        renderInput();
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.change(textarea, { target: { value: "Hello" } });
        fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
        expect(onSend).not.toHaveBeenCalled();
    });

    it("does NOT send when textarea is empty", () => {
        renderInput();
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
        expect(onSend).not.toHaveBeenCalled();
    });

    it("does NOT send when only whitespace", () => {
        renderInput();
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.change(textarea, { target: { value: "   " } });
        fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
        expect(onSend).not.toHaveBeenCalled();
    });

    it("clears textarea after sending", () => {
        renderInput();
        const textarea = screen.getByPlaceholderText("Send a message...") as HTMLTextAreaElement;
        fireEvent.change(textarea, { target: { value: "Hello" } });
        fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
        expect(textarea.value).toBe("");
    });

    // ----------------------------------------------------------
    // Streaming / Interrupt
    // ----------------------------------------------------------

    it("calls onInterrupt on Escape during streaming", () => {
        renderInput({ isStreaming: true });
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.keyDown(textarea, { key: "Escape" });
        expect(onInterrupt).toHaveBeenCalled();
    });

    it("does NOT call onInterrupt on Escape when not streaming", () => {
        renderInput({ isStreaming: false });
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.keyDown(textarea, { key: "Escape" });
        expect(onInterrupt).not.toHaveBeenCalled();
    });

    // ----------------------------------------------------------
    // Disabled state
    // ----------------------------------------------------------

    it("does not send when disabled", () => {
        renderInput({ disabled: true });
        const textarea = screen.getByPlaceholderText("Send a message...");
        fireEvent.change(textarea, { target: { value: "test" } });
        fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
        expect(onSend).not.toHaveBeenCalled();
    });
});

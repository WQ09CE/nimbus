import { useState, useEffect, useRef } from 'react';

/**
 * A hook that smoothly yields characters over time during streaming,
 * using requestAnimationFrame to mask network jitter.
 * 
 * @param text The target text to stream towards
 * @param isStreaming Whether the stream is active
 * @param charsPerMs Speed of the typewriter (e.g. 2 char per ms = 2000 chars per sec)
 */
export function useTypewriter(text: string, isStreaming: boolean, charsPerMs: number = 2) {
    // When streaming, start from empty so the typewriter animation activates.
    // When not streaming (e.g. history load), show full text immediately.
    const [displayedText, setDisplayedText] = useState(isStreaming ? "" : text);
    const indexRef = useRef(isStreaming ? 0 : text.length);
    const lastTimeRef = useRef(performance.now());
    const rafRef = useRef<number | null>(null);

    useEffect(() => {
        // If not streaming, jump to the end immediately
        if (!isStreaming) {
            if (rafRef.current) cancelAnimationFrame(rafRef.current);
            setDisplayedText(text);
            indexRef.current = text.length;
            return;
        }

        // If text shrank (e.g. system reset/clear), reset state immediately
        if (text.length < indexRef.current) {
            setDisplayedText(text);
            indexRef.current = text.length;
            lastTimeRef.current = performance.now();
            return;
        }

        // Still streaming, nothing new to type
        if (indexRef.current >= text.length) {
            return;
        }

        // Animation loop to catch up to `text.length`
        const animate = (time: number) => {
            const delta = Math.max(0, time - lastTimeRef.current);
            // Calculate how many characters we can add this frame
            const charsToAdd = Math.floor(delta * charsPerMs);

            if (charsToAdd > 0) {
                // Fast-forward if we are suspiciously far behind (e.g. tab was inactive)
                // or if network gave us a massive chunk all at once (don't type 10,000 chars slowly)
                const gap = text.length - indexRef.current;
                let actualAdd = charsToAdd;

                // If gap is more than 500 chars, fast forward to 100 chars behind to avoid extreme lag
                if (gap > 500) {
                    actualAdd = gap - 100;
                }

                indexRef.current = Math.min(text.length, indexRef.current + actualAdd);
                setDisplayedText(text.slice(0, indexRef.current));
                lastTimeRef.current = time;
            }

            if (indexRef.current < text.length) {
                rafRef.current = requestAnimationFrame(animate);
            }
        };

        rafRef.current = requestAnimationFrame(animate);

        return () => {
            if (rafRef.current) cancelAnimationFrame(rafRef.current);
        };
    }, [text, isStreaming, charsPerMs]);

    return displayedText;
}

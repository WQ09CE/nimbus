import React, { useRef, useEffect } from 'react';

export function LiveTimer() {
    const ref = useRef<HTMLSpanElement>(null);
    const startTime = useRef(Date.now());

    useEffect(() => {
        const interval = setInterval(() => {
            if (ref.current) {
                ref.current.textContent = `[${((Date.now() - startTime.current) / 1000).toFixed(1)}s]`;
            }
        }, 100);
        return () => clearInterval(interval);
    }, []);

    return (
        <span ref={ref} className="text-[10px] font-mono text-amber-500/80 tracking-tighter">
            [0.0s]
        </span>
    );
}

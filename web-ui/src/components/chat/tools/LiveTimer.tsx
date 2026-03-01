import React, { useState, useEffect } from 'react';

export function LiveTimer() {
    const [startTime] = useState(Date.now());
    const [elapsed, setElapsed] = useState(0);

    useEffect(() => {
        // Update every 100ms for smooth 0.1s increments
        const interval = setInterval(() => {
            setElapsed(Date.now() - startTime);
        }, 100);
        return () => clearInterval(interval);
    }, [startTime]);

    return (
        <span className="text-[10px] font-mono text-amber-500/80 tracking-tighter">
            [{(elapsed / 1000).toFixed(1)}s]
        </span>
    );
}

"use client";

import { useState, useEffect } from "react";
import { useChatStore } from "@/stores";

export function ArtifactViewer() {
    const activeArtifact = useChatStore(s => s.activeArtifact);
    const closeArtifact = useChatStore(s => s.closeArtifact);

    const [content, setContent] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // When activeArtifact changes, we would fetch it from the backend NimFS
    useEffect(() => {
        let isMounted = true;

        const fetchArtifact = async () => {
            if (!activeArtifact?.ref) {
                setContent(null);
                return;
            }

            setIsLoading(true);
            setError(null);

            try {
                const res = await fetch(`/api/artifacts?ref=${encodeURIComponent(activeArtifact.ref)}`);

                if (!res.ok) {
                    throw new Error(`Failed to load artifact: ${res.statusText}`);
                }

                const data = await res.json();

                if (isMounted) {
                    // If the backend returns a content string directly or inside a content field
                    setContent(typeof data === "string" ? data : (data.content || JSON.stringify(data, null, 2)));
                }
            } catch (err: any) {
                if (isMounted) {
                    setError(err.message || "Failed to load artifact details.");
                    setContent(activeArtifact.summary || "Artifact preview pending backend availability.");
                }
            } finally {
                if (isMounted) {
                    setIsLoading(false);
                }
            }
        };

        fetchArtifact();

        return () => { isMounted = false; };
    }, [activeArtifact]);

    if (!activeArtifact) return null;

    return (
        <div className="flex flex-col h-full bg-nimbus-surface border-l border-nimbus-border shadow-2xl z-40 relative">
            <div className="flex items-center justify-between p-4 border-b border-nimbus-border bg-nimbus-surface/80 backdrop-blur-md sticky top-0">
                <h3 className="font-semibold text-gray-200 text-sm flex items-center gap-2">
                    <span className="text-sky-400">📄</span>
                    Artifact Preview
                </h3>
                <button
                    onClick={closeArtifact}
                    className="text-gray-400 hover:text-white transition-colors p-1"
                    title="Close Artifact"
                >
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 hide-scrollbar">
                {isLoading ? (
                    <div className="flex flex-col items-center justify-center h-40 opacity-70">
                        <span className="relative flex h-3 w-3 mb-3">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-3 w-3 bg-sky-500"></span>
                        </span>
                        <p className="text-sm text-gray-400 font-mono">Loading NimFS Artifact...</p>
                    </div>
                ) : (
                    <div className="text-sm text-gray-300 whitespace-pre-wrap">
                        <div className="bg-black/30 w-full mb-4 rounded border border-white/5 overflow-hidden">
                            <div className="px-3 py-1.5 bg-white/5 border-b border-white/5 font-mono text-[10px] text-gray-400 tracking-wider uppercase flex justify-between">
                                <span>Metadata</span>
                                {error && <span className="text-red-400">Fetch Failed</span>}
                            </div>
                            <div className="p-3 font-mono text-xs text-nimbus-text-dim break-all">
                                <div className="mb-1"><span className="opacity-50 inline-block w-12">REF:</span> {activeArtifact.ref}</div>
                                <div><span className="opacity-50 inline-block w-12">TYPE:</span> {activeArtifact.type.toUpperCase()}</div>
                            </div>
                        </div>

                        {error && (
                            <div className="bg-red-500/10 border border-red-500/30 rounded p-3 mb-4 text-red-400 text-xs font-mono">
                                {error}
                            </div>
                        )}

                        <div className={`p-1 ${error ? "opacity-60 grayscale" : ""}`}>
                            {content}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

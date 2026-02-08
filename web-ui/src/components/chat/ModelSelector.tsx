"use client";

import { useState, useEffect, useRef } from "react";
import { Session, listModels, updateSession, Model } from "@/lib/api/sessions";

interface ModelSelectorProps {
    session: Session;
    onChange: () => void; // Trigger reload
}

export function ModelSelector({ session, onChange }: ModelSelectorProps) {
    const [models, setModels] = useState<Model[]>([]);
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Local override to show selection immediately while saving
    const [optimisticModelId, setOptimisticModelId] = useState<string | null>(null);

    const containerRef = useRef<HTMLDivElement>(null);

    // Close when clicking outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    useEffect(() => {
        if (isOpen && models.length === 0) {
            setLoading(true);
            listModels()
                .then(list => {
                    setModels(list);
                    setLoading(false);
                })
                .catch(err => {
                    console.error("Failed to list models", err);
                    setError("Failed to load models");
                    setLoading(false);
                });
        }
    }, [isOpen, models.length]);

    // Use optimistic ID if setting, otherwise fallback to session
    // Default to "default" if nothing set
    const currentConfig = session.llm_config || {};
    const sessionModelId = currentConfig.model_id || "default";
    const currentModelId = optimisticModelId || sessionModelId;

    // Display name logic
    const displayName = currentModelId.split('/').pop() || currentModelId;

    const handleSelect = async (fullModelId: string) => {
        console.log("[ModelSelector] Selecting model:", fullModelId);

        // Expected fullModelId format from API: "provider/model_id"
        // e.g. "anthropic/claude-3-opus-20240229"

        let provider = "anthropic";
        let modelId = fullModelId;

        // If the ID contains a slash, we assume the first part is the provider
        // UNLESS it is a path-like ID that doesn't follow the provider convention.
        // But for our list_models implementation, we control the IDs.
        if (fullModelId.includes('/')) {
            const parts = fullModelId.split('/');
            provider = parts[0];
            modelId = parts.slice(1).join('/');
        }

        // Optimistically update UI
        setOptimisticModelId(modelId);
        setLoading(true);
        setIsOpen(false);

        try {
            console.log("[ModelSelector] Sending update:", { provider, model_id: modelId });
            await updateSession(session.id, {
                llm_config: {
                    provider,
                    model_id: modelId,
                }
            });
            console.log("[ModelSelector] Update success");

            // Clear optimistic state after successful reload
            // We delay slightly to prevent flickering if reload is fast
            setTimeout(() => {
                setOptimisticModelId(null);
                onChange(); // Reload session from server
            }, 100);

        } catch (err) {
            console.error("[ModelSelector] Failed to update model", err);
            setError("Failed to update model");
            setOptimisticModelId(null); // Revert on error
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="relative text-left" ref={containerRef}>
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[#333] hover:border-gray-600 bg-[#1c1c1c] hover:bg-[#252525] transition-all group"
                title="Change Model"
            >
                <span className="text-[10px] uppercase font-bold text-gray-500 group-hover:text-gray-400">Model</span>
                <span className="text-xs font-medium text-blue-400 group-hover:text-blue-300 truncate max-w-[150px]">
                    {loading ? "Updating..." : displayName}
                </span>
                <svg
                    className={`w-3 h-3 text-gray-500 transition-transform ${isOpen ? "rotate-180" : ""}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor"
                >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {isOpen && (
                <div className="absolute right-0 mt-2 w-72 max-h-[400px] overflow-y-auto bg-[#1c1c1c] border border-[#333] rounded-lg shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 custom-scrollbar">
                    <div className="sticky top-0 bg-[#1c1c1c]/95 backdrop-blur px-3 py-2 border-b border-[#333] text-[10px] font-bold text-gray-500 uppercase flex justify-between z-10">
                        <span>Select Model</span>
                        {loading && <span className="text-blue-500 animate-pulse">Loading...</span>}
                    </div>

                    <div className="p-1 space-y-0.5">
                        {error ? (
                            <div className="px-3 py-2 text-xs text-red-400">{error}</div>
                        ) : models.length === 0 && !loading ? (
                            <div className="px-3 py-2 text-xs text-gray-500 text-center">No models available</div>
                        ) : (
                            models.map((model) => {
                                // Matching logic
                                // model.id might be "anthropic/claude..."
                                // currentModelId might be "claude..."
                                const modelShortId = model.id.split('/').slice(1).join('/') || model.id;
                                const isSelected = currentModelId === modelShortId || currentModelId === model.id;

                                return (
                                    <button
                                        key={model.id}
                                        onClick={() => handleSelect(model.id)}
                                        className={`
                      w-full text-left px-3 py-2.5 rounded-md text-xs transition-colors flex items-center justify-between group
                      ${isSelected ? "bg-blue-500/10 text-blue-400" : "text-gray-300 hover:bg-[#2a2a2a] hover:text-white"}
                    `}
                                    >
                                        <div className="flex flex-col gap-0.5 overflow-hidden">
                                            <span className="font-medium truncate">{model.id.split('/').pop()}</span>
                                            <span className="text-[10px] text-gray-500 font-mono opacity-70 group-hover:opacity-100 truncate">{model.id}</span>
                                        </div>
                                        {isSelected && (
                                            <svg className="w-3.5 h-3.5 text-blue-500 shrink-0 ml-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                            </svg>
                                        )}
                                    </button>
                                );
                            })
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

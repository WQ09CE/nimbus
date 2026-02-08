"use client";

import { useState, useEffect } from "react";
import { FileNode, listFiles } from "@/lib/api/sessions";

interface FileExplorerProps {
    sessionId: string;
}

export function FileExplorer({ sessionId }: FileExplorerProps) {
    const [rootFiles, setRootFiles] = useState<FileNode[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (sessionId) {
            loadFiles("");
        }
    }, [sessionId]);

    const loadFiles = async (path: string) => {
        setLoading(true);
        try {
            const files = await listFiles(sessionId, path);
            setRootFiles(files);
            setError(null);
        } catch (err) {
            console.error(err);
            setError("Failed to load files");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex flex-col h-full bg-[#1e1e1e]">
            <div className="px-4 py-3 border-b border-[#333] flex items-center justify-between">
                <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Workspace Files</h3>
                <button
                    onClick={() => loadFiles("")}
                    className="text-gray-500 hover:text-white transition-colors"
                    title="Refresh"
                >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                </button>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-2">
                {loading && rootFiles.length === 0 ? (
                    <div className="flex items-center justify-center py-8">
                        <div className="w-4 h-4 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin"></div>
                    </div>
                ) : error ? (
                    <div className="p-4 text-xs text-center text-red-500 bg-red-500/10 rounded-lg mx-2 my-2 border border-red-500/20">
                        {error}
                    </div>
                ) : rootFiles.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-12 text-gray-600 gap-2">
                        <svg className="w-8 h-8 opacity-20" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
                        </svg>
                        <span className="text-xs">No files found</span>
                    </div>
                ) : (
                    <div className="space-y-0.5">
                        {rootFiles.map(node => (
                            <FileTreeItem key={node.path} node={node} sessionId={sessionId} />
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

function FileTreeItem({ node, sessionId, level = 0 }: { node: FileNode, sessionId: string, level?: number }) {
    const [expanded, setExpanded] = useState(false);
    const [children, setChildren] = useState<FileNode[]>(node.children || []);
    const [loading, setLoading] = useState(false);
    const [hasLoaded, setHasLoaded] = useState(false);

    const isDir = node.type === "directory";

    const toggleExpand = async (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!isDir) return;

        if (!expanded && !hasLoaded && children.length === 0) {
            setLoading(true);
            try {
                const loaded = await listFiles(sessionId, node.path);
                setChildren(loaded);
                setHasLoaded(true);
            } catch (err) {
                console.error("Failed to load children", err);
            } finally {
                setLoading(false);
            }
        }
        setExpanded(!expanded);
    };

    return (
        <div>
            <div
                className={`
          group flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-[#2a2a2a] cursor-pointer text-xs transition-colors select-none
          ${expanded ? "text-gray-200" : "text-gray-400"}
        `}
                style={{ paddingLeft: `${level * 12 + 8}px` }}
                onClick={toggleExpand}
            >
                <span className={`w-4 h-4 flex items-center justify-center transition-transform ${expanded ? "rotate-90" : ""} ${!isDir && "opacity-0"}`}>
                    <svg className="w-3 h-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                </span>

                {isDir ? (
                    <svg className="w-4 h-4 text-blue-500/80" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                    </svg>
                ) : (
                    <svg className="w-4 h-4 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                    </svg>
                )}

                <span className="truncate flex-1">{node.name}</span>

                {loading && (
                    <div className="w-3 h-3 border-2 border-gray-600 border-t-gray-400 rounded-full animate-spin ml-2"></div>
                )}
            </div>

            {expanded && (
                <div className="border-l border-[#333] ml-[15px]">
                    {children.length === 0 && !loading && hasLoaded ? (
                        <div className="pl-6 py-1 text-[10px] text-gray-600 italic">Empty</div>
                    ) : (
                        children.map(child => (
                            <FileTreeItem key={child.path} node={child} sessionId={sessionId} level={level + 1} />
                        ))
                    )}
                </div>
            )}
        </div>
    );
}

"use client";

import { useState, useEffect } from "react";
import { PathInput } from "./PathInput";

interface CreateSessionDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onCreate: (config: CreateSessionConfig) => Promise<void>;
}

export interface CreateSessionConfig {
    name?: string;
    workspace_path?: string;
    agent_mode: string;
    llm_config: {
        provider: string;
        model_id: string;
        temperature: string;
        thinking: string;
    };
}

const DEFAULT_MODELS = {
    google: [
        "gemini-3-flash-preview",
        "gemini-3-pro-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash-exp",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
    anthropic: [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-haiku-4",
    ],
    openai: ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    "openai-codex": ["gpt-5.3-codex", "gpt-4o", "o3-mini"],
    deepseek: ["deepseek-chat", "deepseek-coder"],
    ollama: ["llama3", "mistral", "qwen2"],
};

export function CreateSessionDialog({ isOpen, onClose, onCreate }: CreateSessionDialogProps) {
    const [name, setName] = useState("");
    const [workspacePath, setWorkspacePath] = useState("");
    // Default to dual_agent (Core Orchestrator) for unified architecture
    const [agentMode, setAgentMode] = useState("dual_agent"); 
    const [loading, setLoading] = useState(false);

    // LLM Config
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [provider, setProvider] = useState("google");
    const [model, setModel] = useState("gemini-3-flash-preview");
    const [temperature, setTemperature] = useState(0.7);
    const [thinking, setThinking] = useState(false);

    // Reset form when opening
    useEffect(() => {
        if (isOpen) {
            setName("");
            setWorkspacePath("");
            setLoading(false);
            // Load last used config from localStorage if available
            const saved = localStorage.getItem("nimbus_last_config");
            if (saved) {
                try {
                    const config = JSON.parse(saved);
                    setProvider(config.provider || "google");
                    setModel(config.model || "gemini-3-flash-preview");
                    setTemperature(config.temperature ?? 0.7);
                    setThinking(config.thinking ?? false);
                    setAgentMode(config.agent_mode || "standard");
                } catch (e) {
                    console.warn("Failed to load saved config", e);
                }
            }
        }
    }, [isOpen]);

    const handleSubmit = async () => {
        setLoading(true);
        try {
            const config: CreateSessionConfig = {
                name: name.trim() || undefined,
                workspace_path: workspacePath.trim() || undefined,
                agent_mode: agentMode,
                llm_config: {
                    provider,
                    model_id: model,
                    temperature: temperature.toString(),
                    thinking: thinking ? "true" : "false",
                },
            };

            // Save to localStorage for next time
            localStorage.setItem("nimbus_last_config", JSON.stringify({
                provider,
                model,
                temperature,
                thinking,
                agent_mode: agentMode
            }));

            await onCreate(config);
            onClose();
        } catch (err) {
            console.error("Failed to create session:", err);
        } finally {
            setLoading(false);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center backdrop-blur-sm" onClick={onClose}>
            <div
                className="bg-[#1c1c1c] border border-gray-700 rounded-xl w-[500px] shadow-2xl flex flex-col max-h-[90vh] overflow-hidden"
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div className="px-5 py-4 border-b border-gray-700 flex justify-between items-center bg-[#181818]">
                    <h2 className="text-lg font-medium text-gray-100">新建会话</h2>
                    <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors text-xl leading-none">
                        ×
                    </button>
                </div>

                {/* Content */}
                <div className="p-5 overflow-y-auto space-y-5">
                    {/* Basic Info */}
                    <div className="space-y-4">
                        <div>
                            <label className="block text-xs font-medium text-gray-400 mb-1.5">会话名称</label>
                            <input
                                type="text"
                                value={name}
                                onChange={e => setName(e.target.value)}
                                placeholder="可选，留空则根据首条消息自动生成"
                                className="w-full bg-[#2a2a2a] border border-gray-600 rounded-lg px-3 py-2.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all"
                                autoFocus
                                onKeyDown={e => e.key === "Enter" && handleSubmit()}
                            />
                        </div>

                        <div>
                            <label className="block text-xs font-medium text-gray-400 mb-1.5">工作目录 (可选)</label>
                            <PathInput
                                value={workspacePath}
                                onChange={setWorkspacePath}
                                placeholder="默认为服务器当前目录..."
                                className="w-full bg-[#2a2a2a] border border-gray-600 rounded-lg px-3 py-2.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all"
                            />
                        </div>
                    </div>

                    {/* Advanced Settings Toggle */}
                    <div>
                        <button
                            type="button"
                            onClick={() => setShowAdvanced(!showAdvanced)}
                            className="flex items-center gap-2 text-xs text-gray-400 hover:text-gray-200 transition-colors"
                        >
                            <span className={`transform transition-transform ${showAdvanced ? "rotate-90" : ""}`}>▶</span>
                            高级设置 (模型配置 & 参数)
                        </button>

                        {showAdvanced && (
                            <div className="mt-3 p-4 bg-[#252525] rounded-lg border border-gray-700 space-y-4 animate-in slide-in-from-top-2 fade-in duration-200">
                                {/* Provider & Model */}
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="block text-[10px] uppercase font-bold text-gray-500 mb-1.5">Provider</label>
                                        <select
                                            value={provider}
                                            onChange={e => {
                                                setProvider(e.target.value);
                                                // Reset model to first in list
                                                const list = DEFAULT_MODELS[e.target.value as keyof typeof DEFAULT_MODELS];
                                                if (list) setModel(list[0]);
                                            }}
                                            className="w-full bg-[#1c1c1c] border border-gray-600 rounded px-2 py-2 text-xs text-gray-200 focus:border-blue-500 focus:outline-none"
                                        >
                                            {["google", "anthropic", "openai", "openai-codex", "deepseek", "ollama", "custom"].map(p => (
                                                <option key={p} value={p}>
                                                    {p === "custom" ? "Custom" : p.charAt(0).toUpperCase() + p.slice(1)}
                                                </option>
                                            ))}
                                        </select>
                                    </div>
                                    <div>
                                        <label className="block text-[10px] uppercase font-bold text-gray-500 mb-1.5">Model</label>
                                        {provider === "custom" ? (
                                            <input
                                                type="text"
                                                value={model}
                                                onChange={e => setModel(e.target.value)}
                                                className="w-full bg-[#1c1c1c] border border-gray-600 rounded px-2 py-2 text-xs text-gray-200 focus:border-blue-500 focus:outline-none"
                                                placeholder="Model ID..."
                                            />
                                        ) : (
                                            <select
                                                value={model}
                                                onChange={e => setModel(e.target.value)}
                                                className="w-full bg-[#1c1c1c] border border-gray-600 rounded px-2 py-2 text-xs text-gray-200 focus:border-blue-500 focus:outline-none"
                                            >
                                                {/* Always include current model just in case */}
                                                {!DEFAULT_MODELS[provider as keyof typeof DEFAULT_MODELS]?.includes(model) && (
                                                    <option value={model}>{model}</option>
                                                )}
                                                {DEFAULT_MODELS[provider as keyof typeof DEFAULT_MODELS]?.map(m => (
                                                    <option key={m} value={m}>{m}</option>
                                                ))}
                                            </select>
                                        )}
                                    </div>
                                </div>

                                {/* Temperature */}
                                <div>
                                    <div className="flex justify-between items-center mb-1.5">
                                        <label className="text-[10px] uppercase font-bold text-gray-500">Temperature</label>
                                        <span className="text-[10px] font-mono text-gray-400">{temperature.toFixed(1)}</span>
                                    </div>
                                    <input
                                        type="range"
                                        min="0"
                                        max="1"
                                        step="0.1"
                                        value={temperature}
                                        onChange={e => setTemperature(parseFloat(e.target.value))}
                                        className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                    />
                                    <div className="flex justify-between mt-1 text-[10px] text-gray-600">
                                        <span>精确 (0.0)</span>
                                        <span>创意 (1.0)</span>
                                    </div>
                                </div>

                                {/* Thinking Mode */}
                                <div className="flex items-center justify-between pt-1">
                                    <div>
                                        <div className="text-xs font-medium text-gray-300">Thinking Mode</div>
                                        <div className="text-[10px] text-gray-500">启用深度思考 (Chain of Thought)，适合复杂推理任务</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setThinking(!thinking)}
                                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${thinking ? 'bg-blue-600' : 'bg-gray-600'}`}
                                    >
                                        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${thinking ? 'translate-x-4.5' : 'translate-x-1'}`} />
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>

                {/* Footer */}
                <div className="px-5 py-4 bg-[#181818] border-t border-gray-700 flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
                    >
                        取消
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={loading}
                        className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-500/20"
                    >
                        {loading ? "创建中..." : "创建会话"}
                    </button>
                </div>
            </div>
        </div>
    );
}

/**
 * Pi Bridge for Nimbus
 *
 * 作为 Node.js 子进程运行，暴露 pi-ai 给 Python 主进程
 * 通过 stdin/stdout JSON-RPC 通信
 *
 * 启动方式：
 *   npm run dev          # 开发模式 (tsx)
 *   npm run start        # 生产模式 (编译后)
 */

import * as readline from "readline";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import {
	streamSimple,
	completeSimple,
	getModel,
	getModels,
	getEnvApiKey,
	getOAuthApiKey,
	type Model,
	type Api,
	type Context,
	type SimpleStreamOptions,
	type UserMessage,
	type AssistantMessage,
	type ToolResultMessage,
	type Message,
	type KnownProvider,
	type AssistantMessageEvent,
	type OAuthCredentials,
} from "@mariozechner/pi-ai";

// ============================================================================
// JSON-RPC 协议
// ============================================================================

interface JsonRpcRequest {
	jsonrpc: "2.0";
	id: number | string;
	method: string;
	params?: unknown;
}

interface JsonRpcResponse {
	jsonrpc: "2.0";
	id: number | string;
	result?: unknown;
	error?: { code: number; message: string; data?: unknown };
}

interface JsonRpcNotification {
	jsonrpc: "2.0";
	method: string;
	params?: unknown;
}

// ============================================================================
// Types for RPC
// ============================================================================

interface RpcContentBlock {
	type: "text" | "image" | "toolCall" | "toolResult" | "thinking";
	text?: string;
	id?: string;
	name?: string;
	arguments?: Record<string, unknown> | string;
	toolCallId?: string;
	mimeType?: string;
	data?: string;
}

interface RpcMessage {
	role: "user" | "assistant" | "system" | "toolResult";
	content: string | RpcContentBlock[];
	toolCallId?: string;
	toolName?: string;
	isError?: boolean;
}

interface AiStreamParams {
	provider?: string;
	modelId?: string;
	messages: RpcMessage[];
	systemPrompt?: string;
	options?: {
		maxTokens?: number;
		apiKey?: string;
	};
}

// ============================================================================
// Bridge 核心
// ============================================================================

class PiBridge {
	private rl: readline.Interface;
	private currentModel: Model<Api> | null = null;

	constructor() {
		this.rl = readline.createInterface({
			input: process.stdin,
			output: process.stdout,
			terminal: false,
		});

		this.rl.on("line", (line) => this.handleLine(line));
		this.log("Pi Bridge started");
	}

	private log(message: string) {
		process.stderr.write(`[pi-bridge] ${message}\n`);
	}

	private async handleLine(line: string) {
		try {
			const request = JSON.parse(line) as JsonRpcRequest;
			const result = await this.handleRequest(request);
			this.sendResponse({ jsonrpc: "2.0", id: request.id, result });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			this.log(`Error: ${message}`);
			this.sendResponse({
				jsonrpc: "2.0",
				id: 0,
				error: { code: -32700, message: `Error: ${message}` },
			});
		}
	}

	private async handleRequest(request: JsonRpcRequest): Promise<unknown> {
		const { method, params } = request;

		switch (method) {
			case "ai.stream":
				return this.aiStream(params as AiStreamParams);

			case "ai.complete":
				return this.aiComplete(params as AiStreamParams);

			case "ai.getModels":
				return this.aiGetModels(params as { provider?: string } | undefined);

			case "ai.setModel":
				return this.aiSetModel(params as { provider: string; modelId: string });

			case "tui.render":
				return this.tuiRender(params as { type: string; content: string });

			case "tui.notify":
				return this.tuiNotify(params as { message: string; type: string });

			case "auth.status":
				return this.authStatus();

			case "ping":
				return { pong: true, timestamp: Date.now() };

			case "shutdown":
				this.log("Shutting down...");
				process.exit(0);

			default:
				throw new Error(`Unknown method: ${method}`);
		}
	}

	private sendResponse(response: JsonRpcResponse) {
		process.stdout.write(JSON.stringify(response) + "\n");
	}

	private sendNotification(method: string, params?: unknown) {
		const notification: JsonRpcNotification = { jsonrpc: "2.0", method, params };
		process.stdout.write(JSON.stringify(notification) + "\n");
	}

	// ========================================================================
	// AI Implementation
	// ========================================================================

	private aiSetModel(params: { provider: string; modelId: string }): { success: boolean } {
		const { provider, modelId } = params;

		// 验证 provider
		if (!this.isKnownProvider(provider)) {
			throw new Error(`Unknown provider: ${provider}`);
		}

		const model = getModel(provider, modelId as never);
		if (!model) {
			throw new Error(`Model not found: ${provider}/${modelId}`);
		}

		this.currentModel = model;
		this.log(`Model set to: ${provider}/${modelId}`);
		return { success: true };
	}

	private isKnownProvider(provider: string): provider is KnownProvider {
		const known = ["anthropic", "openai", "google", "xai", "deepseek", "mistral", "openrouter", "azure"];
		return known.includes(provider);
	}

	// ========================================================================
	// Auth: 支持从 Pi 的 auth.json 读取 OAuth tokens
	// ========================================================================

	private authStatus(): {
		authPath: string;
		exists: boolean;
		providers: Array<{ provider: string; type: string; valid: boolean }>;
	} {
		const authPath = this.getPiAuthPath();
		const exists = fs.existsSync(authPath);
		const providers: Array<{ provider: string; type: string; valid: boolean }> = [];

		if (exists) {
			const auth = this.loadPiAuth();
			for (const [provider, cred] of Object.entries(auth)) {
				if (cred.type === "api_key") {
					providers.push({ provider, type: "api_key", valid: !!cred.key });
				} else if (cred.type === "oauth") {
					const expires = (cred as any).expires || 0;
					const valid = Date.now() < expires;
					providers.push({ provider, type: "oauth", valid });
				}
			}
		}

		// 也检查环境变量
		const envProviders = ["anthropic", "openai", "google", "xai"];
		for (const provider of envProviders) {
			const key = getEnvApiKey(provider);
			if (key && !providers.find((p) => p.provider === provider)) {
				providers.push({ provider, type: "env", valid: true });
			}
		}

		return { authPath, exists, providers };
	}

	private getPiAuthPath(): string {
		// Pi 默认存储路径: ~/.pi/agent/auth.json
		return path.join(os.homedir(), ".pi", "agent", "auth.json");
	}

	private loadPiAuth(): Record<string, { type: string; key?: string } & Partial<OAuthCredentials>> {
		const authPath = this.getPiAuthPath();
		try {
			if (fs.existsSync(authPath)) {
				return JSON.parse(fs.readFileSync(authPath, "utf-8"));
			}
		} catch {
			// 忽略错误
		}
		return {};
	}

	private async getApiKeyForProvider(provider: string, explicitKey?: string): Promise<string> {
		// 1. 优先使用显式提供的 key
		if (explicitKey) {
			return explicitKey;
		}

		// 2. 尝试环境变量
		const envKey = getEnvApiKey(provider);
		if (envKey) {
			return envKey;
		}

		// 3. 尝试从 Pi 的 auth.json 读取
		const auth = this.loadPiAuth();
		const cred = auth[provider];

		if (cred) {
			if (cred.type === "api_key" && cred.key) {
				this.log(`Using API key from Pi auth.json for ${provider}`);
				return cred.key;
			}

			if (cred.type === "oauth") {
				// OAuthCredentials 使用 access/refresh/expires 字段
				const oauthCreds: OAuthCredentials = {
					access: (cred as any).access || "",
					refresh: (cred as any).refresh || "",
					expires: (cred as any).expires || 0,
				};

				if (oauthCreds.access) {
					this.log(`Using OAuth token from Pi auth.json for ${provider}`);
					// 检查是否需要刷新
					if (Date.now() < oauthCreds.expires) {
						return oauthCreds.access;
					}

					// Token 过期，尝试刷新
					try {
						const result = await getOAuthApiKey(provider as any, { [provider]: oauthCreds });
						if (result) {
							// 更新 auth.json
							auth[provider] = { type: "oauth", ...result.newCredentials } as any;
							fs.writeFileSync(this.getPiAuthPath(), JSON.stringify(auth, null, 2));
							this.log(`Refreshed OAuth token for ${provider}`);
							return result.apiKey;
						}
					} catch (e) {
						this.log(`Failed to refresh OAuth token: ${e}`);
					}
				}
			}
		}

		throw new Error(
			`No API key found for ${provider}. Options:\n` +
			`  1. Run 'pi' and use /login to authenticate\n` +
			`  2. Set ${provider.toUpperCase()}_API_KEY environment variable`
		);
	}

	private async aiStream(params: AiStreamParams): Promise<{ success: boolean }> {
		const { provider, modelId, messages, systemPrompt, options } = params;

		// 获取模型
		let model = this.currentModel;
		if (provider && modelId && this.isKnownProvider(provider)) {
			model = getModel(provider, modelId as never);
		}

		if (!model) {
			throw new Error("No model set. Call ai.setModel first or provide provider/modelId");
		}

		// 转换消息格式
		const context = this.convertToContext(messages, systemPrompt);

		// 获取 API key (支持 OAuth)
		const apiKey = await this.getApiKeyForProvider(model.provider, options?.apiKey);

		// 构建 stream options
		const streamOptions: SimpleStreamOptions = {
			apiKey,
			maxTokens: options?.maxTokens ?? 8192,
		};

		this.log(`Streaming with model: ${model.provider}/${model.id}`);
		this.sendNotification("ai.streamEvent", { type: "start" });

		try {
			// 使用 pi-ai 的 streamSimple
			for await (const event of streamSimple(model, context, streamOptions)) {
				this.handleStreamEvent(event);
			}
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			this.sendNotification("ai.streamEvent", { type: "error", error: message });
			this.sendNotification("ai.streamEvent", { type: "stop", reason: "error" });
		}

		return { success: true };
	}

	private handleStreamEvent(event: AssistantMessageEvent) {
		switch (event.type) {
			case "text_delta":
				this.sendNotification("ai.streamEvent", {
					type: "text",
					text: event.delta,
				});
				break;

			case "thinking_delta":
				this.sendNotification("ai.streamEvent", {
					type: "thinking",
					text: event.delta,
				});
				break;

			case "toolcall_end":
				this.sendNotification("ai.streamEvent", {
					type: "tool_call",
					toolCall: {
						id: event.toolCall.id,
						name: event.toolCall.name,
						arguments: event.toolCall.arguments,
					},
				});
				break;

			case "done":
				this.sendNotification("ai.streamEvent", {
					type: "usage",
					usage: {
						inputTokens: event.message.usage.input,
						outputTokens: event.message.usage.output,
					},
				});
				this.sendNotification("ai.streamEvent", {
					type: "stop",
					reason: event.reason,
				});
				break;

			case "error":
				this.sendNotification("ai.streamEvent", {
					type: "error",
					error: event.error.errorMessage || "Unknown error",
				});
				this.sendNotification("ai.streamEvent", {
					type: "stop",
					reason: event.reason,
				});
				break;
		}
	}

	private async aiComplete(params: AiStreamParams): Promise<{
		content: RpcContentBlock[];
		usage: { inputTokens: number; outputTokens: number };
	}> {
		const { provider, modelId, messages, systemPrompt, options } = params;

		let model = this.currentModel;
		if (provider && modelId && this.isKnownProvider(provider)) {
			model = getModel(provider, modelId as never);
		}

		if (!model) {
			throw new Error("No model set");
		}

		const context = this.convertToContext(messages, systemPrompt);
		const apiKey = await this.getApiKeyForProvider(model.provider, options?.apiKey);

		const result = await completeSimple(model, context, {
			apiKey,
			maxTokens: options?.maxTokens ?? 8192,
		});

		// 转换 content
		const contentBlocks: RpcContentBlock[] = result.content.map((c) => {
			if (c.type === "text") {
				return { type: "text" as const, text: c.text };
			} else if (c.type === "thinking") {
				return { type: "thinking" as const, text: c.thinking };
			} else {
				// toolCall
				return {
					type: "toolCall" as const,
					id: c.id,
					name: c.name,
					arguments: c.arguments,
				};
			}
		});

		return {
			content: contentBlocks,
			usage: {
				inputTokens: result.usage.input,
				outputTokens: result.usage.output,
			},
		};
	}

	private aiGetModels(params?: { provider?: string }): Array<{ provider: string; id: string; name: string }> {
		if (params?.provider && this.isKnownProvider(params.provider)) {
			const models = getModels(params.provider);
			return models.map((m) => ({
				provider: m.provider,
				id: m.id,
				name: m.name,
			}));
		}

		// 返回所有已知 provider 的模型
		const allModels: Array<{ provider: string; id: string; name: string }> = [];
		const providers: KnownProvider[] = ["anthropic", "openai", "google", "xai"];

		for (const provider of providers) {
			try {
				const models = getModels(provider);
				for (const m of models) {
					allModels.push({
						provider: m.provider,
						id: m.id,
						name: m.name,
					});
				}
			} catch {
				// 忽略不支持的 provider
			}
		}

		return allModels;
	}

	// ========================================================================
	// Context Conversion
	// ========================================================================

	private convertToContext(rpcMessages: RpcMessage[], systemPrompt?: string): Context {
		const messages: Message[] = [];

		for (const msg of rpcMessages) {
			if (msg.role === "system") {
				// System messages 放到 systemPrompt
				continue;
			}

			if (msg.role === "user") {
				const userMsg: UserMessage = {
					role: "user",
					content: this.convertUserContent(msg.content),
					timestamp: Date.now(),
				};
				messages.push(userMsg);
			} else if (msg.role === "assistant") {
				// Assistant 消息需要完整的结构，这里简化处理
				const content = typeof msg.content === "string" ? msg.content : this.extractText(msg.content);
				const userMsg: UserMessage = {
					role: "user",
					content: `[Previous assistant response]\n${content}`,
					timestamp: Date.now(),
				};
				messages.push(userMsg);
			} else if (msg.role === "toolResult") {
				const toolResultMsg: ToolResultMessage = {
					role: "toolResult",
					toolCallId: msg.toolCallId || "",
					toolName: msg.toolName || "unknown",
					content: this.convertUserContent(msg.content),
					isError: msg.isError ?? false,
					timestamp: Date.now(),
				};
				messages.push(toolResultMsg);
			}
		}

		// 提取 system prompt
		const sysPrompt =
			systemPrompt ||
			rpcMessages
				.filter((m) => m.role === "system")
				.map((m) => (typeof m.content === "string" ? m.content : this.extractText(m.content)))
				.join("\n");

		return {
			systemPrompt: sysPrompt || undefined,
			messages,
		};
	}

	private convertUserContent(
		content: string | RpcContentBlock[],
	): Array<{ type: "text"; text: string } | { type: "image"; mimeType: string; data: string }> {
		if (typeof content === "string") {
			return [{ type: "text", text: content }];
		}

		const result: Array<{ type: "text"; text: string } | { type: "image"; mimeType: string; data: string }> = [];
		for (const c of content) {
			if (c.type === "text") {
				result.push({ type: "text", text: c.text || "" });
			} else if (c.type === "image") {
				result.push({
					type: "image",
					mimeType: c.mimeType || "image/png",
					data: c.data || "",
				});
			}
		}
		return result;
	}

	private extractText(content: RpcContentBlock[]): string {
		return content
			.filter((c) => c.type === "text")
			.map((c) => c.text || "")
			.join("\n");
	}

	// ========================================================================
	// TUI Implementation (简化版)
	// ========================================================================

	private tuiRender(params: { type: string; content: string }): { success: boolean } {
		const { type, content } = params;

		if (type === "streaming") {
			process.stderr.write(content);
		} else {
			process.stderr.write(content + "\n");
		}

		return { success: true };
	}

	private tuiNotify(params: { message: string; type: string }): { success: boolean } {
		const { message, type } = params;
		const prefix = type === "error" ? "❌" : type === "warning" ? "⚠️" : "ℹ️";
		process.stderr.write(`${prefix} ${message}\n`);
		return { success: true };
	}
}

// ============================================================================
// Main
// ============================================================================

new PiBridge();

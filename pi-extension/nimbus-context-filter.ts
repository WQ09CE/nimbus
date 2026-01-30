/**
 * Nimbus Context Filter Extension for Pi
 *
 * 简化版：只在 Pi 发送消息给 LLM 前，用 Nimbus 过滤上下文
 * - Pi 负责 TUI 和 agent 循环
 * - Nimbus 负责 Context Stack 过滤（移除失败的 tool calls）
 *
 * Usage:
 *   pi -e /path/to/nimbus/pi-extension/nimbus-context-filter.ts
 */

import { spawn, type ChildProcess } from "child_process";
import * as path from "path";
import * as readline from "readline";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

export default function nimbusContextFilter(pi: ExtensionAPI) {
	let nimbusProcess: ChildProcess | null = null;
	let nimbusRL: readline.Interface | null = null;
	let isReady = false;

	const extensionDir = __dirname;
	const nimbusDir = path.dirname(extensionDir);
	const serverScript = path.join(extensionDir, "nimbus_filter_server.py");

	function log(msg: string) {
		console.error(`[nimbus-filter] ${msg}`);
	}

	// 启动 Nimbus
	async function ensureNimbus(): Promise<boolean> {
		if (nimbusProcess && isReady) return true;

		log("Starting Nimbus filter...");

		try {
			nimbusProcess = spawn("python", [serverScript], {
				cwd: nimbusDir,
				stdio: ["pipe", "pipe", "pipe"],
				env: { ...process.env, PYTHONUNBUFFERED: "1" },
			});

			nimbusProcess.stderr?.on("data", (data) => {
				log(data.toString().trim());
			});

			nimbusProcess.on("exit", () => {
				isReady = false;
				nimbusProcess = null;
			});

			nimbusRL = readline.createInterface({ input: nimbusProcess.stdout! });

			// 等待 ready
			await new Promise<void>((resolve, reject) => {
				const timeout = setTimeout(() => reject(new Error("Timeout")), 10000);
				const onLine = (line: string) => {
					try {
						const data = JSON.parse(line);
						if (data.type === "ready") {
							clearTimeout(timeout);
							isReady = true;
							resolve();
						}
					} catch {}
				};
				nimbusRL!.on("line", onLine);
			});

			log("Nimbus filter ready");
			return true;
		} catch (err) {
			log(`Failed to start: ${err}`);
			return false;
		}
	}

	// 同步添加消息到 Nimbus（用于追踪）
	function trackMessage(role: string, content: string, toolCallId?: string, isError?: boolean) {
		if (!nimbusProcess || !isReady) return;
		nimbusProcess.stdin!.write(
			JSON.stringify({
				type: "track_message",
				role,
				content,
				toolCallId,
				isError,
			}) + "\n",
		);
	}

	// 获取过滤后的消息 IDs
	async function getDiscardableIds(): Promise<Set<string>> {
		if (!nimbusProcess || !isReady) return new Set();

		nimbusProcess.stdin!.write(JSON.stringify({ type: "get_discardable" }) + "\n");

		return new Promise((resolve) => {
			const onLine = (line: string) => {
				try {
					const data = JSON.parse(line);
					if (data.type === "discardable_ids") {
						nimbusRL!.off("line", onLine);
						resolve(new Set(data.ids || []));
					}
				} catch {}
			};
			nimbusRL!.on("line", onLine);

			// 超时
			setTimeout(() => {
				nimbusRL!.off("line", onLine);
				resolve(new Set());
			}, 1000);
		});
	}

	// 追踪 tool results
	pi.on("tool_result", async (event, _ctx) => {
		await ensureNimbus();
		trackMessage("tool", String(event.content?.[0]?.type === "text" ? event.content[0].text : ""), event.toolCallId, event.isError);
	});

	// 在发送给 LLM 前过滤消息
	pi.on("context", async (event, ctx) => {
		if (!(await ensureNimbus())) {
			return; // Nimbus 不可用，不过滤
		}

		const discardableIds = await getDiscardableIds();
		if (discardableIds.size === 0) {
			return; // 没有要过滤的
		}

		log(`Filtering ${discardableIds.size} messages`);

		// 过滤消息
		const filtered = event.messages.filter((msg) => {
			if (msg.role === "toolResult") {
				const toolMsg = msg as { toolCallId?: string };
				if (toolMsg.toolCallId && discardableIds.has(toolMsg.toolCallId)) {
					return false;
				}
			}
			return true;
		});

		ctx.ui.setStatus("nimbus", `filtered: ${event.messages.length - filtered.length}`);

		return { messages: filtered };
	});

	// /gc 命令
	pi.registerCommand("gc", {
		description: "Show Nimbus Context Stack status",
		handler: async (_args, ctx) => {
			if (!isReady) {
				ctx.ui.notify("Nimbus not running", "warning");
				return;
			}

			const ids = await getDiscardableIds();
			ctx.ui.notify(`Context Stack: ${ids.size} messages will be filtered`, "info");
		},
	});

	// 启动时初始化
	pi.on("session_start", async (_event, ctx) => {
		ctx.ui.notify("Nimbus Context Filter loaded", "info");
	});

	// 清理
	pi.on("shutdown", async () => {
		if (nimbusProcess) {
			nimbusProcess.kill();
		}
	});
}

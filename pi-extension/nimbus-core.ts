/**
 * Nimbus Core Extension for Pi
 *
 * 在 Pi TUI 中使用 Nimbus Python 作为 agent 核心
 * - Pi 提供 TUI 界面
 * - Nimbus 提供 Context Stack、MMU 等核心能力
 * - Nimbus 调用 pi-ai 获取 LLM 响应
 *
 * Usage:
 *   pi -e /path/to/nimbus/pi-extension/nimbus-core.ts
 *
 * Commands:
 *   /gc - Show Context Stack status
 *   /nimbus restart - Restart Nimbus core
 */

import { spawn, type ChildProcess } from "child_process";
import * as path from "path";
import * as readline from "readline";
import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";

interface NimbusEvent {
	type: "ready" | "text" | "tool_call" | "usage" | "done" | "error" | "gc_status";
	text?: string;
	toolCall?: { name: string; arguments: Record<string, unknown> };
	usage?: { inputTokens: number; outputTokens: number };
	error?: string;
	gcStatus?: { total: number; discardable: number };
}

export default function nimbusExtension(pi: ExtensionAPI) {
	let nimbusProcess: ChildProcess | null = null;
	let nimbusRL: readline.Interface | null = null;
	let isReady = false;
	let pendingResolve: ((value: void) => void) | null = null;

	// 找到 nimbus 目录
	const extensionDir = __dirname;
	const nimbusDir = path.dirname(extensionDir);
	const serverScript = path.join(extensionDir, "nimbus_server.py");

	// 静默模式：不输出日志到 stderr
	const quiet = process.env.NIMBUS_QUIET === "1" || process.env.NIMBUS_QUIET === "true";

	function log(msg: string) {
		if (!quiet) {
			console.error(`[nimbus-ext] ${msg}`);
		}
	}

	// 启动 Nimbus 进程
	async function startNimbus(): Promise<void> {
		if (nimbusProcess && isReady) return;

		log(`Starting Nimbus from ${serverScript}`);

		nimbusProcess = spawn("python", [serverScript], {
			cwd: nimbusDir,
			stdio: ["pipe", "pipe", "pipe"],
			env: { ...process.env, PYTHONUNBUFFERED: "1" },
		});

		// 日志（静默模式下不转发）
		nimbusProcess.stderr?.on("data", (data) => {
			if (!quiet) {
				log(data.toString().trim());
			}
		});

		nimbusProcess.on("exit", (code) => {
			log(`Nimbus exited with code ${code}`);
			isReady = false;
			nimbusProcess = null;
		});

		// 创建 readline
		nimbusRL = readline.createInterface({
			input: nimbusProcess.stdout!,
			crlfDelay: Infinity,
		});

		// 等待 ready
		return new Promise((resolve, reject) => {
			const timeout = setTimeout(() => {
				reject(new Error("Nimbus startup timeout"));
			}, 15000);

			const onLine = (line: string) => {
				try {
					const event = JSON.parse(line) as NimbusEvent;
					if (event.type === "ready") {
						clearTimeout(timeout);
						isReady = true;
						log("Nimbus ready");
						resolve();
					}
				} catch {}
			};

			nimbusRL!.on("line", onLine);

			nimbusProcess!.on("error", (err) => {
				clearTimeout(timeout);
				reject(err);
			});
		});
	}

	// 发送消息
	function send(msg: object) {
		if (!nimbusProcess || !isReady) {
			throw new Error("Nimbus not ready");
		}
		nimbusProcess.stdin!.write(JSON.stringify(msg) + "\n");
	}

	// 处理用户输入
	async function handleUserInput(prompt: string, ctx: ExtensionContext): Promise<void> {
		if (!isReady) {
			ctx.ui.notify("Starting Nimbus...", "info");
			try {
				await startNimbus();
			} catch (err) {
				ctx.ui.notify(`Failed to start Nimbus: ${err}`, "error");
				return;
			}
		}

		// 发送消息
		send({ type: "user_message", content: prompt });

		// 收集响应并显示
		let fullText = "";
		ctx.ui.setWorkingMessage("Nimbus thinking...");

		return new Promise((resolve) => {
			const updateWidget = () => {
				// 使用 widget 显示流式输出
				ctx.ui.setWidget("nimbus-response", fullText ? [`Assistant: ${fullText}`] : undefined);
			};

			const onLine = (line: string) => {
				try {
					const event = JSON.parse(line) as NimbusEvent;

					switch (event.type) {
						case "text":
							if (event.text) {
								fullText += event.text;
								updateWidget();
							}
							break;

						case "usage":
							if (event.usage) {
								ctx.ui.setStatus("nimbus", `tokens: ${event.usage.inputTokens}→${event.usage.outputTokens}`);
							}
							break;

						case "error":
							ctx.ui.notify(`Error: ${event.error}`, "error");
							ctx.ui.setWorkingMessage();
							ctx.ui.setWidget("nimbus-response", undefined);
							nimbusRL!.off("line", onLine);
							resolve();
							break;

						case "done":
							ctx.ui.setWorkingMessage();
							// 保留最终响应显示一会儿
							setTimeout(() => {
								ctx.ui.setWidget("nimbus-response", undefined);
							}, 100);
							nimbusRL!.off("line", onLine);
							resolve();
							break;
					}
				} catch {}
			};

			nimbusRL!.on("line", onLine);
		});
	}

	// 拦截输入事件
	pi.on("input", async (event, ctx) => {
		const text = event.text.trim();

		// 跳过命令
		if (text.startsWith("/")) {
			return { action: "continue" as const };
		}

		// 跳过空输入
		if (!text) {
			return { action: "continue" as const };
		}

		// 处理用户输入
		await handleUserInput(text, ctx);

		// 阻止默认处理
		return { action: "handled" as const };
	});

	// /gc 命令
	pi.registerCommand("gc", {
		description: "Show Nimbus Context Stack status",
		handler: async (_args, ctx) => {
			if (!isReady) {
				ctx.ui.notify("Nimbus not running. Send a message first.", "warning");
				return;
			}

			send({ type: "gc_status" });

			return new Promise<void>((resolve) => {
				const onLine = (line: string) => {
					try {
						const event = JSON.parse(line) as NimbusEvent;
						if (event.type === "gc_status" && event.gcStatus) {
							ctx.ui.notify(
								`Context Stack: ${event.gcStatus.total} messages, ${event.gcStatus.discardable} will be filtered`,
								"info",
							);
							nimbusRL!.off("line", onLine);
							resolve();
						}
					} catch {}
				};
				nimbusRL!.on("line", onLine);
			});
		},
	});

	// /nimbus 命令
	pi.registerCommand("nimbus", {
		description: "Nimbus control (restart/status)",
		handler: async (args, ctx) => {
			if (args === "restart") {
				if (nimbusProcess) {
					send({ type: "shutdown" });
					nimbusProcess.kill();
					nimbusProcess = null;
					isReady = false;
				}
				try {
					await startNimbus();
					ctx.ui.notify("Nimbus restarted", "info");
				} catch (err) {
					ctx.ui.notify(`Failed: ${err}`, "error");
				}
			} else if (args === "status") {
				ctx.ui.notify(isReady ? "Nimbus is running ✓" : "Nimbus is not running", "info");
			} else {
				ctx.ui.notify("Usage: /nimbus [restart|status]", "info");
			}
		},
	});

	// 启动时预热
	pi.on("session_start", async (_event, ctx) => {
		ctx.ui.notify("Nimbus Core extension loaded", "info");
		// 预启动 Nimbus
		try {
			await startNimbus();
		} catch (err) {
			log(`Pre-start failed: ${err}`);
		}
	});

	// 清理
	pi.on("shutdown", async () => {
		if (nimbusProcess) {
			try {
				send({ type: "shutdown" });
			} catch {}
			nimbusProcess.kill();
		}
	});
}

import { ToolCall, ToolResult } from '@/lib/api';

export const SPEC_TO_TOOL: Record<string, string> = {
    Explorer: 'Explore', Implementer: 'Implement', Architect: 'Design', Tester: 'Test',
};

export const SPECIALIST_TO_TOOL: Record<string, string> = {
    Explorer: "Explore",
    Implementer: "Implement",
    Architect: "Design",
    Tester: "Test",
};

export const META_TOOLS = new Set(["ParallelDispatch", "SequentialDispatch", "SubAgent"]);
export const META_TOOL_LABELS: Record<string, string> = {
    ParallelDispatch: "Parallel Agents",
    SequentialDispatch: "Sequential Agents",
    SubAgent: "Sub-Agent"
};

/**
 * Demultiplexes sub-tool events into proper specialist calls and results mapping.
 * Used during initial session load.
 */
export function demuxSubToolEvents(
    toolCallId: string,
    toolName: string,
    evts: Array<{ type: string; data: Record<string, any> }>,
    subEventsMap: Map<string, { subCalls: ToolCall[], subResults: ToolResult[] }>,
    toolResultsMap: Map<string, ToolResult>
) {
    if (toolName === 'ParallelDispatch') {
        const slotMap = new Map<number, { specialist: string; subCalls: ToolCall[]; subResults: ToolResult[] }>();
        const actionIdToSlot = new Map<string, number>();
        let autoSlot = 0;

        for (const evt of evts) {
            if (evt.type === 'sub_tool_call' && evt.data) {
                const slotIdx = typeof evt.data.batch_slot_index === 'number'
                    ? evt.data.batch_slot_index
                    : autoSlot++;
                const specialist = String(evt.data.specialist || '');
                const actionId = String(evt.data.action_id || evt.data.id || '');

                if (!slotMap.has(slotIdx)) {
                    slotMap.set(slotIdx, { specialist, subCalls: [], subResults: [] });
                }
                const slot = slotMap.get(slotIdx)!;
                slot.subCalls.push({
                    id: actionId,
                    name: String(evt.data.tool || evt.data.name || 'unknown'),
                    arguments: (evt.data.args || evt.data.arguments || {}) as Record<string, unknown>,
                    agentType: 'dispatch',
                });
                if (actionId) actionIdToSlot.set(actionId, slotIdx);

            } else if (evt.type === 'sub_tool_result' && evt.data) {
                const actionId = String(evt.data.action_id || evt.data.id || '');
                const fault = evt.data.fault as { message: string } | undefined;
                const subRes: ToolResult = {
                    id: actionId,
                    name: String(evt.data.tool || evt.data.name || 'unknown'),
                    result: evt.data.output !== undefined ? evt.data.output : evt.data.result,
                    error: evt.data.status === 'ERROR' ? (fault ? fault.message : 'Error') : undefined,
                    duration: evt.data.duration_ms as number | undefined,
                };

                const slotIdx = actionIdToSlot.get(actionId);
                if (slotIdx !== undefined && slotMap.has(slotIdx)) {
                    slotMap.get(slotIdx)!.subResults.push(subRes);
                }

                if (actionId) {
                    toolResultsMap.set(actionId, subRes);
                }
            }
        }

        if (slotMap.size > 0) {
            const sortedSlots = Array.from(slotMap.entries()).sort((a, b) => a[0] - b[0]);
            const maxSlotIdx = sortedSlots.length > 0 ? sortedSlots[sortedSlots.length - 1][0] : -1;

            const specialistCalls: ToolCall[] = new Array(maxSlotIdx + 1);
            for (const [slotIdx, slot] of Array.from(slotMap.entries())) {
                const sName = SPEC_TO_TOOL[slot.specialist] || slot.specialist || 'Dispatch';
                specialistCalls[slotIdx] = {
                    id: `${toolCallId}-slot-${slotIdx}`,
                    name: sName,
                    arguments: {},
                    agentType: 'dispatch' as const,
                    subCalls: slot.subCalls,
                    subResults: slot.subResults,
                };
            }
            subEventsMap.set(toolCallId, { subCalls: specialistCalls, subResults: [] });
        }

    } else {
        const eventsByParent = new Map<string, { subCalls: ToolCall[], subResults: ToolResult[] }>();

        for (const evt of evts) {
            const pid = String(evt.data?.parent_action_id || toolCallId || '');
            if (!eventsByParent.has(pid)) {
                eventsByParent.set(pid, { subCalls: [], subResults: [] });
            }
            const group = eventsByParent.get(pid)!;

            if (evt.type === 'sub_tool_call' && evt.data) {
                group.subCalls.push({
                    id: String(evt.data.action_id || evt.data.id || ''),
                    name: String(evt.data.tool || evt.data.name || 'unknown'),
                    arguments: (evt.data.args || evt.data.arguments || {}) as Record<string, unknown>,
                    agentType: 'dispatch',
                });
            } else if (evt.type === 'sub_tool_result' && evt.data) {
                const fault = evt.data.fault as { message: string } | undefined;
                group.subResults.push({
                    id: String(evt.data.action_id || evt.data.id || ''),
                    name: String(evt.data.tool || evt.data.name || 'unknown'),
                    result: evt.data.output !== undefined ? evt.data.output : evt.data.result,
                    error: evt.data.status === 'ERROR' ? (fault ? fault.message : 'Error') : undefined,
                    duration: evt.data.duration_ms as number | undefined,
                });
            }
        }

        for (const [pid, group] of Array.from(eventsByParent.entries())) {
            if (group.subCalls.length > 0 || group.subResults.length > 0) {
                subEventsMap.set(pid, group);
            }
        }
    }
}

/**
 * Streaming event router: handles incoming `sub_tool_call`
 * Mutates `toolCalls` in-place. Call `set({ streamingToolCalls: [...toolCalls] })` afterwards.
 */
export function routeSubToolCall(subTool: ToolCall, d: any, toolCalls: ToolCall[], upsertCall: (args: any) => void): void {
    const parentId = d.parent_action_id;
    const slotIdx = d.batch_slot_index;

    if (parentId && slotIdx !== undefined) {
        const metaIdx = toolCalls.findIndex(tc => tc.id === parentId);
        if (metaIdx >= 0) {
            const meta = { ...toolCalls[metaIdx] };
            let specialistSlot = meta.subCalls?.[slotIdx];

            if (!specialistSlot) {
                const autoSpecialist = String(d.specialist || 'Executor');
                const sName = SPECIALIST_TO_TOOL[autoSpecialist] || autoSpecialist;
                specialistSlot = {
                    id: `${parentId}-slot-${slotIdx}`,
                    name: sName,
                    arguments: {},
                    agentType: 'dispatch' as const,
                    subCalls: [],
                    subResults: [],
                };
            }

            specialistSlot = { ...specialistSlot };
            specialistSlot.subCalls = [...(specialistSlot.subCalls || []), subTool];
            if (!meta.subCalls) meta.subCalls = [];
            meta.subCalls = [...meta.subCalls];
            meta.subCalls[slotIdx] = specialistSlot;
            toolCalls[metaIdx] = meta;

            upsertCall({
                callId: subTool.id || "",
                name: subTool.name,
                parentId: specialistSlot.id,
                status: "running",
                args: subTool.arguments,
            });
            return;
        }
    }

    // Legacy routing fallback
    let targetMetaIdx = parentId ? toolCalls.findIndex(tc => tc.id === parentId) : -1;
    if (targetMetaIdx < 0) {
        targetMetaIdx = toolCalls.reduce((last, tc, i) => META_TOOLS.has(tc.name) ? i : last, -1);
    }

    if (targetMetaIdx >= 0) {
        const metaTool = { ...toolCalls[targetMetaIdx] };
        metaTool.subCalls = [...(metaTool.subCalls || []), subTool];
        toolCalls[targetMetaIdx] = metaTool;

        upsertCall({
            callId: subTool.id || "",
            name: subTool.name,
            parentId: toolCalls[targetMetaIdx].id,
            status: "running",
            args: subTool.arguments,
        });
    }
}

/**
 * Streaming event router: handles incoming `sub_tool_result`
 */
export function routeSubToolResult(subResult: ToolResult, d: any, toolCalls: ToolCall[], upsertCall: (args: any) => void): void {
    const parentId = d.parent_action_id;
    const slotIdx = d.batch_slot_index;

    if (parentId && slotIdx !== undefined) {
        const metaIdx = toolCalls.findIndex(tc => tc.id === parentId);
        if (metaIdx >= 0) {
            const meta = { ...toolCalls[metaIdx] };
            let specialistSlot = meta.subCalls?.[slotIdx];

            if (!specialistSlot) {
                const autoSpecialist = String(d.specialist || 'Executor');
                const sName = SPECIALIST_TO_TOOL[autoSpecialist] || autoSpecialist;
                specialistSlot = {
                    id: `${parentId}-slot-${slotIdx}`,
                    name: sName,
                    arguments: {},
                    agentType: 'dispatch' as const,
                    subCalls: [],
                    subResults: [],
                };
            }

            specialistSlot = { ...specialistSlot };
            specialistSlot.subResults = [...(specialistSlot.subResults || []), subResult];
            if (!meta.subCalls) meta.subCalls = [];
            meta.subCalls = [...meta.subCalls];
            meta.subCalls[slotIdx] = specialistSlot;
            toolCalls[metaIdx] = meta;

            upsertCall({
                callId: subResult.id || "",
                name: subResult.name,
                parentId: specialistSlot.id,
                status: subResult.error ? "failed" : "completed",
                result: subResult.result,
            });
            return;
        }
    }

    // Legacy routing fallback
    let targetMetaIdx = parentId ? toolCalls.findIndex(tc => tc.id === parentId) : -1;
    if (targetMetaIdx < 0 && subResult.id) {
        targetMetaIdx = toolCalls.findIndex(tc => META_TOOLS.has(tc.name) && tc.subCalls?.some(sc => sc.id === subResult.id));
    }
    if (targetMetaIdx < 0) {
        targetMetaIdx = toolCalls.reduce((last, tc, i) => META_TOOLS.has(tc.name) ? i : last, -1);
    }

    if (targetMetaIdx >= 0) {
        const metaTool = { ...toolCalls[targetMetaIdx] };
        metaTool.subResults = [...(metaTool.subResults || []), subResult];
        toolCalls[targetMetaIdx] = metaTool;

        upsertCall({
            callId: subResult.id || "",
            name: subResult.name,
            parentId: toolCalls[targetMetaIdx].id,
            status: subResult.error ? "failed" : "completed",
            result: subResult.result,
        });
    }
}

/**
 * Streaming event router: handles incoming `executor_start`
 */
export function routeExecutorStart(d: any, toolCalls: ToolCall[], upsertCall: (args: any) => void): void {
    const parentId = d.parent_action_id;
    const slotIdx = d.batch_slot_index;
    const esSpecialist = d.specialist;
    const esPid = d._executor_pid;

    if (parentId && slotIdx !== undefined && esSpecialist) {
        const metaIdx = toolCalls.findIndex(tc => tc.id === parentId);
        if (metaIdx >= 0) {
            const meta = { ...toolCalls[metaIdx] };
            if (!meta.subCalls) meta.subCalls = [];
            meta.subCalls = [...meta.subCalls];
            const toolName = SPECIALIST_TO_TOOL[esSpecialist] || esSpecialist;

            const pdTasks = meta.arguments?.tasks;
            const taskDesc = Array.isArray(pdTasks) && slotIdx < pdTasks.length
                ? ((pdTasks[slotIdx] as any)?.task || (pdTasks[slotIdx] as any)?.context || "")
                : "";
            const contextDesc = Array.isArray(pdTasks) && slotIdx < pdTasks.length
                ? ((pdTasks[slotIdx] as any)?.context || "")
                : "";
            const esGoal = d.goal || "";

            meta.subCalls[slotIdx] = {
                id: esPid || `slot-${slotIdx}`,
                name: toolName,
                arguments: { task: taskDesc, context: contextDesc, goal: esGoal, model: d.resolved_model || d.model || "" },
                agentType: "dispatch" as const,
                subCalls: [],
                subResults: [],
            };

            toolCalls[metaIdx] = meta;

            upsertCall({
                callId: esPid || `slot-${slotIdx}`,
                name: toolName,
                parentId: parentId,
                specialist: esSpecialist,
                batchSlotIndex: slotIdx,
                status: "running",
                args: { task: taskDesc, context: contextDesc, goal: esGoal, model: d.resolved_model || d.model || "" },
            });
            return;
        }
    }

    // Fallback: propagate model
    if (d.resolved_model || d.model_full || d.model) {
        const resolvedModel = d.resolved_model || d.model_full || d.model || "";
        const runningSpecialist = [...toolCalls].reverse().find(tc => META_TOOLS.has(tc.name));
        if (runningSpecialist && runningSpecialist.arguments) {
            (runningSpecialist.arguments as Record<string, unknown>).model = resolvedModel;
        }
    }
}

/**
 * Streaming event router: handles incoming `executor_done`
 */
export function routeExecutorDone(d: any, toolCalls: ToolCall[], upsertCall: (args: any) => void): void {
    const parentId = d.parent_action_id;
    const slotIdx = d.batch_slot_index;

    if (parentId && slotIdx !== undefined) {
        const metaIdx = toolCalls.findIndex(tc => tc.id === parentId);
        if (metaIdx >= 0) {
            const meta = { ...toolCalls[metaIdx] };
            let specialistSlot = meta.subCalls?.[slotIdx];

            if (!specialistSlot) {
                const autoSpecialist = String(d.specialist || 'Executor');
                const sName = SPECIALIST_TO_TOOL[autoSpecialist] || autoSpecialist;
                specialistSlot = {
                    id: `${parentId}-slot-${slotIdx}`,
                    name: sName,
                    arguments: {},
                    agentType: 'dispatch' as const,
                    subCalls: [],
                    subResults: [],
                };
            }

            specialistSlot = { ...specialistSlot };
            if (!specialistSlot.subResults) specialistSlot.subResults = [];
            specialistSlot.subResults = [...specialistSlot.subResults];

            specialistSlot.subResults.push({
                id: specialistSlot.id || `slot-${slotIdx}`,
                name: specialistSlot.name,
                result: d.result || "Completed",
            });

            if (!meta.subCalls) meta.subCalls = [];
            meta.subCalls = [...meta.subCalls];
            meta.subCalls[slotIdx] = specialistSlot;
            toolCalls[metaIdx] = meta;

            upsertCall({
                callId: specialistSlot.id || `slot-${slotIdx}`,
                name: specialistSlot.name,
                parentId: parentId,
                status: "completed",
                result: d.result,
            });
        }
    }
}

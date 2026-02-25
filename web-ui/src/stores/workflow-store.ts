import { create } from "zustand";

export type WorkflowStatus = "running" | "completed" | "failed";

export interface WorkflowCall {
  callId: string;
  name: string;
  parentId?: string;
  specialist?: string;
  batchSlotIndex?: number;
  status: WorkflowStatus;
  args: Record<string, unknown>;
  result?: unknown;
  error?: string;
  startedAt: number;
  endedAt?: number;
  durationMs?: number;
}

interface UpsertCallInput {
  callId: string;
  name: string;
  parentId?: string;
  specialist?: string;
  batchSlotIndex?: number;
  status: WorkflowStatus;
  args?: Record<string, unknown>;
  result?: unknown;
}

interface WorkflowStore {
  calls: Record<string, WorkflowCall>;
  upsertCall: (input: UpsertCallInput) => void;
  reset: () => void;
}

function extractError(result: unknown): string | undefined {
  if (!result || typeof result !== "object") return undefined;
  const obj = result as Record<string, unknown>;
  if (typeof obj.error === "string") return obj.error;
  if (typeof obj.message === "string" && obj.error) return obj.message;
  return undefined;
}

function stableStringify(value: unknown): string {
  try {
    return JSON.stringify(value, (_k, v) => {
      if (v && typeof v === "object" && !Array.isArray(v)) {
        return Object.keys(v as Record<string, unknown>)
          .sort()
          .reduce<Record<string, unknown>>((acc, key) => {
            acc[key] = (v as Record<string, unknown>)[key];
            return acc;
          }, {});
      }
      return v;
    });
  } catch {
    return "";
  }
}

function isSameCall(prev: WorkflowCall, next: WorkflowCall): boolean {
  return (
    prev.callId === next.callId &&
    prev.name === next.name &&
    prev.parentId === next.parentId &&
    prev.specialist === next.specialist &&
    prev.batchSlotIndex === next.batchSlotIndex &&
    prev.status === next.status &&
    prev.error === next.error &&
    prev.startedAt === next.startedAt &&
    prev.endedAt === next.endedAt &&
    prev.durationMs === next.durationMs &&
    stableStringify(prev.args) === stableStringify(next.args) &&
    stableStringify(prev.result) === stableStringify(next.result)
  );
}

export const useWorkflowStore = create<WorkflowStore>((set) => ({
  calls: {},
  upsertCall: (input) =>
    set((state) => {
      const now = Date.now();
      const prev = state.calls[input.callId];
      const startedAt = prev?.startedAt ?? now;

      // Only set terminal timestamp once; prevents state churn on repeated "complete" renders.
      const shouldClose =
        (input.status === "completed" || input.status === "failed") && !prev?.endedAt;
      const endedAt = shouldClose ? now : prev?.endedAt;

      const call: WorkflowCall = {
        callId: input.callId,
        name: input.name,
        parentId: input.parentId ?? prev?.parentId,
        specialist: input.specialist ?? prev?.specialist,
        batchSlotIndex: input.batchSlotIndex ?? prev?.batchSlotIndex,
        status: input.status,
        args: input.args ?? prev?.args ?? {},
        result: input.result !== undefined ? input.result : prev?.result,
        error: extractError(input.result) ?? prev?.error,
        startedAt,
        endedAt,
        durationMs: endedAt ? endedAt - startedAt : undefined,
      };

      if (prev && isSameCall(prev, call)) {
        return state;
      }

      return {
        calls: {
          ...state.calls,
          [input.callId]: call,
        },
      };
    }),
  reset: () => set({ calls: {} }),
}));

export function selectChildren(calls: Record<string, WorkflowCall>, parentId: string): WorkflowCall[] {
  return Object.values(calls)
    .filter((call) => call.parentId === parentId)
    .sort((a, b) => a.startedAt - b.startedAt);
}

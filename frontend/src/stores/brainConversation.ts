import type { ConversationThread, ConversationTurn } from "../types";

const ACTIVE_BRAIN_TASKS_KEY = "tongzhouxing_brain_active_tasks";
const ACTIVE_CONVERSATION_THREADS_KEY =
  "tongzhouxing_brain_active_conversation_threads";

interface StoredActiveBrainTasks {
  version: 1;
  accounts: Record<string, number>;
}

const EMPTY_ACTIVE_TASKS: StoredActiveBrainTasks = {
  version: 1,
  accounts: {},
};

export function upsertTurnByClientMessageId(
  thread: ConversationThread,
  threadId: number,
  clientMessageId: string,
  update: (current: ConversationTurn | null) => ConversationTurn | null,
): ConversationThread {
  if (thread.id !== threadId) return thread;

  const turnIndex = thread.turns.findIndex(
    (turn) => turn.client_message_id === clientMessageId,
  );
  const current = turnIndex >= 0 ? thread.turns[turnIndex] : null;
  const next = update(current);
  if (next == null || next === current) return thread;

  return turnIndex < 0
    ? { ...thread, turns: [...thread.turns, next] }
    : {
        ...thread,
        turns: thread.turns.map((turn, index) => index === turnIndex ? next : turn),
      };
}

export function isCurrentConversationRequest({
  activeAccountId,
  activeThreadId,
  accountId,
  threadId,
}: {
  activeAccountId: number | null;
  activeThreadId: number | null;
  accountId: number;
  threadId: number;
}) {
  return activeAccountId === accountId && activeThreadId === threadId;
}

function readActiveTasks(): StoredActiveBrainTasks {
  try {
    const raw = localStorage.getItem(ACTIVE_BRAIN_TASKS_KEY);
    if (!raw) return EMPTY_ACTIVE_TASKS;
    const parsed = JSON.parse(raw) as Partial<StoredActiveBrainTasks>;
    if (parsed.version !== 1 || !parsed.accounts || typeof parsed.accounts !== "object") {
      return EMPTY_ACTIVE_TASKS;
    }
    return {
      version: 1,
      accounts: Object.fromEntries(
        Object.entries(parsed.accounts).filter(([, taskId]) => Number.isInteger(taskId)),
      ),
    };
  } catch {
    return EMPTY_ACTIVE_TASKS;
  }
}

function writeActiveTasks(value: StoredActiveBrainTasks) {
  localStorage.setItem(ACTIVE_BRAIN_TASKS_KEY, JSON.stringify(value));
}

export function getActiveBrainTaskId(accountId: number): number | null {
  return readActiveTasks().accounts[String(accountId)] ?? null;
}

export function setActiveBrainTaskId(accountId: number, taskId: number) {
  const stored = readActiveTasks();
  writeActiveTasks({
    version: 1,
    accounts: { ...stored.accounts, [String(accountId)]: taskId },
  });
}

export function clearActiveBrainTaskId(accountId: number) {
  const stored = readActiveTasks();
  const accounts = { ...stored.accounts };
  delete accounts[String(accountId)];
  writeActiveTasks({ version: 1, accounts });
}

function readActiveConversationThreads(): StoredActiveBrainTasks {
  try {
    const raw = localStorage.getItem(ACTIVE_CONVERSATION_THREADS_KEY);
    if (!raw) return EMPTY_ACTIVE_TASKS;
    const parsed = JSON.parse(raw) as Partial<StoredActiveBrainTasks>;
    if (parsed.version !== 1 || !parsed.accounts || typeof parsed.accounts !== "object") {
      return EMPTY_ACTIVE_TASKS;
    }
    return {
      version: 1,
      accounts: Object.fromEntries(
        Object.entries(parsed.accounts).filter(
          ([accountId, threadId]) =>
            Number.isInteger(Number(accountId)) &&
            Number(accountId) > 0 &&
            Number.isInteger(threadId) &&
            threadId > 0,
        ),
      ),
    };
  } catch {
    return EMPTY_ACTIVE_TASKS;
  }
}

function writeActiveConversationThreads(value: StoredActiveBrainTasks) {
  localStorage.setItem(ACTIVE_CONVERSATION_THREADS_KEY, JSON.stringify(value));
}

export function getActiveConversationThreadId(accountId: number): number | null {
  if (!Number.isInteger(accountId) || accountId <= 0) return null;
  return readActiveConversationThreads().accounts[String(accountId)] ?? null;
}

export function setActiveConversationThreadId(accountId: number, threadId: number) {
  if (
    !Number.isInteger(accountId) ||
    accountId <= 0 ||
    !Number.isInteger(threadId) ||
    threadId <= 0
  ) {
    return;
  }
  const stored = readActiveConversationThreads();
  writeActiveConversationThreads({
    version: 1,
    accounts: { ...stored.accounts, [String(accountId)]: threadId },
  });
}

export function clearActiveConversationThreadId(accountId: number) {
  if (!Number.isInteger(accountId) || accountId <= 0) return;
  const stored = readActiveConversationThreads();
  const accounts = { ...stored.accounts };
  delete accounts[String(accountId)];
  writeActiveConversationThreads({ version: 1, accounts });
}

export function clearBrainConversationSession() {
  localStorage.removeItem(ACTIVE_BRAIN_TASKS_KEY);
  localStorage.removeItem(ACTIVE_CONVERSATION_THREADS_KEY);
}

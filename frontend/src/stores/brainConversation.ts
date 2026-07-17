const ACTIVE_BRAIN_TASKS_KEY = "tongzhouxing_brain_active_tasks";

interface StoredActiveBrainTasks {
  version: 1;
  accounts: Record<string, number>;
}

const EMPTY_ACTIVE_TASKS: StoredActiveBrainTasks = {
  version: 1,
  accounts: {},
};

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

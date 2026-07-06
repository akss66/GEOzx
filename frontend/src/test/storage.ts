import { vi } from "vitest";

export function installLocalStorage(initial: Record<string, string> = {}) {
  const store = new Map(Object.entries(initial));

  const storage = {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      store.delete(key);
    }),
    clear: vi.fn(() => {
      store.clear();
    }),
    key: vi.fn((index: number) => Array.from(store.keys())[index] ?? null),
    get length() {
      return store.size;
    },
  } as Storage;

  vi.stubGlobal("localStorage", storage);

  return storage;
}

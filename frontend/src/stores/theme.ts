import { create } from "zustand";

type Mode = "dark" | "light";
const KEY = "dyflow_theme";

interface ThemeState {
  mode: Mode;
  toggle: () => void;
}

export const useThemeMode = create<ThemeState>((set) => ({
  mode: (localStorage.getItem(KEY) as Mode | null) ?? "light",
  toggle: () =>
    set((s) => {
      const mode: Mode = s.mode === "dark" ? "light" : "dark";
      localStorage.setItem(KEY, mode);
      return { mode };
    }),
}));

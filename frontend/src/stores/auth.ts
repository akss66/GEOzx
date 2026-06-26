import { create } from "zustand";

import { TOKEN_KEY } from "../api/client";
import type { User } from "../types";

interface AuthState {
  token: string | null;
  user: User | null;
  setAuth: (token: string, user: User) => void;
  setUser: (user: User | null) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: null,
  setAuth: (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    set({ token, user });
  },
  setUser: (user) => set({ user }),
  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    set({ token: null, user: null });
  },
}));

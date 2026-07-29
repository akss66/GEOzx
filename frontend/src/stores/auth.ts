import { create } from "zustand";

import { TOKEN_KEY } from "../api/client";
import { queryClient } from "../queryClient";
import type { User } from "../types";
import { clearBrainConversationSession } from "./brainConversation";

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
    queryClient.clear();
    clearBrainConversationSession();
    localStorage.setItem(TOKEN_KEY, token);
    set({ token, user });
  },
  setUser: (user) => set({ user }),
  logout: () => {
    queryClient.clear();
    clearBrainConversationSession();
    localStorage.removeItem(TOKEN_KEY);
    set({ token: null, user: null });
  },
}));

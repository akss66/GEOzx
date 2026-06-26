import { api } from "./client";
import type { CreateUserInput, LoginResponse, User } from "../types";

export async function login(email: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", { email, password });
  return data;
}

export async function getMe(): Promise<User> {
  const { data } = await api.get<User>("/auth/me");
  return data;
}

export async function listUsers(): Promise<User[]> {
  const { data } = await api.get<User[]>("/users");
  return data;
}

export async function createUser(input: CreateUserInput): Promise<User> {
  const { data } = await api.post<User>("/users", input);
  return data;
}

import { api } from "./client";
import type {
  CreateUserInput,
  LoginResponse,
  UpdateUserAccessInput,
  UpdateUserInput,
  User,
  UserAccessCatalog,
  UserDetail,
} from "../types";

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

export async function getUserDetail(userId: number): Promise<UserDetail> {
  const { data } = await api.get<UserDetail>(`/users/${userId}`);
  return data;
}

export async function getUserAccessCatalog(): Promise<UserAccessCatalog> {
  const { data } = await api.get<UserAccessCatalog>("/users/access-catalog");
  return data;
}

export async function updateUser(userId: number, input: UpdateUserInput): Promise<User> {
  const { data } = await api.patch<User>(`/users/${userId}`, input);
  return data;
}

export async function updateUserAccess(
  userId: number,
  input: UpdateUserAccessInput,
): Promise<UserDetail> {
  const { data } = await api.put<UserDetail>(`/users/${userId}/access`, input);
  return data;
}

export type Role = "admin" | "user";

export interface User {
  id: number;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface CreateUserInput {
  email: string;
  password: string;
  display_name: string;
  role: Role;
}

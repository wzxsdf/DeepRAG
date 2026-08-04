import { client } from "./client"

export interface AuthUser {
  id: string
  email: string
  name: string
  tenant_id: string
}

export interface AuthResp {
  token: string
  user: AuthUser
}

export const authApi = {
  register: (data: { email: string; password: string; name?: string }) =>
    client.post<AuthResp>("/auth/register", data).then((r) => r.data),
  login: (data: { email: string; password: string }) =>
    client.post<AuthResp>("/auth/login", data).then((r) => r.data),
  me: () => client.get("/auth/me").then((r) => r.data),
}

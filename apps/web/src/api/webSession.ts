export type Role = 'admin' | 'operator'
export type Account = { username: string; role: Role; enabled: boolean }
export type Credentials = { username: string; password: string }

export const roleLabels = { admin: '管理员', operator: '评测操作员' }

export async function accountRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/web/${path}`, {
    method,
    cache: 'no-store',
    ...(body === undefined ? {} : {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '账号请求失败，请检查输入后重试')
  return data as T
}

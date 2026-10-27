/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-22 02:58:16
 * @LastEditTime: 2026-10-22 09:21:45
 * @Description: 
 */

export interface MysqlField {
  name: string
  data_type: string
  nullable: boolean
  is_primary_key: boolean
}

export interface MysqlSchema {
  database: string
  table: string
  row_count: number
  fields: MysqlField[]
}

export async function fetchMysqlSchema(): Promise<MysqlSchema> {
  const response = await fetch('/api/v1/datasources/mysql/schema')

  if (!response.ok) {
    throw new Error(`获取 MySQL Schema 失败：HTTP ${response.status}`)
  }

  return response.json() as Promise<MysqlSchema>
}

export type MysqlPreviewValue = string | number | boolean | null

export type MysqlPreviewRow = Record<string, MysqlPreviewValue>

export interface MysqlPreview {
  database: string
  table: string
  limit: number
  rows: MysqlPreviewRow[]
}

export async function fetchMysqlPreview(
  limit = 5,
  maxTextLength = 300,
): Promise<MysqlPreview> {
  const params = new URLSearchParams({
    limit: String(limit),
    max_text_length: String(maxTextLength),
  })

  const response = await fetch(
    `/api/v1/datasources/mysql/preview?${params.toString()}`,
  )

  if (!response.ok) {
    throw new Error(
      `获取 MySQL 数据预览失败：HTTP ${response.status}`,
    )
  }

  return response.json() as Promise<MysqlPreview>
}

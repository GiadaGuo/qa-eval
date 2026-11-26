const beijingTimeZone = 'Asia/Shanghai'
const dayFormatter = new Intl.DateTimeFormat('zh-CN', { timeZone: beijingTimeZone })

function parseAgentTimestamp(iso: string): Date {
  // SQLite serializes stored UTC timestamps without an offset.
  const hasTime = /[T ]\d{2}:\d{2}/.test(iso)
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso)
  return new Date(hasTime && !hasOffset ? iso.replace(' ', 'T') + 'Z' : iso)
}

export function formatAgentTime(iso: string, style: 'datetime' | 'time' = 'datetime'): string {
  const date = parseAgentTimestamp(iso)
  const options = { timeZone: beijingTimeZone, hourCycle: 'h23' } as const
  return style === 'time'
    ? date.toLocaleTimeString('zh-CN', options)
    : date.toLocaleString('zh-CN', options)
}

export function formatAgentRelativeTime(iso: string, now = new Date()): string {
  const date = parseAgentTimestamp(iso)
  const elapsed = now.getTime() - date.getTime()
  if (!Number.isFinite(elapsed)) return '—'
  if (elapsed >= 0 && elapsed < 60_000) return '刚刚'
  if (elapsed >= 0 && elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}分钟前`
  if (dayFormatter.format(date) === dayFormatter.format(now)) {
    return date.toLocaleTimeString('zh-CN', {
      timeZone: beijingTimeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    })
  }
  return dayFormatter.format(date)
}

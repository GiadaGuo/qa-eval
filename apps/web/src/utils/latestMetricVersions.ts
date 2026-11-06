export interface VersionedMetric {
  metric_key: string
  version: string
}

const parseVersion = (version: string): { core: number[]; prerelease: string[] } => {
  const normalized = version.trim().replace(/^v/i, '')
  const [withoutBuild] = normalized.split('+', 1)
  const [corePart, prereleasePart = ''] = withoutBuild.split('-', 2)
  return {
    core: corePart.split('.').map(part => {
      const parsed = Number.parseInt(part, 10)
      return Number.isNaN(parsed) ? 0 : parsed
    }),
    prerelease: prereleasePart ? prereleasePart.split('.') : [],
  }
}

export const compareMetricVersions = (left: string, right: string): number => {
  const leftVersion = parseVersion(left)
  const rightVersion = parseVersion(right)
  const length = Math.max(leftVersion.core.length, rightVersion.core.length)
  for (let index = 0; index < length; index += 1) {
    const difference = (leftVersion.core[index] ?? 0) - (rightVersion.core[index] ?? 0)
    if (difference !== 0) return difference
  }
  if (!leftVersion.prerelease.length && rightVersion.prerelease.length) return 1
  if (leftVersion.prerelease.length && !rightVersion.prerelease.length) return -1
  return leftVersion.prerelease.join('.').localeCompare(rightVersion.prerelease.join('.'), undefined, {
    numeric: true,
    sensitivity: 'base',
  })
}

export const latestMetricVersions = <Metric extends VersionedMetric>(metrics: Metric[]): Metric[] => {
  const latestByKey = new Map<string, Metric>()
  for (const metric of metrics) {
    const current = latestByKey.get(metric.metric_key)
    if (!current || compareMetricVersions(metric.version, current.version) > 0) {
      latestByKey.set(metric.metric_key, metric)
    }
  }
  return [...latestByKey.values()]
}

export interface RadarPoint { x: number; y: number }

export const buildRadarSegments = (
  scores: Array<number | null>,
  pointFor: (index: number, score: number) => RadarPoint,
): string[] => {
  const segments: string[] = []
  let current: string[] = []
  scores.forEach((score, index) => {
    if (score === null) {
      if (current.length > 1) segments.push(current.join(' '))
      current = []
      return
    }
    const point = pointFor(index, score)
    current.push(`${point.x},${point.y}`)
  })
  if (current.length > 1) segments.push(current.join(' '))
  return segments
}

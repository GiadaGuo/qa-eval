import type { SemanticBinding, SemanticDraftItem } from '../api/datasetVersions'

export function semanticBindingKey(binding: Pick<SemanticBinding, 'semantic' | 'representation'>): string {
  return `${binding.semantic}:${binding.representation}`
}

export function addRecommendedBindings(current: SemanticDraftItem[], recommended: SemanticDraftItem[]): SemanticDraftItem[] {
  const existing = new Set(current.map(({ binding }) => semanticBindingKey(binding)))
  return recommended.flatMap((item) => {
    const key = semanticBindingKey(item.binding)
    if (existing.has(key)) return []
    existing.add(key)
    return [{ ...item, confirmed: false }]
  })
}

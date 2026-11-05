import type { FieldMappingSpec, UploadedSourceInspection } from '../api/datasetVersions'

export type MappingGroup =
  | 'unassigned'
  | 'sample_id'
  | 'content_fields'
  | 'annotation_fields'
  | 'metadata_fields'
  | 'time_fields'
  | 'source_fields'
  | 'passthrough'
  | 'ignored'
export type ContentModality = 'text' | 'image' | 'audio' | 'video' | 'structured'
export type TextFormat = 'plain' | 'html' | 'markdown' | 'code'
export type Assignments = Record<string, MappingGroup>
export type ContentSettings = Record<string, {
  modality: ContentModality
  role: string
  format: TextFormat
  requiredForNormalization: boolean
}>
export type TimeSettings = Record<string, {
  targetName: string
  sourceTimezone: string
}>

export interface MappingEditorState {
  assignments: Assignments
  contentSettings: ContentSettings
  timeSettings: TimeSettings
}

export function mappingToEditorState(
  fields: UploadedSourceInspection['fields'],
  mapping: FieldMappingSpec,
): MappingEditorState {
  const assignments: Assignments = {}
  const contentSettings: ContentSettings = {}
  const timeSettings: TimeSettings = {}
  for (const field of fields) {
    const rules = mapping.mappings.filter(rule => rule.source_path === `$.${field.name}`)
    const rule = rules[0]
    if (!rule) assignments[field.name] = 'unassigned'
    else if (rule.action === 'IGNORE') assignments[field.name] = 'ignored'
    else if (rule.action === 'PASSTHROUGH') assignments[field.name] = 'passthrough'
    else if (rule.target?.kind === 'SAMPLE_ID') assignments[field.name] = 'sample_id'
    else if (rule.target?.kind === 'CONTENT') {
      assignments[field.name] = 'content_fields'
      contentSettings[field.name] = {
        modality: (rule.target.modality ?? 'text') as ContentModality,
        role: rule.target.role ?? field.name,
        format: rule.target.role?.endsWith('.html') ? 'html' : 'plain',
        requiredForNormalization: rule.required_for_normalization,
      }
    } else if (rule.target?.kind === 'TEMPORAL') {
      assignments[field.name] = 'time_fields'
      timeSettings[field.name] = {
        targetName: rule.target.name ?? field.name,
        sourceTimezone: 'unknown',
      }
    } else if (rule.target?.kind === 'SOURCE_ATTRIBUTE') assignments[field.name] = 'source_fields'
    else if (rule.target?.kind === 'ANNOTATION') assignments[field.name] = 'annotation_fields'
    else assignments[field.name] = 'metadata_fields'
  }
  return { assignments, contentSettings, timeSettings }
}

export function rulesForUnmodifiedField(
  mapping: FieldMappingSpec | null,
  fieldName: string,
  dirtyFields: ReadonlySet<string>,
) {
  if (!mapping || dirtyFields.has(fieldName)) return []
  return mapping.mappings.filter(rule => rule.source_path === `$.${fieldName}`)
}

export function selectInitialMapping(
  savedMapping: FieldMappingSpec | null,
  recommendedMapping: FieldMappingSpec | null,
) {
  return savedMapping ?? recommendedMapping
}

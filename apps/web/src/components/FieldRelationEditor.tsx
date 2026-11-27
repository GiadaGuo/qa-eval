import { useEffect, useState } from 'react'
import { Alert, Button, Card, Checkbox, Collapse, Input, InputNumber, Select, Space } from 'antd'
import ParameterInput from './ParameterInput'
import type { MappingContext } from './ContractInputEditor'
import { getFieldRelationResources, type FieldRelationResources, type InputSelector } from '../api/qualityContracts'
import type { ParameterSchema } from '../api/registry'

export const relationParameterLabels: Record<string, string> = {
  unicode_whitespace_v1: 'Unicode 与空白归一', unicode_casefold_v1: 'Unicode、空白与大小写归一', document_number_v1: '发文字号归一',
  day: '按日', instant: '按时刻', exact_match: '精确匹配', structured_match: '结构化匹配', semantic_match: '文本相关性匹配', embedding_match: '语义匹配（Embedding）', type_match: '类别匹配',
  normalizer: '规范化方式', attribute: '比较属性', document_number: '发文字号', date: '日期', number: '数值',
  precision: '时间精度', timezone: '比较时区', left_naive_timezone: '左侧默认时区', right_naive_timezone: '右侧默认时区',
  maximum_year: '最大年份', right_extractor: '右侧提取器', left_unit: '左侧默认单位', right_unit: '右侧默认单位',
  unit_factors: '单位换算表', abs_tol: '绝对容差（换算后单位）', rel_tol: '相对容差（比例）',
  threshold_version: '阈值版本', fail_below: '不一致阈值', pass_at: '一致阈值',
  tfidf: 'TF-IDF 资源', dictionary: '类别词典', calibration: '校准曲线', block_chars: '分块长度（字符）',
  resource_id: '平台 TF-IDF 版本', model_id: '平台模型', chunk_chars: '分块长度（字符）',
  max_chunks: '单字段最大分块数', aggregation: '相似度聚合', max: '最高分块相似度', bidirectional_mean: '双向最佳匹配均值',
  minimum_body_chars: '正文最小长度（字符）', applies_when: '适用条件',
}
const methodDescriptions: Record<string, string> = {
  exact_match: '比较表达同一业务属性的字段值是否相等。',
  structured_match: '提取文号、日期或数值后比较。',
  embedding_match: '比较文本片段的语义相似度，适用于短文本与长正文。',
  semantic_match: '用版本化 TF-IDF 比较文本的词项相关性。',
  type_match: '用类别词典检查声明类别与文本线索是否一致。',
}
const thresholdKeys = ['fail_below', 'pass_at']
const probabilisticMethods = ['embedding_match', 'semantic_match', 'type_match']

type MatcherParameters = Record<string, unknown>
export type Relation = { id: string; left?: string; right?: string; method: string; sharedAttribute?: string; weight: number; required: boolean; veto_on_conflict: boolean; parameters: MatcherParameters }
type MatcherSchema = ParameterSchema & { required?: string[]; oneOf?: MatcherSchema[]; const?: string }
type RelationSchema = {
  maxItems?: number
  items?: { properties: Record<string, ParameterSchema>; allOf: {
    if: { properties: { method: { const: string } } }
    then: { properties: { parameters: MatcherSchema } }
  }[] }
}
const types: Record<string, string> = { string: 'string', text: 'text', integer: 'number', float: 'number', number: 'number', boolean: 'boolean', datetime: 'datetime', array: 'array', object: 'object' }

function fieldOptions(mapping?: MappingContext, digest?: string) {
  const fields = mapping?.recommendations.fields ?? []
  const bindings = mapping?.revisions.find(item => item.digest === digest)?.revision.bindings ?? []
  return [
    ...fields.map(field => {
      const kinds = [...new Set(field.data_type.split('|').filter(kind => kind !== 'null').map(kind => types[kind]))]
      return { value: `field:${field.name}`, label: field.display_name ? `${field.name}（${field.display_name}）` : field.name,
        kind: kinds.length === 1 ? kinds[0] : undefined, path: [field.name], selector: { field_tokens: [field.name] } }
    }),
    ...bindings.filter(binding => bindings.filter(other => other.semantic === binding.semantic).length === 1).map(binding => ({
      value: `semantic:${binding.semantic}`, label: `${binding.field_tokens.join(' / ')}（${binding.definition?.name ?? binding.semantic}）`,
      kind: binding.transform ? 'text' : types[binding.definition?.type ?? ''] ??
        (binding.field_tokens.length === 1 ? types[fields.find(field => field.name === binding.field_tokens[0])?.data_type ?? ''] : undefined),
      path: binding.field_tokens, selector: { semantic: binding.semantic },
    })),
  ]
}

export function compileFieldRelations(rows: Relation[], mapping?: MappingContext, digest?: string) {
  const options = fieldOptions(mapping, digest)
  const fields: Record<string, { value_type: string; semantic_tag?: string }> = {}
  const selectors: Record<string, InputSelector> = {}
  const aliases = new Map<string, string>()
  const edges = new Set<string>()
  const endpoint = (choice?: string) => {
    const option = options.find(item => item.value === choice)
    if (!option?.kind) throw new Error('请选择类型明确的字段')
    let alias = aliases.get(option.value)
    if (!alias) {
      alias = `field_${aliases.size + 1}`
      aliases.set(option.value, alias)
      fields[alias] = { value_type: option.kind }
      selectors[alias] = option.selector
    }
    return { alias, path: JSON.stringify(option.path) }
  }
  if (!rows.length) throw new Error('请添加并确认至少一组字段关系')
  const relations = rows.map(({ sharedAttribute, ...row }) => {
    const left = endpoint(row.left)
    const right = endpoint(row.right)
    if (left.path === right.path) throw new Error('关系两端来自同一字段')
    const edge = JSON.stringify([left.path, right.path].sort())
    if (edges.has(edge)) throw new Error('存在重复字段关系')
    edges.add(edge)
    if (row.method === 'exact_match' || row.method === 'structured_match') {
      const tag = sharedAttribute?.trim()
      if (!tag) throw new Error('请填写两侧字段已确认的共同业务属性')
      for (const { alias } of [left, right]) {
        if (fields[alias].semantic_tag && fields[alias].semantic_tag !== tag) {
          throw new Error('同一字段在多组关系中使用了不同的共同业务属性')
        }
        fields[alias].semantic_tag = tag
      }
    }
    const condition = row.parameters.applies_when as { field?: string; equals?: unknown } | undefined
    return { ...row, left: left.alias, right: right.alias, parameters: condition
      ? { ...row.parameters, applies_when: { ...condition, field: endpoint(condition.field).alias } }
      : row.parameters }
  })
  return { fields, relations, selectors }
}

function ResourceParameter({ label, value, onChange }: { label: string; value: unknown; onChange: (value: unknown) => void }) {
  const [error, setError] = useState(false)
  return <Space orientation="vertical">
    <label style={{ display: 'block' }}>{label}
      <input type="file" accept=".json,application/json" onChange={async event => {
        const file = event.target.files?.[0]
        event.target.value = ''
        if (!file) return
        try {
          const resource: unknown = JSON.parse(await file.text())
          if (!resource || typeof resource !== 'object' || Array.isArray(resource)) throw new Error('invalid resource')
          onChange(resource)
          setError(false)
        } catch { setError(true) }
      }} />
    </label>
    {error && <Alert type="error" title="无法读取配置资源，请选择有效的 JSON 对象文件" />}
    {value != null
      ? <Button onClick={() => { onChange(undefined); setError(false) }}>移除已载入的资源</Button>
      : <span>请选择已有配置资源文件</span>}
  </Space>
}

export default function FieldRelationEditor({ value = [], onChange, mapping, digest, schema, defaultMatcher }: {
  value?: Relation[]; onChange?: (rows: Relation[]) => void; mapping?: MappingContext; digest?: string; schema: ParameterSchema; defaultMatcher?: string
}) {
  const [resources, setResources] = useState<FieldRelationResources>()
  const [resourceError, setResourceError] = useState<string>()
  useEffect(() => {
    if (!defaultMatcher) return
    const controller = new AbortController()
    getFieldRelationResources(controller.signal).then(setResources).catch((error: Error) => {
      if (!controller.signal.aborted) setResourceError(error.message)
    })
    return () => controller.abort()
  }, [defaultMatcher])

  const relationSchema = schema as RelationSchema
  const definition = relationSchema.items
  const methods = definition?.allOf.map(item => item.if.properties.method.const) ?? []
  const options = fieldOptions(mapping, digest)
  const matcher = (method: string) => definition?.allOf.find(item => item.if.properties.method.const === method)?.then.properties.parameters
  const defaults = (selected?: MatcherSchema) => Object.fromEntries(Object.entries(selected?.properties ?? {}).flatMap(([key, spec]) =>
    spec.const !== undefined && selected?.required?.includes(key) ? [[key, spec.const]] : spec.default !== undefined ? [[key, spec.default]] : []))
  const availability = (method: string) => {
    if (!defaultMatcher || !['embedding_match', 'semantic_match'].includes(method)) return undefined
    if (!resources) return resourceError ?? '正在读取平台资源'
    if (method === 'semantic_match') return resources.tfidf.length ? undefined : '平台没有可用的 TF-IDF 版本'
    const model = resources.models.find(item => item.available)
    return model ? undefined : resources.models[0]?.reason ?? '平台没有可用的 Embedding 模型'
  }
  const methodDefaults = (method: string) => {
    const parameters = defaults(matcher(method))
    if (method === 'semantic_match' && resources?.tfidf[0]) parameters.resource_id = resources.tfidf[0].id
    return parameters
  }
  return <Space orientation="vertical" style={{ width: '100%' }}>
    {value.map((row, index) => {
      const update = (patch: Partial<Relation>) => onChange?.(value.map(item => item.id === row.id ? { ...row, ...patch } : item))
      const base = matcher(row.method)
      const branches = base?.oneOf
      const active = branches?.find(item => item.properties?.attribute.const === row.parameters.attribute) ?? base
      const setParameter = (key: string, input: unknown) => {
        const parameters = { ...row.parameters }
        if (input === undefined || input === null || input === '') delete parameters[key]
        else parameters[key] = input
        update({ parameters })
      }
      const required = active?.required?.filter(key => row.parameters[key] == null) ?? []
      const renderParameter = ([key, spec]: [string, ParameterSchema]) => {
        if (key === 'resource_id' || key === 'model_id') {
          const available = key === 'resource_id'
            ? resources?.tfidf.map(item => ({ value: item.id, label: item.id }))
            : resources?.models.map(item => ({ value: item.id, label: item.label, disabled: !item.available }))
          return <label key={key} style={{ display: 'block' }}>{relationParameterLabels[key]}
            <Select aria-label={relationParameterLabels[key]} style={{ width: '100%' }} value={row.parameters[key]}
              options={available} onChange={input => setParameter(key, input)} />
          </label>
        }
        if (spec.const !== undefined) return active?.required?.includes(key) ? null : <Checkbox key={key} checked={row.parameters[key] === spec.const} onChange={event => setParameter(key, event.target.checked ? spec.const : undefined)}>{relationParameterLabels[key] ?? key}</Checkbox>
        if (key === 'applies_when') {
          const condition = row.parameters[key] as { field?: string; equals?: unknown } | undefined
          const kind = options.find(option => option.value === condition?.field)?.kind
          return <Space key={key} wrap>
            <Select aria-label="适用条件字段" allowClear placeholder="适用条件字段" style={{ minWidth: 180 }} options={options} value={condition?.field}
              onChange={field => setParameter(key, field ? { field } : undefined)} />
            {condition?.field && <ParameterInput schema={{ type: kind === 'number' || kind === 'boolean' ? kind : 'string' }}
              value={condition.equals} onChange={equals => setParameter(key, { ...condition, equals })} />}
          </Space>
        }
        if (spec.type === 'object') return <ResourceParameter key={key} label={relationParameterLabels[key] ?? key}
          value={row.parameters[key]} onChange={input => setParameter(key, input)} />
        if (Array.isArray(spec.enum)) return <label key={key} style={{ display: 'block' }}>{relationParameterLabels[key] ?? key}
          <Select allowClear style={{ width: '100%' }} value={row.parameters[key]} onChange={input => setParameter(key, input)}
            options={spec.enum.map(value => ({ value, label: relationParameterLabels[String(value)] ?? String(value) }))} />
        </label>
        return <label key={key} style={{ display: 'block' }}>{relationParameterLabels[key] ?? key}
          {spec.minimum !== undefined && `（${spec.minimum}–${spec.maximum}）`}
          <ParameterInput schema={spec} value={row.parameters[key]} onChange={input => setParameter(key, input)} />
        </label>
      }
      const entries = Object.entries(active?.properties ?? {}).filter(([key]) => !defaultMatcher || ![
        'threshold_version', 'model_identity', 'tfidf', 'backend',
      ].includes(key))
      const lowFrequency = entries.filter(([key]) => defaultMatcher
        ? !thresholdKeys.includes(key) && key !== 'attribute'
        : !active?.required?.includes(key) && key !== 'normalizer')
      const veto = <Checkbox checked={row.veto_on_conflict} disabled={probabilisticMethods.includes(row.method)}
        onChange={event => update({ veto_on_conflict: event.target.checked })}>一票否决</Checkbox>
      const sharedAttribute = ['exact_match', 'structured_match'].includes(row.method) && <label style={{ display: 'block' }}>
        已确认的共同业务属性
        <Input value={row.sharedAttribute} maxLength={160} placeholder="两侧字段表达同一属性时填写，如发文字号"
          onChange={event => update({ sharedAttribute: event.target.value })} />
      </label>
      const attributeSelect = branches && <Select aria-label="比较属性" placeholder="比较属性" value={row.parameters.attribute}
        options={branches.map(branch => ({ value: branch.properties?.attribute.const, label: relationParameterLabels[String(branch.properties?.attribute.const)] }))}
        onChange={attribute => update({ parameters: defaults(branches.find(branch => branch.properties?.attribute.const === attribute)) })} />
      const unavailable = availability(row.method)
      return <Card key={row.id} size="small" title={`关系 ${index + 1}`}
        extra={<Button onClick={() => onChange?.(value.filter(item => item.id !== row.id))}>删除关系</Button>}>
        <Space wrap>
          {(['left', 'right'] as const).map((side, i) => <Select key={side} aria-label={i === 0 ? '比较字段 A' : '比较字段 B'}
            placeholder={i === 0 ? '比较字段 A' : '比较字段 B'} style={{ minWidth: 185 }} showSearch optionFilterProp="label"
            options={options.map(option => ({ ...option, disabled: !option.kind }))} value={row[side]} onChange={choice => update({ [side]: choice })} />)}
          <Select aria-label="匹配方法" value={row.method}
            options={methods.map(method => ({ value: method, label: relationParameterLabels[method] ?? method }))}
            onChange={method => update({ method, parameters: methodDefaults(method), veto_on_conflict: false })} />
          <label title="权重必须大于 0">权重 <InputNumber min={0} max={definition?.properties.weight.maximum} value={row.weight}
            onChange={weight => update({ weight: weight ?? 0 })} /></label>
          <Checkbox checked={row.required} onChange={event => update({ required: event.target.checked })}>必评</Checkbox>
          {!defaultMatcher && veto}
        </Space>
        {defaultMatcher && <p>{methodDescriptions[row.method]}</p>}
        {!defaultMatcher && <>{sharedAttribute}{attributeSelect}</>}
        {entries.filter(entry => !lowFrequency.includes(entry) && entry[0] !== 'attribute').map(renderParameter)}
        {defaultMatcher && probabilisticMethods.includes(row.method) &&
          <p>分数低于不一致阈值判不一致，达到一致阈值判一致，两者之间为未判定。试运行阈值，未经业务校准。</p>}
        {(defaultMatcher || lowFrequency.length > 0) && <Collapse size="small" items={[{
          key: 'advanced', label: '高级设置', children: <>
            {defaultMatcher && <>{sharedAttribute}{attributeSelect}{veto}</>}
            {lowFrequency.map(renderParameter)}
            {row.method === 'embedding_match' && <p>最高分块相似度适用于短文本检索长正文中的相关片段。双向最佳匹配均值衡量整体覆盖。超出分块上限的字段记为未知。</p>}
          </>,
        }]} />}
        {unavailable && <Alert type="warning" title={unavailable} />}
        {defaultMatcher && sharedAttribute && !row.sharedAttribute?.trim() &&
          <Alert type="warning" title="请在高级设置中确认两侧共同表达的业务属性" />}
        {defaultMatcher && branches && !row.parameters.attribute &&
          <Alert type="warning" title="请在高级设置中选择比较属性" />}
        {!!required.length && <Alert type="warning" title={`缺少配置：${required.map(key => relationParameterLabels[key] ?? key).join('、')}`} />}
      </Card>
    })}
    <Button disabled={!mapping || value.length >= (relationSchema.maxItems ?? Infinity) || !methods.length} onClick={() => {
      const method = defaultMatcher ?? methods[0]
      onChange?.([...value, { id: `relation_${crypto.randomUUID()}`, method, weight: 1, required: false, veto_on_conflict: false, parameters: methodDefaults(method) }])
    }}>添加关系</Button>
  </Space>
}

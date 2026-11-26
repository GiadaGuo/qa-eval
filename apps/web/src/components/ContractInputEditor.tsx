import { Alert, Button, Card, Collapse, Select, Space, Typography } from 'antd'
import type { getMappingDraft, listMappingRevisions, getMappingRecommendations } from '../api/datasetVersions'
import type { ContractInputSlot, preflightContractInputs } from '../api/qualityContracts'

export type MappingContext = {
  draft: Awaited<ReturnType<typeof getMappingDraft>>
  revisions: Awaited<ReturnType<typeof listMappingRevisions>>
  recommendations: Awaited<ReturnType<typeof getMappingRecommendations>>
}

type Props = {
  calculatorKey: string
  versionId?: string
  currentMapping?: MappingContext
  chosenDigest?: string
  mappingDigest?: string
  setMappingDigest: (value?: string) => void
  slots?: ContractInputSlot[]
  slotChoices: Record<string, string>
  setSlotChoices: (choices: Record<string, string>) => void
  inputCheck?: Awaited<ReturnType<typeof preflightContractInputs>>
  setInputCheck: (value: undefined) => void
  checking: boolean
  onRefresh: () => void
  onCheck: () => void
}

const { Text } = Typography

const inputLabels: Record<string, string> = {
  'document.title': '标题',
  'document.body.html': 'HTML 正文',
  'document.body.plain': '纯文本正文',
  'policy.type': '政策类型',
  'policy.policytype': '政策分类',
  'policy.releaseunit': '发布单位',
  'policy.document_type': '公文文种',
  'policy.filenumber': '发文字号',
  'policy.issue': '文号补充字段',
  'policy.organization': '发文机关',
  'policy.publishunit': '发文单位',
  'policy.attachurl': '附件链接',
  'policy.format_original_verified': '原文已核验标记',
  'policy.format_standard_scope_verified': '规范适用范围已核验标记',
}

const inputLabel = (slot: ContractInputSlot) => inputLabels[slot.semantic] ?? slot.name

export default function ContractInputEditor({
  calculatorKey, versionId, currentMapping, chosenDigest, mappingDigest, setMappingDigest, slots,
  slotChoices, setSlotChoices, inputCheck, setInputCheck, checking, onRefresh, onCheck,
}: Props) {
  const availableSlots = inputCheck?.slots ?? slots ?? []
  const policyTitle = ['policy_document_format', 'policy_effectiveness'].includes(calculatorKey)
  const requiresBody = policyTitle || ['policy_structure_compliance', 'content_consistency'].includes(calculatorKey)
  const requiresText = ['sensitive_dictionary_match', 'structured_sensitive_element_regex'].includes(calculatorKey)
  const alternatives = availableSlots.filter(slot => slot.selectable && (
    (requiresBody && ['html', 'plain'].includes(slot.name)) ||
    (requiresText && ['title', 'html', 'plain'].includes(slot.name))
  ))
  const individualSlots = availableSlots.filter(slot => !alternatives.includes(slot))
  const required = (slot: ContractInputSlot) => slot.required || (policyTitle && slot.name === 'title')
  const requiredSlots = individualSlots.filter(required)
  const optionalSlots = individualSlots.filter(slot => !required(slot))
  const bindings = currentMapping?.revisions.find(({ digest }) => digest === chosenDigest)?.revision.bindings ?? []
  const optionsFor = (slot: ContractInputSlot) => [
    ...(currentMapping?.recommendations.fields ?? []).map(({ name }) => ({ value: `field:${name}`, label: name })),
    ...bindings.filter(entry => entry.representation === slot.representation).map(({ semantic, field_tokens }) => ({
      value: `semantic:${semantic}`, label: `${field_tokens.join(' / ')}（语义映射 · ${semantic}）`,
    })),
  ]

  const applyChoices = (next: Record<string, string>) => {
    setSlotChoices(next)
    setInputCheck(undefined)
  }
  const renderSlot = (slot: ContractInputSlot) => {
    const binding = inputCheck?.plan?.slots.find(entry => entry.slot.name === slot.name)?.binding
      ?? (!slot.selectable ? bindings.find(entry => entry.semantic === slot.semantic && entry.representation === slot.representation) : undefined)
    return <Space key={slot.name} orientation="vertical" size={4} style={{ display: 'flex' }}>
      <Text strong>{inputLabel(slot)}{required(slot) && <Text type="danger"> · 必填</Text>}</Text>
      {slot.selectable ? <Select aria-label={inputLabel(slot)} placeholder={`选择${inputLabel(slot)}字段`}
        allowClear showSearch={{ optionFilterProp: 'label' }} style={{ width: '100%', maxWidth: 460 }}
        value={slotChoices[slot.name]} options={optionsFor(slot)} onChange={(value) => {
          const next = { ...slotChoices }
          if (value) next[slot.name] = value
          else delete next[slot.name]
          applyChoices(next)
        }} />
        : <Text type="secondary">{binding ? binding.field_tokens.join(' / ') : '由语义映射提供，当前未绑定'}</Text>}
    </Space>
  }

  const alternativeLabel = requiresText ? '待检测文本' : '正文'
  const selectedAlternatives = alternatives.filter(slot => slotChoices[slot.name])
    .map(slot => JSON.stringify([slot.name, slotChoices[slot.name]]))

  return <Card size="small" title="评测输入">
    <Space orientation="vertical" style={{ width: '100%' }} size="middle">
      {requiredSlots.map(renderSlot)}
      {alternatives.length > 0 && <Space orientation="vertical" size={4} style={{ display: 'flex' }}>
        <Text strong>{alternativeLabel}<Text type="danger"> · 至少选一项</Text></Text>
        <Select aria-label={alternativeLabel} placeholder={requiresText ? '选择标题或正文字段' : '选择 HTML 或纯文本正文字段'}
          allowClear showSearch={{ optionFilterProp: 'label' }} style={{ width: '100%', maxWidth: 460 }}
          mode="multiple" value={selectedAlternatives}
          options={alternatives.map(slot => ({
            label: inputLabel(slot),
            options: optionsFor(slot).map(option => ({
              value: JSON.stringify([slot.name, option.value]),
              label: `${option.label} · ${inputLabel(slot)}`,
            })),
          }))}
          onChange={(value: string[]) => {
            const next = { ...slotChoices }
            for (const slot of alternatives) delete next[slot.name]
            for (const selection of value) {
              const [name, choice] = JSON.parse(selection) as [string, string]
              next[name] = choice
            }
            applyChoices(next)
          }} />
      </Space>}
      {optionalSlots.length > 0 && <Collapse size="small"
        defaultActiveKey={requiredSlots.length || alternatives.length ? [] : ['optional']}
        items={[{ key: 'optional', label: '补充输入（可选）', children: <Space orientation="vertical" style={{ width: '100%' }}>
          {optionalSlots.map(renderSlot)}
        </Space> }]} />}
      <Collapse size="small" items={[{ key: 'mapping', label: '语义映射设置', children: <Space wrap>
        <Select aria-label="映射修订" placeholder="当前默认修订" allowClear style={{ minWidth: 210 }}
          value={mappingDigest} onChange={(value) => { setMappingDigest(value); setInputCheck(undefined) }}
          options={currentMapping?.revisions.map(({ digest }) => ({
            value: digest, label: digest.slice(7, 19) + (digest === currentMapping.draft.default_digest ? '（默认）' : ''),
          }))} />
        <Typography.Link href={`/app/datasets?rawVersion=${versionId}`} target="_blank" rel="noopener">打开语义映射</Typography.Link>
        <Button onClick={onRefresh}>刷新修订</Button>
      </Space> }]} />
      <Button loading={checking} onClick={onCheck}>输入预检</Button>
      {inputCheck && <Alert type={inputCheck.status === 'READY' ? 'success' : 'warning'}
        title={inputCheck.status === 'READY' ? '输入已就绪' : inputCheck.status} description={inputCheck.detail} />}
    </Space>
  </Card>
}

export function inputSelectors(choices: Record<string, string>) {
  return Object.fromEntries(Object.entries(choices).map(([name, value]) => [
    name, value.startsWith('field:') ? { field_tokens: [value.slice(6)] } : { semantic: value.slice(9) },
  ]))
}

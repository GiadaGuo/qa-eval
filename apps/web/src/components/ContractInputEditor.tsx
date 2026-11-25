import { Alert, Button, Card, Select, Space, Typography } from 'antd'
import type { getMappingDraft, listMappingRevisions, getMappingRecommendations } from '../api/datasetVersions'
import type { ContractInputSlot, preflightContractInputs } from '../api/qualityContracts'

export type MappingContext = {
  draft: Awaited<ReturnType<typeof getMappingDraft>>
  revisions: Awaited<ReturnType<typeof listMappingRevisions>>
  recommendations: Awaited<ReturnType<typeof getMappingRecommendations>>
}

type Props = {
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

export default function ContractInputEditor({
  versionId, currentMapping, chosenDigest, mappingDigest, setMappingDigest, slots,
  slotChoices, setSlotChoices, inputCheck, setInputCheck, checking, onRefresh, onCheck,
}: Props) {
  return <Card size="small" title="评测输入">
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Space wrap>
        <Select aria-label="映射修订" placeholder="当前默认修订" allowClear style={{ minWidth: 210 }}
          value={mappingDigest} onChange={(value) => { setMappingDigest(value); setInputCheck(undefined) }}
          options={currentMapping?.revisions.map(({ digest }) => ({
            value: digest, label: digest.slice(7, 19) + (digest === currentMapping.draft.default_digest ? '（默认）' : ''),
          }))} />
        <Typography.Link href={`/app/datasets?rawVersion=${versionId}`} target="_blank" rel="noopener">打开语义映射</Typography.Link>
        <Button onClick={onRefresh}>刷新修订</Button>
        <Button loading={checking} onClick={onCheck}>输入预检</Button>
      </Space>
      <Text>当前映射 {chosenDigest?.slice(7, 19) ?? '未设置'}</Text>
      {inputCheck && <Alert type={inputCheck.status === 'READY' ? 'success' : 'warning'}
        title={inputCheck.status === 'READY' ? '输入已就绪' : inputCheck.status} description={inputCheck.detail} />}
      {(inputCheck?.slots ?? slots ?? []).map((slot) => {
        const bindings = currentMapping?.revisions.find(({ digest }) => digest === chosenDigest)?.revision.bindings ?? []
        const binding = inputCheck?.plan?.slots.find((entry) => entry.slot.name === slot.name)?.binding
          ?? (!slot.selectable ? bindings.find((entry) => entry.semantic === slot.semantic && entry.representation === slot.representation) : undefined)
        return <Space key={slot.name} wrap>
          <Text>{slot.name} · {slot.semantic} · {slot.representation} · {slot.required ? '必需' : '可选'}</Text>
          {slot.selectable && <Select aria-label={`选择输入 ${slot.name}`} allowClear style={{ minWidth: 210 }}
            value={slotChoices[slot.name]} onChange={(value) => {
              const next = { ...slotChoices }
              if (value) next[slot.name] = value
              else delete next[slot.name]
              setSlotChoices(next)
              setInputCheck(undefined)
            }} options={[
              ...(currentMapping?.recommendations.fields ?? []).map(({ name }) => ({ value: `field:${name}`, label: `原始 · ${name}` })),
              ...bindings.filter((entry) => entry.representation === slot.representation).map(({ semantic }) => ({ value: `semantic:${semantic}`, label: `语义 · ${semantic}` })),
            ]} />}
          <Text type="secondary">{binding ? [binding.field_tokens.join(' / '), binding.transform].filter(Boolean).join(' → ') : '未绑定'}</Text>
        </Space>
      })}
    </Space>
  </Card>
}

export function inputSelectors(choices: Record<string, string>) {
  return Object.fromEntries(Object.entries(choices).map(([name, value]) => [
    name, value.startsWith('field:') ? { field_tokens: [value.slice(6)] } : { semantic: value.slice(9) },
  ]))
}

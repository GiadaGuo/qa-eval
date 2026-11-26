import ContractPublishConfirmation from '../components/ContractPublishConfirmation'
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getMappingDraft, getMappingRecommendations, listMappingRevisions, type DatasetVersion } from '../api/datasetVersions'
import { fetchBindings, fetchCalculators, fetchMetrics, type CalculatorDescriptor, type MetricCalculatorBinding, type MetricDefinition } from '../api/registry'
import { preflightContractInputs, prepareQualityContract, type ContractItemDraft, type ManualContractRequest, type PublishedQualityContract, type RuleOperator } from '../api/qualityContracts'
import { createEvaluationRun } from '../api/evaluationRuns'
import ContractInputEditor, { inputSelectors, type MappingContext } from '../components/ContractInputEditor'
import ParameterInput from '../components/ParameterInput'
import { calculatorDefaults, updateCalculatorParameters } from '../utils/calculatorDefaults'

const metricCodes = Array.from({ length: 8 }, (_, index) => `020${index + 1}`)
type Operator = { id: string; metric: MetricDefinition; binding: MetricCalculatorBinding; calculator: CalculatorDescriptor }
type Item = Pick<ContractItemDraft, 'parameters' | 'measurement' | 'rule' | 'mapping_digest'> & { id: string; choices: Record<string, string> }
type Draft = { name: string; selected: Record<string, Item[]>; excluded: Record<string, string> }
type Check = Awaited<ReturnType<typeof preflightContractInputs>>

function measurements(operator: Operator) {
  const { binding, calculator, metric } = operator
  if (calculator.native_output) return calculator.native_output.statistics
    .filter(value => ['count', 'ratio', 'scalar'].includes(value.kind))
    .map(value => ({ ...value, value_type: 'NUMBER', direction: '' }))
  return metric.measurements.filter(value => binding.compatibility?.primary_measurement
    ? binding.compatibility.primary_measurement === value.key
    : !binding.produced_measurements?.length || binding.produced_measurements.includes(value.key))
}

function newItem(operator: Operator, mapping?: MappingContext): Item {
  const fields = measurements(operator)
  const policy = operator.binding.compatibility
  const measurement = fields.find(field => field.key === policy?.primary_measurement)
    ?? fields.find(field => field.value_type === 'NUMBER') ?? fields[0]
  return {
    id: operator.id, choices: {}, measurement: measurement?.key ?? '',
    mapping_digest: mapping?.draft.default_digest ?? undefined,
    parameters: calculatorDefaults(operator.calculator),
    rule: {
      strategy: 'THRESHOLD',
      operator: policy?.default_rule?.operator ?? (measurement?.direction === 'HIGHER_IS_BETTER' ? 'GTE' : 'LTE'),
      threshold: policy?.default_rule?.threshold,
      severity: 'ERROR', on_missing: 'UNKNOWN',
    },
  }
}

function itemPayload(versionId: string, item: Item, operator: Operator) {
  return {
    dataset_version_id: versionId, metric_key: operator.metric.metric_key, metric_version: operator.metric.version,
    calculator_key: operator.calculator.calculator_key, calculator_version: operator.calculator.version,
    mapping_digest: item.mapping_digest, input_selectors: inputSelectors(item.choices), parameters: item.parameters,
  }
}

function OperatorEditor({ item, operator, version, mapping, reloadMappings, onChange, onRemove }: {
  item: Item; operator: Operator; version: DatasetVersion; mapping?: MappingContext; reloadMappings: () => void;
  onChange: (item: Item) => void; onRemove: () => void;
}) {
  const [check, setCheck] = useState<{ item: Item; result: Check }>()
  const [busy, setBusy] = useState(false)
  const measurement = measurements(operator).find(value => value.key === item.measurement)
  const onCheck = async () => {
    setBusy(true)
    try { setCheck({ item, result: await preflightContractInputs(itemPayload(version.id, item, operator)) }) }
    catch (error) { setCheck({ item, result: { status: 'ERROR', detail: String(error) } }) }
    finally { setBusy(false) }
  }
  return <Card size="small" title={operator.calculator.name} extra={<Button danger type="link" onClick={onRemove}>移除</Button>}>
    <Form layout="vertical">
      <Space wrap align="start">
        <Form.Item label="算子版本"><Tag>{operator.calculator.version}</Tag></Form.Item>
        <Form.Item label="达标规则"><Select style={{ width: 120 }} value={item.rule.strategy === 'OBSERVE_ONLY' ? 'OBSERVE_ONLY' : item.rule.operator}
          options={[{ value: 'OBSERVE_ONLY', label: '仅观测' }, ...Object.entries({ LT: '<', LTE: '≤', GT: '>', GTE: '≥', EQ: '=', NE: '≠' })
            .filter(([key]) => measurement?.value_type === 'NUMBER' || ['EQ', 'NE'].includes(key)).map(([value, label]) => ({ value, label }))]}
          onChange={value => onChange({ ...item, rule: value === 'OBSERVE_ONLY'
            ? { strategy: value, severity: 'ERROR', on_missing: 'UNKNOWN' }
            : { ...item.rule, strategy: 'THRESHOLD', operator: value as RuleOperator } })} /></Form.Item>
        {item.rule.strategy === 'THRESHOLD' && <Form.Item label="阈值" required><ParameterInput
          schema={{ type: measurement?.value_type.toLowerCase(), minimum: measurement?.minimum, maximum: measurement?.maximum }}
          value={item.rule.threshold} onChange={threshold => onChange({ ...item, rule: { ...item.rule, threshold } })} /></Form.Item>}
      </Space>
      {version.record_schema === '3.0' ? <ContractInputEditor calculatorKey={operator.calculator.calculator_key} versionId={version.id} currentMapping={mapping}
        chosenDigest={item.mapping_digest ?? mapping?.draft.default_digest ?? undefined} mappingDigest={item.mapping_digest}
        setMappingDigest={mapping_digest => onChange({ ...item, mapping_digest })}
        slots={operator.calculator.input_contract.logical?.slots} slotChoices={item.choices}
        setSlotChoices={choices => onChange({ ...item, choices })} inputCheck={check?.item === item ? check.result : undefined}
        setInputCheck={() => setCheck(undefined)} checking={busy} onRefresh={reloadMappings} onCheck={() => void onCheck()} />
        : <Space wrap><Button loading={busy} onClick={() => void onCheck()}>输入预检</Button>
          {check?.item === item && <Tag color={check.result.status === 'READY' ? 'green' : 'red'}>{check.result.status} {check.result.detail}</Tag>}</Space>}
      <Typography.Title level={5}>运行参数</Typography.Title>
      {Object.entries(operator.calculator.parameter_schema.properties ?? {}).map(([key, schema]) => <Form.Item key={key} label={schema.title ?? key}
        required={operator.calculator.parameter_schema.required?.includes(key)}>
        <ParameterInput schema={schema} value={item.parameters[key]} onChange={value => {
          onChange({ ...item, parameters: updateCalculatorParameters(item.parameters, { [key]: value }) })
        }} />
      </Form.Item>)}
    </Form>
  </Card>
}

export default function ManualEvaluationWorkspace({ version }: { version: DatasetVersion }) {
  const storageKey = `qa-eval:manual-contract:${version.id}`
  const [draft, setDraft] = useState<Draft>(() => {
    try { const saved = sessionStorage.getItem(storageKey); if (saved) return JSON.parse(saved) as Draft } catch { /* start with an empty draft */ }
    return { name: '数据集综合质量评测契约', selected: {}, excluded: {} }
  })
  const [registry, setRegistry] = useState<{ metrics: MetricDefinition[]; operators: Operator[] }>()
  const [mapping, setMapping] = useState<MappingContext>()
  const [mappingRefresh, setMappingRefresh] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<React.Key[]>([])
  const [validated, setValidated] = useState<Draft>()
  const [preview, setPreview] = useState<{ contract: PublishedQualityContract; payload: ManualContractRequest }>()
  const [published, setPublished] = useState<PublishedQualityContract>()
  const [confirmed, setConfirmed] = useState(false)
  const [runId, setRunId] = useState('')
  useEffect(() => {
    let active = true
    Promise.all([fetchMetrics(), fetchCalculators(), fetchBindings()]).then(([metrics, calculators, bindings]) => {
      if (!active) return
      const operators = bindings.flatMap(binding => {
        const metric = metrics.find(value => value.metric_key === binding.metric_key && value.version === binding.metric_version)
        const calculator = calculators.find(value => value.calculator_key === binding.calculator_key && value.version === binding.calculator_version)
        if (!metric || !calculator || !binding.enabled || calculator.availability.status !== 'AVAILABLE' || !calculator.entrypoint) return []
        return [{ id: [metric.metric_key, metric.version, calculator.calculator_key, calculator.version].join('@'), metric, binding, calculator }]
      })
      setRegistry({ metrics, operators })
    }).catch(failure => { if (active) setError(String(failure)) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (version.record_schema !== '3.0') return
    const controller = new AbortController()
    Promise.all([getMappingDraft(version.id, controller.signal), listMappingRevisions(version.id, 0, controller.signal),
      getMappingRecommendations(version.id, controller.signal)]).then(([draft, revisions, recommendations]) => {
      if (!controller.signal.aborted) setMapping({ draft, revisions, recommendations })
    }).catch(failure => { if (!controller.signal.aborted) setError(String(failure)) })
    return () => controller.abort()
  }, [version.id, version.record_schema, mappingRefresh])

  const update = (next: Draft) => { setDraft(next); setError(''); setNotice(''); setPreview(undefined); setPublished(undefined); setRunId('') }
  const changeItems = (code: string, items: Item[]) => update({ ...draft, selected: { ...draft.selected, [code]: items } })
  const getOperator = (id: string) => registry?.operators.find(value => value.id === id)
  const checkConfiguration = async (showPreview: boolean) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const payload: ManualContractRequest = { name: draft.name.trim(), dataset_version_id: version.id,
        version_label: 'manual-v1', excluded_metrics: draft.excluded, items: [], observation_items: [] }
      for (const item of Object.values(draft.selected).flat()) {
        const operator = getOperator(item.id)
        if (!operator) throw new Error(`Registry 中找不到所选算子 ${item.id}`)
        const inputs = itemPayload(version.id, item, operator)
        if (item.rule.strategy === 'THRESHOLD' && (item.rule.threshold == null || item.rule.threshold === '')) {
          throw new Error(`请填写 ${operator.calculator.name} 的阈值`)
        }
        if (operator.calculator.native_output) {
          const base = { metric_key: inputs.metric_key, metric_version: inputs.metric_version, key: item.id,
            calculator_key: inputs.calculator_key, calculator_version: inputs.calculator_version, parameters: item.parameters, projections: [] as [] }
          payload.observation_items.push(item.rule.strategy === 'OBSERVE_ONLY'
            ? { ...base, schema_version: '1.0', strategy: 'OBSERVE_ONLY' }
            : { ...base, schema_version: '1.1', strategy: 'EVALUATE', severity: item.rule.severity,
              statistic_ref: { contract_item_key: item.id, output_slot_id: item.id, calculator_version: inputs.calculator_version, statistics_key: item.measurement },
              rule: { operator: item.rule.operator!, threshold: item.rule.threshold } })
        } else payload.items.push({ ...inputs, key: item.id, measurement: item.measurement, rule: item.rule })
      }
      const contract = await prepareQualityContract(payload)
      for (const item of payload.items) {
        item.mapping_digest = contract.contract.items.find(value => value.key === item.key)?.calculation?.implementation_constraints.attributes.mapping_digest
      }
      setValidated(draft)
      setNotice('后端配置校验通过')
      if (showPreview) { setConfirmed(false); setPreview({ contract, payload }) }
    } catch (failure) { setValidated(undefined); setError(String(failure)) }
    finally { setBusy(false) }
  }
  const complete = (code: string) => Boolean((draft.selected[code]?.length && validated === draft) || draft.excluded[code]?.trim())
  const selectedCount = Object.values(draft.selected).flat().length
  const pending = metricCodes.filter(code => !complete(code)).length

  return <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
    {error && <Alert type="error" showIcon title={error} />}
    {notice && <Alert type="success" showIcon title={notice} />}
    <Table rowKey="code" size="small" pagination={false} loading={!registry && !error} scroll={{ x: 760 }}
      dataSource={metricCodes.map(code => ({
        code, items: draft.selected[code] ?? [],
        name: registry?.metrics.find(metric => metric.metric_key === code)?.name,
        operators: registry?.operators.filter(value =>
          (value.metric.parent_metric_key ?? value.metric.metric_key) === code
          && Boolean(value.calculator.input_contract.logical) === (version.record_schema === '3.0')),
      }))}
      expandable={{ expandedRowKeys: expanded, onExpandedRowsChange: keys => setExpanded([...keys]),
        expandedRowRender: ({ code, items }) => <Space orientation="vertical" style={{ width: '100%' }}>
          {code in draft.excluded ? <Input aria-label="不参与评测原因" placeholder="填写不参与评测原因" value={draft.excluded[code]}
            onChange={event => update({ ...draft, excluded: { ...draft.excluded, [code]: event.target.value } })} />
            : items.map(item => {
              const operator = getOperator(item.id)
              return operator ? <OperatorEditor key={item.id} item={item} operator={operator} version={version} mapping={mapping}
                reloadMappings={() => setMappingRefresh(value => value + 1)}
                onChange={next => changeItems(code, items.map(value => value.id === item.id ? next : value))}
                onRemove={() => changeItems(code, items.filter(value => value.id !== item.id))} />
                : <Alert key={item.id} type="error" title={item.id} action={<Button onClick={() => changeItems(code, items.filter(value => value.id !== item.id))}>移除</Button>} />
            })}
        </Space> }}
      columns={[
        { title: '指标编码及名称', render: (_, { code, name }) => <Space><Tag color="blue">{code}</Tag>{name}</Space> },
        { title: '配置状态', render: (_, { code, items }) => code in draft.excluded
          ? <Tag color={complete(code) ? 'default' : 'orange'}>不参与评测</Tag>
          : <Tag color={complete(code) ? 'green' : items.length ? 'orange' : 'default'}>{complete(code) ? '已配置' : items.length ? '待完善' : '未配置'}</Tag> },
        { title: '已选算子', render: (_, { items }) => items.map(item => <Tag key={item.id}>{getOperator(item.id)?.calculator.name ?? item.id}</Tag>) },
        { title: '操作', render: (_, { code, items, operators }) => <Space wrap>
          {!(code in draft.excluded) && (operators?.length === 0
            ? <Typography.Text type="secondary">{version.record_schema === '3.0'
              ? '暂无适用于 SampleRecord v3 的算子' : '暂无适用于当前版本的算子'}</Typography.Text>
            : <Select aria-label={`${code} 选择算子`} mode="multiple" value={items.map(item => item.id)}
            maxTagCount={0} maxTagPlaceholder={() => <><PlusOutlined /> 选择算子</>} placeholder={<><PlusOutlined /> 选择算子</>}
            disabled={!registry} style={{ minWidth: 145 }} popupMatchSelectWidth={360} showSearch optionFilterProp="label"
            notFoundContent="没有匹配的可用算子"
            options={operators?.map(value => ({
              value: value.id, label: `${value.calculator.name} · ${value.calculator.version}（${value.metric.metric_key} · ${value.metric.version}）`,
              disabled: items.some(item => {
                const other = getOperator(item.id)
                return other && other.id !== value.id && (
                  (other.calculator.calculator_key === value.calculator.calculator_key) ||
                  (Boolean(value.binding.compatibility?.operator_slot) && other.binding.compatibility?.operator_slot === value.binding.compatibility?.operator_slot))
              }),
            }))}
            onChange={ids => { changeItems(code, ids.map(id => items.find(item => item.id === id) ?? newItem(getOperator(id)!, mapping))); setExpanded(keys => [...new Set([...keys, code])]) }} />)}
          <Button type="link" disabled={items.length > 0} onClick={() => {
            const excluded = { ...draft.excluded }
            if (code in excluded) delete excluded[code]
            else { excluded[code] = ''; setExpanded(keys => [...new Set([...keys, code])]) }
            update({ ...draft, excluded })
          }}>{code in draft.excluded ? '恢复评测' : '不参与评测'}</Button>
        </Space> },
      ]} />
    <Card size="small" style={{ position: 'sticky', bottom: 0, zIndex: 2, background: '#f0f5ff' }}>
      <Space wrap>
        <Typography.Text>指标覆盖 {metricCodes.filter(code => draft.selected[code]?.length).length}/8 · 已选算子 {selectedCount} · 待完善 {pending}</Typography.Text>
        <Button onClick={() => {
          try { sessionStorage.setItem(storageKey, JSON.stringify(draft)); setNotice('草稿已保存，可在当前标签页刷新后继续编辑') }
          catch (failure) { setError(String(failure)) }
        }}>保存草稿</Button>
        <Button loading={busy} onClick={() => void checkConfiguration(false)}>配置校验</Button>
        <Button type="primary" loading={busy} onClick={() => void checkConfiguration(true)}>预览质量契约</Button>
      </Space>
    </Card>
    <Modal title="质量契约预览" open={Boolean(preview)} width={850} onCancel={() => setPreview(undefined)} footer={null}>
      {preview && <Space orientation="vertical" style={{ width: '100%' }}>
        {error && <Alert type="error" title={error} />}
        {published ? <>
          <Alert type="success" title="多指标质量契约已发布" description={published.contract.contract_version_id} />
          {runId ? <Alert type="info" title={`评测任务 ${runId}`} /> : <Button type="primary" loading={busy} onClick={async () => {
            setBusy(true)
            try { setRunId((await createEvaluationRun(published.contract.contract_version_id)).run_id); setError('') }
            catch (failure) { setError(String(failure)) }
            finally { setBusy(false) }
          }}>提交真实评测任务</Button>}
        </> : <>
          <Input aria-label="契约名称" maxLength={255} value={preview.payload.name} onChange={event =>
            setPreview({ ...preview, payload: { ...preview.payload, name: event.target.value } })} />
          <pre style={{ overflow: 'auto', maxHeight: '45vh' }}>{JSON.stringify({
            ...preview.contract.contract, name: preview.payload.name, dataset_version_id: version.id,
          }, null, 2)}</pre>
          <ContractPublishConfirmation confirmed={confirmed} onConfirm={setConfirmed}
            disabled={!preview.payload.name.trim()} loading={busy} onPublish={async () => {
            setBusy(true)
            try { setPublished(await prepareQualityContract({ ...preview.payload, name: preview.payload.name.trim() }, true)); setError('') }
            catch (failure) { setError(String(failure)) }
            finally { setBusy(false) }
          }}>我已核对当前评测方案，确认发布 {selectedCount} 个评测项</ContractPublishConfirmation>
        </>}
      </Space>}
    </Modal>
  </Space>
}

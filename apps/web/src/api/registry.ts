export interface Measurement { key: string; value_type: string; unit?: string; minimum?: number; maximum?: number; direction: string; interpretation?: string }
export interface MetricDefinition { metric_key: string; version: string; name: string; dimension: string; applicability: { modalities: string[]; required_roles: string[] }; measurements: Measurement[]; definition: { requirement: string; limitations: string[] } }
export interface CalculatorDescriptor { calculator_key: string; version: string; name: string; entrypoint: string | null; input_contract: { sample_record_schema: string; role_selectors: string[] }; parameter_schema: { properties?: Record<string, { type?: string; default?: unknown; minimum?: number; maximum?: number }> }; output_schema_ref: string; deterministic: boolean; capabilities: { produces_evidence: boolean }; availability: { status: string; reason?: string | null } }
export interface MetricCalculatorBinding { metric_key: string; metric_version: string; calculator_key: string; calculator_version: string; priority: number; enabled: boolean; produced_measurements?: string[] }
export interface RegistrySyncResult { metrics: number; calculators: number; bindings: number; repaired_calculators: unknown[] }
async function get<T>(path: string): Promise<T> { const response = await fetch(path); if (!response.ok) throw new Error(`Registry 请求失败：HTTP ${response.status}`); return response.json() as Promise<T> }
async function post<T>(path: string): Promise<T> { const response = await fetch(path, { method: 'POST' }); if (!response.ok) throw new Error(`Registry 同步失败：HTTP ${response.status}`); return response.json() as Promise<T> }
export const fetchMetrics = () => get<MetricDefinition[]>('/api/v1/metrics')
export const fetchCalculators = () => get<CalculatorDescriptor[]>('/api/v1/calculators')
export const fetchBindings = () => get<MetricCalculatorBinding[]>('/api/v1/metric-calculator-bindings')
export const syncRegistry = () => post<RegistrySyncResult>('/api/v1/registry/sync')

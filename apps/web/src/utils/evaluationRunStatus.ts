import type { EvaluationRun } from '../api/evaluationRuns'

export type EvaluationRunStatus = EvaluationRun['status']

export const TERMINAL_EVALUATION_RUN_STATUSES = new Set<EvaluationRunStatus>([
  'PARTIAL_SUCCEEDED',
  'SUCCEEDED',
  'FAILED',
  'CANCELED',
])

export const isEvaluationRunTerminal = (status: EvaluationRunStatus): boolean =>
  TERMINAL_EVALUATION_RUN_STATUSES.has(status)

export const shouldAutoPollEvaluationRun = (run: EvaluationRun): boolean =>
  !isEvaluationRunTerminal(run.status)
  && typeof run.execution.worker_id === 'string'
  && run.execution.worker_id.length > 0

export const evaluationRunStatusLabel = (status: EvaluationRunStatus): string => ({
  PENDING: '等待中',
  RUNNING: '运行中',
  PARTIAL_SUCCEEDED: '部分成功',
  SUCCEEDED: '评测完成',
  FAILED: '评测失败',
  CANCEL_REQUESTED: '取消中',
  CANCELED: '已取消',
})[status]

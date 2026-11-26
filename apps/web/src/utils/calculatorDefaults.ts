import type { CalculatorDescriptor } from '../api/registry'

export function calculatorDefaults(calculator: CalculatorDescriptor): Record<string, unknown> {
  return Object.fromEntries(Object.entries(calculator.parameter_schema.properties ?? {})
    .filter(([, property]) => property.default !== undefined)
    .map(([key, property]) => [key, property.default]))
}

export function updateCalculatorParameters(parameters: Record<string, unknown>, changes: Record<string, unknown>) {
  const next = { ...parameters, ...changes }
  if (Array.isArray(changes.dictionary) && changes.dictionary.length) delete next.dictionary_path
  if (typeof changes.dictionary_path === 'string' && changes.dictionary_path.trim()) delete next.dictionary
  if (next.dictionary_path === '') delete next.dictionary_path
  return Object.fromEntries(Object.entries(next).filter(([, value]) => value != null))
}

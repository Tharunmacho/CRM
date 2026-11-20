import { readFileSync } from 'node:fs'
import ts from 'typescript'

/**
 * Tests load single source files as data: modules, where the app's `@/` alias cannot resolve. Any module that
 * imports the shared role helpers (`@/shared/model/role-labels`: roleLabel, isAdminLevel, ...) or the shared phone
 * helpers (`@/shared/lib/phone`: waDigits, formatPhone, ...) is run through `withRoleLabels`, which points those
 * imports at the real files, each transpiled once.
 */
const dataUrl = (relative) => {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}

export const roleLabelsUrl = dataUrl('../../src/shared/model/role-labels.ts')
export const phoneUrl = dataUrl('../../src/shared/lib/phone.ts')
export const withRoleLabels = (js) => js
  .replace(/(['"])@\/shared\/model\/role-labels\1/g, `'${roleLabelsUrl}'`)
  .replace(/(['"])@\/shared\/lib\/phone\1/g, `'${phoneUrl}'`)

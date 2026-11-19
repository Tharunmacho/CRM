import { readFileSync } from 'node:fs'
import ts from 'typescript'

/**
 * Tests load single source files as data: modules, where the app's `@/` alias cannot resolve. Any module that
 * imports the shared role helpers (`@/shared/model/role-labels`: roleLabel, isAdminLevel, ...) is run through
 * `withRoleLabels`, which points that import at the real file, transpiled once.
 */
const source = readFileSync(new URL('../../src/shared/model/role-labels.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })

export const roleLabelsUrl = `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
export const withRoleLabels = (js) => js.replace(/(['"])@\/shared\/model\/role-labels\1/g, `'${roleLabelsUrl}'`)

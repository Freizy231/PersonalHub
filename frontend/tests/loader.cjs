// Real on-demand module loader for Vue SFCs and TS modules used by the tests.
// It resolves real relative imports so tests mount the same component graph the
// app does, instead of hand-written stub maps that drift from the source.
const { readFileSync, existsSync } = require('node:fs')
const { dirname, join, resolve } = require('node:path')
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript')
const Vue = require('vue')

const SRC = resolve(__dirname, '..', 'src')
const cache = new Map()

function transpile(source, filename) {
  return ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText
}

function resolveFile(specifier, fromDir) {
  const base = specifier.startsWith('.') ? resolve(fromDir, specifier) : null
  if (!base) return null
  for (const candidate of [base, base + '.ts', base + '.vue', join(base, 'index.ts')]) {
    if (existsSync(candidate) && !candidate.endsWith('/')) return candidate
  }
  return null
}

function makeRequire(fromDir, overrides) {
  return function localRequire(specifier) {
    if (specifier in overrides) return overrides[specifier]
    if (specifier === 'vue') return Vue
    if (specifier === 'vue-router') return require('vue-router')
    const file = resolveFile(specifier, fromDir)
    if (!file) throw new Error(`Unresolved test import "${specifier}" from ${fromDir}`)
    return loadModule(file, overrides)
  }
}

/**
 * Mounts a real .vue or .ts module. `overrides` replaces individual import
 * specifiers (e.g. the shared api transport) without listing the whole graph.
 */
function loadModule(file, overrides = {}) {
  // Cache by file so every importer shares one module instance (stores, refs).
  // Overrides are test-wide constants, so this cannot leak state between tests.
  const key = file
  if (cache.has(key)) return cache.get(key)

  const dir = dirname(file)
  const localRequire = makeRequire(dir, overrides)

  if (file.endsWith('.vue')) {
    const source = readFileSync(file, 'utf8')
    const { descriptor } = parse(source, { filename: file })
    if (!descriptor.script && !descriptor.scriptSetup) {
      throw new Error(`Component without <script>: ${file}`)
    }
    const compiled = compileScript(descriptor, { id: file, inlineTemplate: true, templateOptions: { compilerOptions: { hoistStatic: false } } })
    const code = transpile(compiled.content, file + '.ts')
    const module = { exports: {} }
    new Function('require', 'module', 'exports', code)(localRequire, module, module.exports)
    cache.set(key, module.exports)
    return module.exports
  }

  const code = transpile(readFileSync(file, 'utf8'), file)
  const module = { exports: {} }
  new Function('require', 'module', 'exports', '__filename', code)(localRequire, module, module.exports, file)
  cache.set(key, module.exports)
  return module.exports
}

function load(path, overrides) {
  return loadModule(resolve(SRC, path), overrides)
}

module.exports = { load, loadModule, SRC }

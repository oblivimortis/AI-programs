#!/usr/bin/env node
/**
 * sync-persona.mjs — 把 dsh-tavern 的角色卡同步成 dsh-companion 的人设卡。
 *
 * 方向：tavern -> companion（单向）。
 * 理由：AGENTS.md 约定酒馆的角色卡/世界书是唯一真源，companion 只是另一套
 * 注入通道，所以以酒馆为准，避免两边各改一份。
 *
 * 用法：
 *   node sync-persona.mjs              # 生成/更新卡片，不改变当前激活的卡
 *   node sync-persona.mjs --dry-run    # 只看会做什么
 *   node sync-persona.mjs --activate   # 顺带把第一张卡设为全局生效
 *   node sync-persona.mjs --no-first   # systemPrompt 只放 desc，不含开场白
 *
 * 环境变量：
 *   DSH_HOME       默认 ~/.dsh
 *   TAVERN_PRESET  默认 tavern-lite
 */
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import crypto from 'node:crypto'

const argv = new Set(process.argv.slice(2))
const DRY = argv.has('--dry-run')
const ACTIVATE = argv.has('--activate')
const WITH_FIRST = !argv.has('--no-first')

const HOME = (process.env.DSH_HOME || '').trim() || path.join(os.homedir(), '.dsh')
const PRESET = (process.env.TAVERN_PRESET || 'tavern-lite').trim()
const charsFile = path.join(HOME, '.agent-presets', PRESET, 'characters.json')
const storeDir = path.join(HOME, 'persona-cards')
const indexFile = path.join(storeDir, 'index.json')

function readJson (file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return fallback
  }
}

function writeAtomic (file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const tmp = `${file}.tmp-${process.pid}-${Date.now()}`
  fs.writeFileSync(tmp, text, 'utf8')
  fs.renameSync(tmp, file)
}

/** 稳定 id：名字不变则 id 不变，增删角色不会让别的卡错位。 */
function cardId (name) {
  const h = crypto.createHash('md5').update(name, 'utf8').digest('hex').slice(0, 8)
  return `tavern-${h}`
}

const chars = readJson(charsFile, null)
if (!Array.isArray(chars)) {
  console.error(`[x] 读不到角色卡文件：${charsFile}`)
  process.exit(1)
}

const enabled = chars.filter(c => c && typeof c.name === 'string' && c.name.trim() && c.enabled !== false)
if (enabled.length === 0) {
  console.log(`[!] ${charsFile} 里没有启用中的角色，什么都没做。`)
  process.exit(0)
}

const made = []
for (const c of enabled) {
  const name = c.name.trim()
  const id = cardId(name)

  const parts = []
  if (typeof c.desc === 'string' && c.desc.trim()) parts.push(c.desc.trim())
  if (WITH_FIRST && typeof c.first === 'string' && c.first.trim()) {
    parts.push(`【开场白参考】\n${c.first.trim()}`)
  }
  const systemPrompt = parts.join('\n\n')

  if (!systemPrompt) {
    console.log(`[!] 跳过「${name}」：desc 和 first 都是空的`)
    continue
  }

  const card = {
    id,
    name,
    systemPrompt,
    trigger: '切回普通模式',
    note: `同步自 dsh-tavern（${PRESET}）`,
    assets: {}
  }

  made.push(card)

  const file = path.join(storeDir, `${id}.json`)
  const existed = fs.existsSync(file)
  if (!DRY) writeAtomic(file, JSON.stringify(card, null, 2) + '\n')

  const tag = existed ? '更新' : '新建'
  console.log(`${DRY ? '[dry] ' : ''}${tag}  ${id}  ${name}  (${systemPrompt.length} 字)`)
}

if (ACTIVATE && made.length > 0) {
  const idx = readJson(indexFile, { version: 1, activeId: null })
  const next = { version: 1, activeId: made[0].id }
  if (!DRY) writeAtomic(indexFile, JSON.stringify(next, null, 2) + '\n')
  console.log(`${DRY ? '[dry] ' : ''}已把「${made[0].name}」设为全局生效（activeId=${made[0].id}）`)
}

console.log(`\n${DRY ? '将生成' : '已生成'} ${made.length} 张卡 -> ${storeDir}`)
if (!ACTIVATE) {
  console.log('提示：没有改动 activeId，companion 仍保持原来的启用状态（默认不激活，避免和酒馆双重注入）。')
}

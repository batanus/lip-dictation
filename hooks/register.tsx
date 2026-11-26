import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Enrollment, HistoryEntry, Meter, Personal, Phase, SetupProgress, Training } from '../types'

// Drawing state only. Whether something is really running lives in this module instance
// (`run`, `busy`, `settingUp`), so a reload that kills the child never leaves a stuck button.
const phaseAtom = atom({ plugin: 'lip-dictation', key: 'phase' } as const, 'idle' as Phase)
const installedAtom = atom({ plugin: 'lip-dictation', key: 'installed' } as const, true)
const meterAtom = atom({ plugin: 'lip-dictation', key: 'meter' } as const, {
  seconds: 0,
  maxSeconds: 20,
  levels: [],
} as Meter)
const setupAtom = atom({ plugin: 'lip-dictation', key: 'setup' } as const, { step: '', percent: null } as SetupProgress)
const enrollAtom = atom({ plugin: 'lip-dictation', key: 'enrollment' } as const, {
  phrase: '',
  recorded: 0,
  total: 0,
} as Enrollment)
const trainAtom = atom({ plugin: 'lip-dictation', key: 'training' } as const, { step: '', done: 0, total: 0 } as Training)
const menuAtom = atom({ plugin: 'lip-dictation', key: 'menu' } as const, false)
const historyAtom = atom({ plugin: 'lip-dictation', key: 'history' } as const, [] as HistoryEntry[])
const personalAtom = atom({ plugin: 'lip-dictation', key: 'personal' } as const, {
  exists: false,
  enabled: true,
  info: null,
} as Personal)
const HISTORY_PANE = 'lip-dictation-history'

// Used by the status line when the band is hidden.
const LABELS: Record<Phase, string> = {
  idle: '👄 Lip dictation',
  starting: '… Starting camera',
  recording: '● Recording: mouth your words',
  processing: '… Reading lips',
  setup: '… Installing lip dictation',
  enroll: '👄 Lip training: press the button for the next phrase',
  training: '… Training on your lips',
}
const SETUP_LABEL = '👄 Set up lip dictation'
const MIN_CLIPS = 20 // sidecar/lipread/personal.py trains from this many recorded phrases

// The recording waveform: newest sample on the right, one glyph per ~0.1 s. Every glyph stands on
// the same baseline: a still mouth is the lowest bar, movement raises it.
const WAVE_WIDTH = 24
const BASELINE = '▁'
const BARS = '▂▃▄▅▆▇█'

/** Mouth levels (0..1, null = no face) as a bottom-aligned bar line; silence is a flat ▁ baseline. */
export function waveText(levels: readonly (number | null)[]): string {
  const glyphs = levels
    .slice(-WAVE_WIDTH)
    .map(level => (level === null || level < 0.06 ? BASELINE : BARS[Math.min(6, Math.floor(level * 7))]))
  return BASELINE.repeat(WAVE_WIDTH - glyphs.length) + glyphs.join('')
}

/** What to say beside the waveform: a missing face first, then the auto-stop countdown. */
export function recordingHint(meter: Meter): { text: string; warn: boolean } | null {
  if (meter.levels.length > 0 && meter.levels[meter.levels.length - 1] === null) {
    return { text: 'face not in view', warn: true }
  }
  const left = Math.max(0, Math.ceil(meter.maxSeconds - meter.seconds))
  return left <= 5 ? { text: `stops in ${left}s`, warn: false } : null
}

export function formatTime(seconds: number): string {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

const CLAUDE_HINT =
  'Part of this prompt was dictated silently and transcribed by a lip-reading model, ' +
  'which often confuses words that look alike on the lips (e.g. "bat/pat/mat", "fine/vine", "move/remove"). ' +
  'Read these fragments for the most plausible meaning in the context of this conversation and project. ' +
  "Each fragment may list the model's other readings in brackets: the intended words may be one of them, or look like them. " +
  'If the intent is unclear, or a fragment would lead to a destructive or irreversible action, confirm with the user first. ' +
  'Lip-read fragments: '

const SETUP_QUESTION =
  'Install lip dictation? It downloads about 1.5 GB (Python packages and a 1 GB model) into ~/.cache/lip-dictation. ' +
  'The model weights come from the Auto-AVSR project and may only be used for research and non-commercial purposes. Continue?'

type SidecarEvent = {
  event?: string
  text?: string
  message?: string
  t?: number
  level?: number | null
  max_seconds?: number
  stop_file?: string
  go_file?: string
  alternatives?: string[]
  recorded?: number
  total?: number
  step?: string
  done?: number
  before?: number
  after?: number
  kept?: boolean
  held_out?: number
}
/** A sidecar run: `stopFile` (and `goFile` for a standby) arrive with its first event; a press before
 * that is remembered. `enroll` records a training phrase instead of dictating. A standby run loads the
 * model ahead and waits, `armed` false, until a press creates its go file and the camera starts. */
type Run = {
  id: number
  kind: 'dictate' | 'enroll'
  stopFile: string | null
  goFile: string | null
  stopRequested: boolean
  armed: boolean
}
type Fragment = { text: string; alternatives: string[] }

const config = { showButton: true, hintClaude: true }

/** `«best»`, then the other readings in brackets: `«best» (or «a» / «b»)`. */
export function hintFragment(fragment: Fragment): string {
  const others = fragment.alternatives.map(text => `«${text}»`).join(' / ')
  return others ? `«${fragment.text}» (or ${others})` : `«${fragment.text}»`
}
let run: Run | null = null
let standby: Run | null = null // loaded and waiting for the next press, after a first dictation
const STANDBY_SECONDS = 600 // then it exits, freeing the model's memory
let busy = false
let settingUp = false
let enrolling = false // between training phrases: the band offers the next one
let runCounter = 0
let pendingFragments: Fragment[] = []
let pluginVersion = '' // from plugin.json, shown in the settings row
let historyShown = false // reload the History pane's lines after each dictation once it was opened

async function setPhase($: EngineInterface, phase: Phase) {
  await update($, phaseAtom, () => phase)
  // The band already shows the state; the status line is for when the band is hidden.
  if (!config.showButton) $.ui.status(phase === 'idle' ? undefined : LABELS[phase])
}

async function refreshInstalled($: EngineInterface) {
  try {
    const { exitCode } = await $.process.run(['/bin/bash', `${$.plugin.root}/scripts/lipread.sh`, '--installed'])
    await update($, installedAtom, () => exitCode === 0)
  } catch {
    // keep the last known value
  }
}

/** Streams a script's stdout line by line into `onLine`; returns the tail of its stderr. */
async function runLines($: EngineInterface, argv: string[], onLine: (line: string) => Promise<void>) {
  let buffer = ''
  let stderr = ''
  for await (const { stream, text } of $.process.spawn({ argv })) {
    if (stream === 'stderr') {
      stderr = (stderr + text).slice(-2000)
      continue
    }
    buffer += text
    let newline = buffer.indexOf('\n')
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim()
      buffer = buffer.slice(newline + 1)
      if (line) await onLine(line)
      newline = buffer.indexOf('\n')
    }
  }
  if (buffer.trim()) await onLine(buffer.trim())
  return stderr.trim()
}

/** Puts dictated text at the cursor, with a space before it when it would glue onto a word. */
async function insertText($: EngineInterface, text: string, alternatives: string[]) {
  try {
    const box = await $.prompt.read()
    const before = box.text.slice(0, box.cursor)
    const separator = before && !/\s$/.test(before) ? ' ' : ''
    const filled = await $.prompt.fill({ text: separator + text, mode: 'insert' })
    if (filled.isFilled || filled.refusal === 'unanswered') {
      pendingFragments.push({ text, alternatives })
      return
    }
  } catch {
    // fall through to the clipboard
  }
  await $.ui.copy({ text }).catch(() => undefined)
  $.ui.toast(`Could not insert into the prompt (a dialog is open?). Copied to clipboard: ${text}`)
}

/** Handles one JSON event from `lipread dictate`; returns true once the run has an outcome. */
async function handleEvent($: EngineInterface, live: Run, line: string): Promise<boolean> {
  let event: SidecarEvent
  try {
    event = JSON.parse(line)
  } catch {
    return false // a library printed to stdout; ignore it
  }
  if (event.event === 'expired') return true // a standby nobody pressed for
  if (!live.armed && event.event !== 'started') return false // a standby draws nothing until pressed
  switch (event.event) {
    case 'started':
      live.stopFile = event.stop_file ?? null
      live.goFile = event.go_file ?? null
      if (live.stopRequested) await requestStop($, live)
      else if (live.armed && live.goFile) await $.fs.write(live.goFile, 'go') // pressed before it said where
      return false
    case 'recording':
      await update($, meterAtom, () => ({ seconds: 0, maxSeconds: event.max_seconds ?? 20, levels: [] }))
      await setPhase($, 'recording')
      return false
    case 'level':
      await update($, meterAtom, meter => ({
        ...meter,
        seconds: event.t ?? meter.seconds,
        levels: [...meter.levels.slice(-(WAVE_WIDTH - 1)), event.level ?? null],
      }))
      return false
    case 'processing':
      await setPhase($, 'processing')
      return false
    case 'cancelled':
      return true
    case 'empty':
    case 'error':
      if (event.message?.includes('/lip-setup')) await update($, installedAtom, () => false)
      $.ui.toast(`Lip dictation: ${event.message ?? 'nothing recognized'}`)
      return true
    case 'result':
      if (event.text) await insertText($, event.text, event.alternatives ?? [])
      return true
    case 'phrase':
      await update($, enrollAtom, () => ({
        phrase: event.text ?? '',
        recorded: event.recorded ?? 0,
        total: event.total ?? 0,
      }))
      return false
    case 'saved':
      await update($, enrollAtom, enrollment => ({ ...enrollment, recorded: event.recorded ?? enrollment.recorded + 1 }))
      return true
    case 'done':
      await update($, enrollAtom, enrollment => ({ ...enrollment, recorded: event.recorded ?? enrollment.recorded }))
      $.ui.toast('Every training phrase is recorded. Press Train.')
      return true
    default:
      return false
  }
}

/** Runs the sidecar: at once (the caller set `busy`), or as the standby with `wait`. */
async function dictate($: EngineInterface, kind: Run['kind'] = 'dictate', wait = false) {
  const live: Run = { id: ++runCounter, kind, stopFile: null, goFile: null, stopRequested: false, armed: !wait }
  if (wait) standby = live
  else run = live // before any await: a second press sees it
  try {
    if (!wait) {
      if (kind === 'enroll') await update($, enrollAtom, enrollment => ({ ...enrollment, phrase: '' })) // set by the sidecar
      await setPhase($, 'starting')
    }
    let finished = false
    const argv = ['/bin/bash', `${$.plugin.root}/scripts/lipread.sh`, kind]
    if (wait) argv.push('--wait', String(STANDBY_SECONDS))
    const stderr = await runLines($, argv, async line => {
      if (await handleEvent($, live, line)) finished = true
    })
    if (!finished && live.armed) $.ui.toast(`Lip dictation stopped unexpectedly. ${stderr.split('\n').slice(-3).join(' ')}`)
  } catch (error) {
    if (live.armed) $.ui.toast(`Lip dictation failed: ${String(error)}`)
  } finally {
    if (standby === live) standby = null
    if (run === live) {
      run = null
      busy = false
    }
    if (live.armed) {
      await setPhase($, enrolling ? 'enroll' : 'idle')
      if (kind === 'dictate') {
        if (historyShown) await loadHistory($)
        keepWarm($)
      }
    }
  }
}

/** After a dictation, load the next one ahead, so the next press only has to open the camera. */
function keepWarm($: EngineInterface) {
  if (!standby && !settingUp) void dictate($, 'dictate', true)
}

/** Turns the standby into the live run: its go file starts the camera. */
async function arm($: EngineInterface, live: Run) {
  standby = null
  run = live
  live.armed = true
  await setPhase($, 'starting')
  if (live.goFile) await $.fs.write(live.goFile, 'go') // else written when its started event arrives
}

/** Records the next training phrase (the band shows it while the camera is on). */
function recordPhrase($: EngineInterface) {
  busy = true
  void dictate($, 'enroll')
}

/** /lip-train and the band's button: start recording training phrases. */
async function startEnroll($: EngineInterface): Promise<string> {
  if (run || busy || settingUp) return 'Lip dictation is busy.'
  if (!(await read($, installedAtom))) return confirmSetup($)
  await update($, menuAtom, () => false)
  enrolling = true
  recordPhrase($)
  return (
    'Lip training: mouth each phrase shown above the prompt, then press Stop. ' +
    `Press Train after ${MIN_CLIPS} or more phrases (about 100 works best).`
  )
}

async function stopEnroll($: EngineInterface) {
  enrolling = false
  if (!run) await setPhase($, 'idle')
}

function trainedMessage(event: SidecarEvent): string {
  const percent = (rate?: number) => `${Math.round((rate ?? 0) * 100)}%`
  const change = `word errors ${percent(event.before)} → ${percent(event.after)} on ${event.held_out ?? 0} held-out phrases`
  return event.kept
    ? `Lip dictation now uses your personal model: ${change}.`
    : `Training did not improve accuracy (${change}), so nothing changed. Record more phrases and train again.`
}

/** Trains the personal adapter on the recorded phrases (several minutes). */
async function train($: EngineInterface) {
  if (run || busy || settingUp) return
  enrolling = false
  busy = true
  await update($, trainAtom, () => ({ step: 'Starting', done: 0, total: 0 }))
  await setPhase($, 'training')
  let finished = false
  try {
    const stderr = await runLines($, ['/bin/bash', `${$.plugin.root}/scripts/lipread.sh`, 'train'], async line => {
      let event: SidecarEvent
      try {
        event = JSON.parse(line)
      } catch {
        return
      }
      if (event.event === 'progress') {
        await update($, trainAtom, () => ({ step: event.step ?? '', done: event.done ?? 0, total: event.total ?? 0 }))
      } else if (event.event === 'trained') {
        finished = true
        $.ui.toast(trainedMessage(event))
        await loadPersonal($)
        await restartStandby($)
      } else if (event.event === 'error') {
        finished = true
        $.ui.toast(`Lip training: ${event.message ?? 'failed'}`)
      }
    })
    if (!finished) $.ui.toast(`Lip training stopped unexpectedly. ${stderr.split('\n').slice(-3).join(' ')}`)
  } catch (error) {
    $.ui.toast(`Lip training failed: ${String(error)}`)
  } finally {
    busy = false
    await setPhase($, 'idle')
  }
}

async function setup($: EngineInterface) {
  settingUp = true
  await update($, setupAtom, () => ({ step: 'Starting', percent: null }))
  await setPhase($, 'setup')
  let ready = false
  const errors: string[] = []
  try {
    await runLines($, ['/bin/bash', `${$.plugin.root}/scripts/setup.sh`], async line => {
      if (line.startsWith('==>')) {
        const step = line.slice(4)
        await update($, setupAtom, () => ({ step, percent: null }))
        if (!config.showButton) $.ui.status(`Lip dictation setup: ${step}`)
      }
      if (line.startsWith('@@ download ')) {
        const percent = Number(line.slice(12))
        await update($, setupAtom, progress => ({ ...progress, percent: Number.isFinite(percent) ? percent : null }))
      }
      if (line.startsWith('ERROR')) errors.push(line)
      if (line === 'Ready.') ready = true
    })
    $.ui.toast(
      ready
        ? 'Lip dictation is installed. Press the button or run /lip to dictate.'
        : `Lip dictation setup failed: ${errors.slice(-2).join(' ') || 'see ~/.cache/lip-dictation/setup.log'}`,
    )
  } catch (error) {
    $.ui.toast(`Lip dictation setup failed: ${String(error)}`)
  } finally {
    settingUp = false
    $.ui.status(undefined)
    await refreshInstalled($)
    await setPhase($, 'idle')
  }
}

/** Asks before the ~1.5 GB download and shows the model's license terms. */
async function confirmSetup($: EngineInterface): Promise<string> {
  if (busy || settingUp) return 'Lip dictation is busy.'
  let answer = ''
  try {
    answer = await $.ui.ask(SETUP_QUESTION, ['Install', 'Not now'])
  } catch {
    return 'Setup cancelled.'
  }
  if (answer !== 'Install') return 'Setup cancelled.'
  void setup($)
  return 'Installing lip dictation in the background. Progress is in the status line.'
}

async function requestStop($: EngineInterface, live: Run) {
  live.stopRequested = true
  if (!live.stopFile) return // written as soon as the sidecar says where
  try {
    await $.fs.write(live.stopFile, 'stop')
  } catch (error) {
    $.ui.toast(`Could not stop the recording: ${String(error)}`)
  }
}

const script = ($: EngineInterface) => `${$.plugin.root}/scripts/lipread.sh`

/** Reads the last dictations from the sidecar's history file into the History pane. */
async function loadHistory($: EngineInterface) {
  try {
    const { stdout } = await $.process.run(['/bin/bash', script($), '--history', '100'])
    const entries = stdout.split('\n').flatMap(line => {
      try {
        return line.trim() ? [JSON.parse(line) as HistoryEntry] : []
      } catch {
        return []
      }
    })
    await update($, historyAtom, () => entries)
  } catch {
    // keep what the pane shows
  }
}

async function openHistory($: EngineInterface) {
  await update($, menuAtom, () => false)
  historyShown = true
  await loadHistory($)
  const { isPlaced } = await $.ui.open({ id: HISTORY_PANE, title: 'Lip dictation history' })
  if (!isPlaced) $.ui.toast('The history is in ~/.cache/lip-dictation/dictations.jsonl (scripts/lipread.sh --history).')
}

function localTime(): string {
  const now = new Date()
  const two = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())} ${two(now.getHours())}:${two(now.getMinutes())}:${two(now.getSeconds())}`
}

/** Notes in the history whether each dictated fragment was sent as read or edited first. */
async function logSubmitted($: EngineInterface, fragments: Fragment[], prompt: string) {
  for (const fragment of fragments) {
    const entry = { time: localTime(), event: 'submitted', text: fragment.text, kept: prompt.includes(fragment.text), prompt }
    await $.process.run(['/bin/bash', script($), '--log', JSON.stringify(entry)]).catch(() => undefined)
  }
  if (historyShown) await loadHistory($)
}

/** Reads whether a personal model exists and is switched on; `to` switches it first. */
async function loadPersonal($: EngineInterface, to?: 'on' | 'off') {
  try {
    const { stdout } = await $.process.run(['/bin/bash', script($), '--personal', ...(to ? [to] : [])])
    const personal = JSON.parse(stdout) as Personal
    await update($, personalAtom, () => personal)
  } catch {
    // keep what the row shows
  }
}

/** The standby loaded the model as it was: after a change to the personal model, load it again. */
async function restartStandby($: EngineInterface) {
  const old = standby
  if (!old) return
  standby = null
  if (old.stopFile) await $.fs.write(old.stopFile, 'stop').catch(() => undefined) // it exits as expired
  keepWarm($)
}

async function togglePersonal($: EngineInterface) {
  const { enabled } = await read($, personalAtom)
  await loadPersonal($, enabled ? 'off' : 'on')
  await restartStandby($)
  $.ui.toast(enabled ? 'Personal model off: dictation uses the stock model.' : 'Personal model on: dictation uses the model trained on your lips.')
}

/** Opens the settings row, with the installed version from the plugin's manifest. */
async function openMenu($: EngineInterface) {
  await loadPersonal($)
  if (!pluginVersion) {
    try {
      pluginVersion = String(JSON.parse(await $.fs.read(`${$.plugin.root}/.claude-plugin/plugin.json`)).version ?? '')
    } catch {
      // drawn without a version
    }
  }
  await update($, menuAtom, () => true)
}

async function forgetData($: EngineInterface) {
  await update($, menuAtom, () => false)
  let answer = ''
  try {
    answer = await $.ui.ask(
      'Delete your lip dictation data? This removes the recorded training phrases, your personal model and the dictation history. The stock model stays installed.',
      ['Delete', 'Cancel'],
    )
  } catch {
    return
  }
  if (answer !== 'Delete') return
  const { exitCode } = await $.process.run(['/bin/bash', script($), 'forget'])
  await update($, historyAtom, () => [])
  await loadPersonal($)
  await restartStandby($)
  $.ui.toast(exitCode === 0 ? 'Deleted your training phrases, personal model and history.' : 'Could not delete the data.')
}

/** The History pane's lines: each dictation with its other readings and whether it was sent as read. */
export function historyLines(entries: readonly HistoryEntry[]) {
  type Line = { text: string; dim?: boolean; bold?: boolean; color?: 'success' | 'warning' }
  const dictations: { entry: HistoryEntry; sent?: HistoryEntry }[] = []
  for (const entry of entries) {
    if (entry.event !== 'submitted') {
      dictations.push({ entry })
      continue
    }
    const match = [...dictations].reverse().find(d => d.entry.event === 'result' && d.entry.text === entry.text && !d.sent)
    if (match) match.sent = entry
  }
  const lines: Line[] = []
  for (const { entry, sent } of dictations.reverse()) {
    const time = entry.time.slice(11, 16)
    if (entry.event === 'empty') {
      lines.push({ text: `${time}  nothing inserted: ${entry.message ?? ''}`, color: 'warning' })
      continue
    }
    lines.push({ text: `${time}  «${entry.text ?? ''}»`, bold: true })
    if (entry.alternatives?.length) lines.push({ text: `       also read as ${entry.alternatives.map(a => `«${a}»`).join(' / ')}`, dim: true })
    const model = entry.personal ? 'personal model' : 'stock model'
    lines.push({ text: `       ${entry.seconds ?? 0}s clip, read in ${entry.decode_seconds ?? 0}s, ${model}`, dim: true })
    if (sent) {
      lines.push(
        sent.kept
          ? { text: '       ✓ sent as read', color: 'success' }
          : { text: `       ✎ edited before sending: ${sent.prompt ?? ''}`, color: 'warning' },
      )
    }
  }
  return lines
}

/** The button, /lip and a keybinding all land here: start, stop, or offer setup. */
async function toggle($: EngineInterface): Promise<string> {
  if (run) {
    await requestStop($, run)
    return 'Stopping the recording.'
  }
  if (busy || settingUp) return 'Lip dictation is busy.'
  if (enrolling) {
    recordPhrase($)
    return 'Mouth the phrase shown above the prompt, then press Stop.'
  }
  if (!(await read($, installedAtom))) return confirmSetup($)
  busy = true // set before any await, so a double press cannot start two recordings
  if (standby) await arm($, standby)
  else void dictate($)
  return 'Camera on: mouth your words, then press the button or run /lip again to stop.'
}

export const register: Register = (on, options) => {
  config.showButton = options.showButton !== false
  config.hintClaude = options.hintClaude !== false

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'lip',
      description: 'Start or stop silent (lip-reading) dictation into the prompt',
      immediate: true,
    })
    await $.command.register({
      name: 'lip-train',
      description: 'Adapt lip dictation to your lips: mouth training phrases, then train a personal model',
      immediate: true,
    })
    await $.command.register({
      name: 'lip-setup',
      description: 'Install lip dictation: Python packages and a ~1 GB model (asks first)',
    })
    await refreshInstalled($)
    return started
  })

  // Only someone at this computer may turn its camera on.
  on('command.run', { command: 'lip' }, async ($, e) => ({
    text: e.origin.kind === 'composer' ? await toggle($) : 'Lip dictation only starts from this computer.',
    isTextDisplayOnly: true,
  }))
  on('command.run', { command: 'lip-setup' }, async $ => ({ text: await confirmSetup($), isTextDisplayOnly: true }))
  on('command.run', { command: 'lip-train' }, async ($, e) => ({
    text: e.origin.kind === 'composer' ? await startEnroll($) : 'Lip training only starts from this computer.',
    isTextDisplayOnly: true,
  }))

  on('ui.render', { component: 'Pane', requestId: HISTORY_PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const lines = historyLines(await read($, historyAtom))
    const room = Math.max(1, (e.viewport?.rows ?? 40) - 4)
    return (
      <Box flexDirection="column">
        {lines.length === 0 ? <Text dimColor>No dictations yet.</Text> : null}
        {lines.slice(0, room).map(line => (
          <Text dimColor={line.dim} bold={line.bold} color={line.color}>
            {line.text}
          </Text>
        ))}
      </Box>
    )
  })

  // Tell Claude which words came from the lip reader, so it can see past look-alike word errors.
  on('prompt.submit', async ($, e, next) => {
    if (e.origin.kind !== 'composer' || e.command || e.isMeta) return next(e)
    const pending = pendingFragments
    pendingFragments = []
    if (pending.length > 0) void logSubmitted($, pending, e.text)
    const fragments = pending.filter(fragment => e.text.includes(fragment.text))
    if (!config.hintClaude || fragments.length === 0) return next(e)
    const note = CLAUDE_HINT + fragments.map(hintFragment).join(', ')
    return next({ ...e, context: [...(e.context ?? []), note] })
  }).catch(($, e, next) => next(e)) // never block a prompt over a hint

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // The camera is this computer's: no button on remote surfaces such as the phone app.
    if (e.props.hasSurvey || (e.surface !== 'terminal' && e.surface !== 'desktop')) return next(e)
    // Read the phase on every draw: the read subscribes the band, so a later phase change redraws it.
    const stored = await read($, phaseAtom)
    const phase = run !== null || busy || settingUp || enrolling ? stored : 'idle'
    if (!config.showButton && phase === 'idle') return next(e)

    const { Box, Button, Text, Progress } = $.ui.resolve(e)
    const press = () => toggle($)
    // `l` presses the button only while the band holds the focus (ctrl+x tab), which only the terminal
    // offers; elsewhere the letter would be shown but never work.
    const hotkey = e.surface === 'terminal' ? { hotkey: 'l' } : {}

    if (phase === 'setup') {
      const progress = await read($, setupAtom)
      return (
        <Box flexDirection="row" gap={1} alignItems="center">
          <Text>👄</Text>
          <Progress
            label={`Installing lip dictation: ${progress.step}`}
            {...(progress.percent === null ? {} : { value: progress.percent, max: 100 })}
            color="claude"
          />
        </Box>
      )
    }

    if (phase === 'training') {
      const training = await read($, trainAtom)
      return (
        <Box flexDirection="row" gap={1} alignItems="center">
          <Text>👄</Text>
          <Progress
            label={`Training on your lips: ${training.step}`}
            {...(training.total > 0 ? { value: training.done, max: training.total } : {})}
            color="claude"
          />
        </Box>
      )
    }

    if (phase === 'enroll') {
      const enrollment = await read($, enrollAtom)
      const enough = enrollment.recorded >= MIN_CLIPS
      return (
        <Box flexDirection="row" gap={1} alignItems="center">
          <Button key="lip" {...hotkey} variant="primary" label="● Next phrase" onPress={press} />
          {enough ? <Button key="lip-train-now" variant="secondary" label="Train" onPress={() => void train($)} /> : null}
          <Button key="lip-train-done" variant="secondary" label="Done" onPress={() => stopEnroll($)} />
          <Text dimColor>
            {`${enrollment.recorded}/${enrollment.total} phrases recorded` + (enough ? '' : `, Train unlocks at ${MIN_CLIPS}`)}
          </Text>
        </Box>
      )
    }

    // While recording a training phrase, show the phrase to mouth.
    const phrase = run?.kind === 'enroll' ? (await read($, enrollAtom)).phrase : ''

    if (phase === 'processing') {
      const meter = await read($, meterAtom)
      return (
        <Box flexDirection="row" gap={1} alignItems="center">
          <Text>👄</Text>
          <Progress label="Reading lips" valueText={`${formatTime(meter.seconds)} clip`} color="claude" />
        </Box>
      )
    }

    if (phase === 'starting') {
      return (
        <Box flexDirection="row" gap={1} alignItems="center">
          <Button key="lip" {...hotkey} variant="secondary" label="Cancel" onPress={press} />
          <Text dimColor>Starting camera…</Text>
          {phrase ? <Text>{`Next: «${phrase}»`}</Text> : null}
        </Box>
      )
    }

    if (phase === 'recording') {
      const meter = await read($, meterAtom)
      const hint = recordingHint(meter)
      return (
        <Box flexDirection="row" gap={1} alignItems="center">
          <Button key="lip" {...hotkey} variant="primary" label="■ Stop" onPress={press} />
          <Text color="error">●</Text>
          <Text tabular>{formatTime(meter.seconds)}</Text>
          <Text color="claude">{waveText(meter.levels)}</Text>
          {phrase ? <Text bold>{`«${phrase}»`}</Text> : null}
          {hint ? hint.warn ? <Text color="warning">{hint.text}</Text> : <Text dimColor>{hint.text}</Text> : null}
        </Box>
      )
    }

    const installed = await read($, installedAtom)
    if (installed && (await read($, menuAtom))) {
      const personal = await read($, personalAtom)
      return (
        <Box flexDirection="row" justifyContent="space-between" width="100%">
          <Box flexDirection="row" gap={1}>
            <Button key="lip-train" variant="secondary" label="🎯 Train on my lips" onPress={() => startEnroll($)} />
            {personal.exists ? (
              <Button
                key="lip-personal"
                variant={personal.enabled ? 'primary' : 'secondary'}
                label={personal.enabled ? '🧠 Personal model: On' : '🧠 Personal model: Off'}
                onPress={() => togglePersonal($)}
              />
            ) : null}
            <Button key="lip-history" variant="secondary" label="🕘 History" onPress={() => openHistory($)} />
            <Button key="lip-forget" variant="secondary" label="🗑 Delete my data" onPress={() => forgetData($)} />
          </Box>
          <Box flexDirection="row" gap={1} alignItems="center">
            {pluginVersion ? <Text dimColor>{`v${pluginVersion}`}</Text> : null}
            <Button key="lip-menu" role="dismiss" label="✕ Close" onPress={() => update($, menuAtom, () => false)} />
          </Box>
        </Box>
      )
    }
    // Settings sits at the right edge, apart from the dictation button.
    return (
      <Box flexDirection="row" justifyContent="space-between" width="100%">
        <Button
          key="lip"
          {...hotkey}
          variant="secondary"
          label={installed ? LABELS.idle : SETUP_LABEL}
          onPress={press}
        />
        {installed ? <Button key="lip-menu" variant="secondary" label="⚙ Settings" onPress={() => openMenu($)} /> : null}
      </Box>
    )
  })
}

import { expect, mock, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'

import { formatTime, hintFragment, historyLines, recordingHint, waveText } from '../hooks/register'

// The sidecar is faked: these tests need no camera, Python or model.
// The stand-in streams plain pieces and answers its end as { value }.
const line = (event: object) => ({ stream: 'stdout' as const, text: `${JSON.stringify(event)}\n` })
// The band's props as a session hands them to an AbovePrompt hook.
const BAND = {
  hasSurvey: false,
  isCollapsed: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns: 120,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
}

test('the waveform: a flat baseline for a still mouth, taller bars for more movement, newest on the right', () => {
  expect(waveText([])).toBe('▁'.repeat(24))
  expect(waveText([0, 0.1, 0.5, 0.99, null])).toBe(`${'▁'.repeat(19)}▁▂▅█▁`)
  expect(waveText(Array.from({ length: 40 }, () => 1))).toBe('█'.repeat(24))
})

test('the hint next to the waveform', () => {
  expect(recordingHint({ seconds: 3, maxSeconds: 20, levels: [0.4] })).toBeNull()
  expect(recordingHint({ seconds: 16.4, maxSeconds: 20, levels: [0.4] })).toEqual({ text: 'stops in 4s', warn: false })
  expect(recordingHint({ seconds: 3, maxSeconds: 20, levels: [0.4, null] })).toEqual({ text: 'face not in view', warn: true })
})

test('the note for Claude lists the other readings', () => {
  expect(hintFragment({ text: 'Fix the bug', alternatives: [] })).toBe('«Fix the bug»')
  expect(hintFragment({ text: 'Fix the bug', alternatives: ['Fix the pug', 'Mix the bug'] })).toBe(
    '«Fix the bug» (or «Fix the pug» / «Mix the bug»)',
  )
})

test('the history pairs each dictation with what was sent', () => {
  const lines = historyLines([
    { time: '2026-11-26 13:05:01', event: 'result', text: 'Run the tests', alternatives: ['Run the test'], seconds: 2.1, decode_seconds: 1.4, personal: false },
    { time: '2026-11-26 13:05:09', event: 'submitted', text: 'Run the tests', kept: true, prompt: 'Run the tests' },
    { time: '2026-11-26 13:06:00', event: 'result', text: 'Fix the pug', seconds: 1.8, decode_seconds: 1.2, personal: true },
    { time: '2026-11-26 13:06:20', event: 'submitted', text: 'Fix the pug', kept: false, prompt: 'Fix the bug' },
    { time: '2026-11-26 13:07:00', event: 'empty', message: 'No face found in the recording.' },
  ]).map(line => line.text)
  expect(lines).toEqual([
    '13:07  nothing inserted: No face found in the recording.',
    '13:06  «Fix the pug»',
    '       1.8s clip, read in 1.2s, personal model',
    '       ✎ edited before sending: Fix the bug',
    '13:05  «Run the tests»',
    '       also read as «Run the test»',
    '       2.1s clip, read in 1.4s, stock model',
    '       ✓ sent as read',
  ])
})

test('settings: training, the personal model switch, history and deleting data', async ($, on) => {
  let enabled = true
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('fs.read', () => ({ value: JSON.stringify({ name: 'lip-dictation', version: '9.8.7' }) }))
  on('process.run', (_, e) => {
    if (e.argv.includes('--personal')) {
      if (e.argv.includes('on')) enabled = true
      if (e.argv.includes('off')) enabled = false
    }
    const stdout = e.argv.includes('--personal') ? JSON.stringify({ exists: true, enabled, info: null }) : ''
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  const ui = await $.ui.mount({ plugin: 'lip-dictation', surface: 'terminal', component: 'AbovePrompt', props: BAND })
  expect(await ui.find({ type: 'Button', text: '🎯 Train on my lips' })).toBeUndefined()
  await ui.press({ key: 'lip-menu' })
  for (const label of ['🧠 Personal model: On', '🎯 Train on my lips', '🕘 History', '🗑 Delete my data', '✕ Close']) {
    expect(await ui.find({ type: 'Button', text: label })).toBeDefined()
  }
  const buttons = (await ui.findAll({ type: 'Button' })).map(button => button.text)
  expect(buttons[0]).toBe('🧠 Personal model: On') // first in the row
  expect(await ui.find({ type: 'Text', text: 'v9.8.7' })).toBeDefined() // the installed version, from plugin.json

  await ui.press({ key: 'lip-personal' })
  expect(enabled).toBe(false)
  expect(await ui.find({ type: 'Button', text: '🧠 Personal model: Off' })).toBeDefined()

  await ui.press({ key: 'lip-menu' })
  expect(await ui.find({ type: 'Button', text: '📹 Lip dictation' })).toBeDefined()
  await ui.unmount()
})

test('the timer', () => {
  expect(formatTime(0)).toBe('0:00')
  expect(formatTime(7.9)).toBe('0:07')
  expect(formatTime(65)).toBe('1:05')
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`${surface}: a press records, the text lands at the cursor, the button comes back`, async ($, on) => {
    const filled: string[] = []
    const toasts: string[] = []
    on('ui.toast', (_, e) => {
      toasts.push(e.text)
      return { value: undefined }
    })
    on('ui.status', () => ({ value: undefined }))
    on('process.run', () => ({
      value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }))
    on('prompt.read', () => ({ value: { text: 'Please', cursor: 6 } }))
    on('prompt.fill', (_, e) => {
      filled.push(e.text)
      return { isFilled: true, text: e.text, cursor: e.text.length }
    })
    on('process.spawn', async function* () {
      yield line({ event: 'started', stop_file: '/tmp/lipread-test/stop' })
      yield line({ event: 'recording', max_seconds: 20 })
      yield line({ event: 'level', t: 0.1, level: 0 })
      yield line({ event: 'level', t: 1.2, level: 0.95 })
      yield line({ event: 'processing', seconds: 1.3 })
      yield line({ event: 'result', text: 'fix the login bug' })
      return { value: { code: 0, signal: null } }
    })

    const ui = await $.ui.mount({
      plugin: 'lip-dictation',
      surface,
      component: 'AbovePrompt',
      props: BAND,
    })
    expect(await ui.find({ type: 'Button', text: '📹 Lip dictation' })).toBeDefined()

    await ui.press({ key: 'lip' })
    expect(toasts).toEqual([])
    expect(filled).toEqual([' fix the login bug']) // a space, since the draft ends in a word
    expect(await ui.find({ type: 'Button', text: '📹 Lip dictation' })).toBeDefined()
    await ui.unmount()
  })
}

test('the band shows the recording while it runs', async ($, on) => {
  const clock = mock.clock(on)
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('prompt.read', () => ({ value: { text: '', cursor: 0 } }))
  on('prompt.fill', (_, e) => ({ isFilled: true, text: e.text, cursor: e.text.length }))
  on('process.spawn', async function* () {
    yield line({ event: 'started', stop_file: '/tmp/lipread-test/stop' })
    yield line({ event: 'recording', max_seconds: 20 })
    yield line({ event: 'level', t: 0.1, level: 0.5 })
    await clock.sleep(1000) // still recording
    yield line({ event: 'result', text: 'hello' })
    return { value: { code: 0, signal: null } }
  })

  const ui = await $.ui.mount({
    plugin: 'lip-dictation',
    surface: 'desktop',
    component: 'AbovePrompt',
    props: BAND,
  })
  await ui.press({ key: 'lip' })
  expect(await ui.find({ type: 'Button', text: '■ Stop' })).toBeDefined()
  await clock.advance(1000)
  expect(await ui.find({ type: 'Button', text: '📹 Lip dictation' })).toBeDefined()
  await ui.unmount()
})

test('a missing install turns the button into setup', async ($, on) => {
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 1, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('process.spawn', async function* () {
    yield line({ event: 'error', code: 'not-installed', message: 'Not set up yet: run /lip-setup' })
    return { value: { code: 2, signal: null } }
  })

  const ui = await $.ui.mount({
    plugin: 'lip-dictation',
    surface: 'desktop',
    component: 'AbovePrompt',
    props: BAND,
  })
  await ui.press({ key: 'lip' })
  expect(await ui.find({ type: 'Button', text: '📹 Set up lip dictation' })).toBeDefined()
  await ui.unmount()
})


test('training: record phrases, train, and hear how much it helped', async ($, on) => {
  const toasts: string[] = []
  on('ui.toast', (_, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.status', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('process.spawn', async function* (_, e) {
    if (e.argv.includes('enroll')) {
      yield line({ event: 'phrase', text: 'run the tests', recorded: 24, total: 98 })
      yield line({ event: 'started', stop_file: '/tmp/lipread-test/stop' })
      yield line({ event: 'recording', max_seconds: 20 })
      yield line({ event: 'processing', seconds: 1.5 })
      yield line({ event: 'saved', recorded: 25, total: 98 })
    } else {
      yield line({ event: 'progress', step: 'Training', done: 1, total: 8 })
      yield line({ event: 'trained', before: 0.5, after: 0.3, kept: true, clips: 25, held_out: 5 })
    }
    return { value: { code: 0, signal: null } }
  })

  const ui = await $.ui.mount({
    plugin: 'lip-dictation',
    surface: 'desktop',
    component: 'AbovePrompt',
    props: BAND,
  })
  await ui.press({ key: 'lip-menu' })
  await ui.press({ key: 'lip-train' })
  expect(await ui.find({ type: 'Button', text: '● Next phrase' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /25\/98 phrases recorded/ })).toBeDefined()

  await ui.press({ key: 'lip-train-now' })
  expect(toasts).toEqual(['Lip dictation now uses your personal model: word errors 50% → 30% on 5 held-out phrases.'])
  expect(await ui.find({ type: 'Button', text: '📹 Lip dictation' })).toBeDefined()
  await ui.unmount()
})

test('after a dictation, the next press starts the loaded standby instead of a new process', async ($, on) => {
  const clock = mock.clock(on)
  const filled: string[] = []
  const spawned: string[][] = []
  let goWritten = false
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('prompt.read', () => ({ value: { text: '', cursor: 0 } }))
  on('prompt.fill', (_, e) => {
    filled.push(e.text)
    return { isFilled: true, text: e.text, cursor: e.text.length }
  })
  on('fs.write', (_, e) => {
    if (e.path.endsWith('.go')) goWritten = true
    return { value: undefined }
  })
  on('process.spawn', async function* (_, e) {
    spawned.push([...e.argv])
    const standby = e.argv.includes('--wait')
    yield line({ event: 'started', stop_file: '/tmp/lipread-test/stop', ...(standby ? { go_file: '/tmp/lipread-test/stop.go' } : {}) })
    if (standby) {
      yield line({ event: 'ready' })
      while (!goWritten) await clock.sleep(50) // loaded, waiting for a press
    }
    yield line({ event: 'recording', max_seconds: 20 })
    yield line({ event: 'processing', seconds: 1.5 })
    yield line({ event: 'result', text: standby ? 'second' : 'first' })
    return { value: { code: 0, signal: null } }
  })

  const ui = await $.ui.mount({
    plugin: 'lip-dictation',
    surface: 'desktop',
    component: 'AbovePrompt',
    props: BAND,
  })
  await ui.press({ key: 'lip' })
  expect(filled).toEqual(['first'])
  expect(spawned.map(argv => argv.includes('--wait'))).toEqual([false, true]) // a standby loads right away

  await ui.press({ key: 'lip' })
  expect(goWritten).toBe(true)
  await clock.advance(50)
  expect(filled).toEqual(['first', 'second'])
  expect(spawned).toHaveLength(3) // the standby was used, and the next one is loading
  expect(await ui.find({ type: 'Button', text: '📹 Lip dictation' })).toBeDefined()
  await ui.unmount()
})

/** A fake `lipread.sh enroll`: says the phrase, records until the stop file, then saves or (with
 * "<stop>.discard" written first) cancels. `--redo-last` drops the last saved phrase first. */
function fakeEnroll(on: Parameters<TestBody>[1]) {
  const clock = mock.clock(on)
  const state = { recorded: 24, written: [] as string[], spawned: [] as string[][] }
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('fs.write', (_, e) => {
    state.written.push(e.path)
    return { value: undefined }
  })
  on('process.spawn', async function* (_, e) {
    state.spawned.push([...e.argv])
    state.written = []
    if (e.argv.includes('--redo-last')) state.recorded -= 1
    yield line({ event: 'phrase', text: 'run the tests', recorded: state.recorded, total: 98 })
    yield line({ event: 'started', stop_file: '/tmp/lipread-test/stop' })
    yield line({ event: 'recording', max_seconds: 20 })
    while (!state.written.includes('/tmp/lipread-test/stop')) await clock.sleep(50) // mouthing until Stop
    if (state.written.includes('/tmp/lipread-test/stop.discard')) {
      yield line({ event: 'cancelled' })
    } else {
      state.recorded += 1
      yield line({ event: 'saved', recorded: state.recorded, total: 98 })
    }
    return { value: { code: 0, signal: null } }
  })
  return { clock, state }
}

test('training: Discard ends a phrase without saving it', async ($, on) => {
  const { clock, state } = fakeEnroll(on)
  const ui = await $.ui.mount({ plugin: 'lip-dictation', surface: 'desktop', component: 'AbovePrompt', props: BAND })
  await ui.press({ key: 'lip-menu' })
  await ui.press({ key: 'lip-train' })
  await ui.press({ key: 'lip-discard' })
  expect(state.written).toEqual(['/tmp/lipread-test/stop.discard', '/tmp/lipread-test/stop']) // the mark goes first
  await clock.advance(50)
  expect(await ui.find({ type: 'Text', text: /24\/98 phrases recorded/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /Redo/ })).toBeUndefined()
  await ui.unmount()
})

test('training: Redo records the last saved phrase again', async ($, on) => {
  const { clock, state } = fakeEnroll(on)
  const ui = await $.ui.mount({ plugin: 'lip-dictation', surface: 'desktop', component: 'AbovePrompt', props: BAND })
  await ui.press({ key: 'lip-menu' })
  await ui.press({ key: 'lip-train' })
  await ui.press({ key: 'lip' }) // Stop: saved
  await clock.advance(50)
  expect(await ui.find({ type: 'Button', text: '↺ Redo «run the tests»' })).toBeDefined()
  await ui.press({ key: 'lip-redo' })
  expect(state.spawned.at(-1)).toContain('--redo-last')
  expect(state.recorded).toBe(24) // the saved one was dropped
  await ui.unmount()
})

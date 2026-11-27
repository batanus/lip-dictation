export type Phase = 'idle' | 'starting' | 'recording' | 'processing' | 'setup' | 'enroll' | 'training'

/** The live recording: elapsed and maximum seconds, and recent mouth levels (0..1, null = no face). */
export type Meter = { seconds: number; maxSeconds: number; levels: (number | null)[] }

/** Setup progress: the step being run and, while downloading, a percent (null = no known end). */
export type SetupProgress = { step: string; percent: number | null }

/** Recording training phrases: the phrase being mouthed, how many of the list are recorded, and the one just saved (to redo). */
export type Enrollment = { phrase: string; recorded: number; total: number; lastSaved?: string }

/** Training the personal adapter: the step and its progress. */
export type Training = { step: string; done: number; total: number }

/** One line of ~/.cache/lip-dictation/dictations.jsonl: a dictation (`result` or `empty`), or what was sent. */
export type HistoryEntry = {
  time: string
  event: 'result' | 'empty' | 'submitted'
  text?: string
  alternatives?: string[]
  seconds?: number
  decode_seconds?: number
  personal?: boolean
  message?: string
  kept?: boolean
  prompt?: string
}

/** The personal model (`lipread.sh --personal`): trained or not, switched on or off, and its scores. */
export type Personal = {
  exists: boolean
  enabled: boolean
  info: { clips: number; held_out: number; before: number; after: number; trained_at: string } | null
}

declare module 'claude-code' {
  interface PluginState {
    'lip-dictation': {
      phase: Phase
      installed: boolean
      meter: Meter
      setup: SetupProgress
      enrollment: Enrollment
      training: Training
      menu: boolean
      history: HistoryEntry[]
      personal: Personal
    }
  }
}

import { postJson } from './api.js'

// The four states the relay recognises, in the order an event moves through
// them. `starting` is the welcome screen, not a null state.
export const EVENT_STATES = [
	{ id: 'starting', label: 'Starting soon (welcome screen)' },
	{ id: 'live', label: 'Live' },
	{ id: 'paused', label: 'Paused' },
	{ id: 'ended', label: 'Ended' },
]

const BACKENDS = [
	{ id: 'apple_speech', label: 'Apple on-device (local, free)' },
	{ id: 'whisper', label: 'Whisper (local, offline)' },
	{ id: 'mock', label: 'Mock (rehearsal script)' },
]

// The text an audience sees, as the relay's /api/event names it.
const EVENT_TEXT = [
	['title', 'Title'],
	['message', 'Welcome message'],
	['notice', 'Accessibility notice'],
	['pause_message', 'Pause message'],
	['disclaimer', 'Disclaimer'],
]

const ONOFF = {
	type: 'dropdown',
	id: 'mode',
	label: 'Turn',
	default: 'toggle',
	choices: [
		{ id: 'toggle', label: 'Toggle' },
		{ id: 'on', label: 'On' },
		{ id: 'off', label: 'Off' },
	],
}

const onOff = (mode, current) => (mode === 'toggle' ? !current : mode === 'on')

export function getActions(self) {
	// Every action funnels through here so a failure is logged once, shown on
	// the connection, and never left as an unhandled rejection.
	const send = async (path, body, description) => {
		try {
			await postJson(self.conn, path, body)
			self.log('debug', `${description} ok`)
		} catch (error) {
			self.log('error', `${description} failed: ${error.message}`)
		}
	}

	return {
		set_event_state: {
			name: 'Event: set state',
			options: [
				{
					type: 'dropdown',
					id: 'state',
					label: 'State',
					default: 'live',
					choices: EVENT_STATES,
				},
			],
			callback: async ({ options }) =>
				send('/api/event', { state: options.state }, `Event -> ${options.state}`),
		},

		open_stream: {
			name: 'Captions: open ASR stream',
			description: 'Starts recognition.',
			options: [],
			callback: async () => send('/api/control', { action: 'open_stream' }, 'Open ASR stream'),
		},

		close_stream: {
			name: 'Captions: close ASR stream',
			description: 'Stops recognition.',
			options: [],
			callback: async () => send('/api/control', { action: 'close_stream' }, 'Close ASR stream'),
		},

		toggle_stream: {
			name: 'Captions: toggle ASR stream',
			description: 'Opens the stream if closed, closes it if open.',
			options: [],
			callback: async () => {
				const action = self.state?.asr?.stream_open ? 'close_stream' : 'open_stream'
				await send('/api/control', { action }, `Toggle ASR stream -> ${action}`)
			},
		},

		clear_captions: {
			name: 'Captions: clear',
			description: 'Blanks the wall now, and empties the replay buffer so a phone joining afterwards does not see the cleared text.',
			options: [],
			callback: async () => send('/api/control', { action: 'clear' }, 'Clear captions'),
		},

		caption: {
			name: 'Captions: type a caption',
			description: 'Commits a line exactly as the recogniser would - [Applause], a speaker\'s name, a correction.',
			options: [
				{
					type: 'textinput',
					id: 'text',
					label: 'Text',
					default: '[Applause]',
					useVariables: true,
				},
			],
			callback: async ({ options }, context) => {
				const text = await context.parseVariablesInString(options.text)
				if (!text.trim()) {
					self.log('warn', 'Caption skipped: nothing to send')
					return
				}
				await send('/api/control', { action: 'caption', text }, `Caption "${text}"`)
			},
		},

		mark_video_start: {
			name: 'Recording: mark video start',
			description: 'Sets the zero point for exported .srt timings. Press it when the stream or recording actually begins.',
			options: [],
			callback: async () => send('/api/control', { action: 'mark_video_start' }, 'Mark video start'),
		},

		select_backend: {
			name: 'Setup: select transcription backend',
			options: [
				{
					type: 'dropdown',
					id: 'backend',
					label: 'Backend',
					default: 'apple_speech',
					choices: BACKENDS,
				},
			],
			callback: async ({ options }) =>
				send('/api/control', { action: 'select_backend', backend: options.backend }, `Backend -> ${options.backend}`),
		},

		select_audio_device: {
			name: 'Setup: select audio input',
			description: 'By device index, as listed on the status page. The relay remembers the choice for next launch.',
			options: [
				{
					type: 'number',
					id: 'device',
					label: 'Device index',
					default: 0,
					min: 0,
					max: 4096,
				},
			],
			callback: async ({ options }) =>
				send('/api/control', { action: 'select_audio_device', device: Number(options.device) }, `Audio input -> ${options.device}`),
		},

		set_profanity_filter: {
			name: 'Setup: profanity filter',
			options: [
				{
					type: 'dropdown',
					id: 'enabled',
					label: 'Filter',
					default: 'true',
					choices: [
						{ id: 'true', label: 'On (bleep)' },
						{ id: 'false', label: 'Off (verbatim)' },
					],
				},
			],
			callback: async ({ options }) =>
				send(
					'/api/control',
					{ action: 'set_profanity_filter', enabled: options.enabled === 'true' },
					`Profanity filter -> ${options.enabled}`
				),
		},

		reload_keyterms: {
			name: 'Setup: reload keyterms & blocklist',
			description: 'Re-reads both word lists from disk without restarting anything.',
			options: [],
			callback: async () => send('/api/control', { action: 'reload_keyterms' }, 'Reload keyterms & blocklist'),
		},

		set_welcome_text: {
			name: 'Event: set audience text',
			description: 'Any field left blank is left unchanged.',
			options: EVENT_TEXT.map(([id, label]) => ({
				type: 'textinput',
				id,
				label,
				default: '',
				useVariables: true,
			})),
			callback: async ({ options }, context) => {
				const body = {}
				for (const [id] of EVENT_TEXT) {
					const value = await context.parseVariablesInString(options[id] ?? '')
					if (value.trim()) body[id] = value
				}
				if (!Object.keys(body).length) {
					self.log('warn', 'Audience text: every field blank, nothing sent')
					return
				}
				await send('/api/event', body, 'Audience text')
			},
		},

		next_event: {
			name: 'Event: next event',
			description:
				'Saves the transcript so far, wipes the wall and every phone, and goes back to the welcome screen. ' +
				'Settings and the audio input stay as they are.',
			options: [
				{ type: 'textinput', id: 'title', label: 'New title (blank = keep the current one)', default: '', useVariables: true },
			],
			callback: async ({ options }, context) => {
				const title = await context.parseVariablesInString(options.title ?? '')
				await send('/api/event/next', title.trim() ? { title } : {}, 'Next event')
			},
		},

		reset_transcript: {
			name: 'Recording: reset transcript',
			description: 'Empties the recorded transcript (the .srt/.txt export) without touching what is on screen.',
			options: [],
			callback: async () => send('/api/control', { action: 'reset_transcript' }, 'Reset transcript'),
		},

		set_profanity_toggle: {
			name: 'Setup: profanity filter (toggle)',
			options: [],
			callback: async () => {
				const enabled = !self.state?.source?.mask_profanity
				await send('/api/control', { action: 'set_profanity_filter', enabled }, `Profanity filter -> ${enabled}`)
			},
		},

		set_audio_gain: {
			name: 'Setup: input gain',
			description: 'Sets the boost applied to the microphone, or nudges it up or down. 0-24 dB.',
			options: [
				{
					type: 'dropdown',
					id: 'mode',
					label: 'Mode',
					default: 'set',
					choices: [
						{ id: 'set', label: 'Set to' },
						{ id: 'up', label: 'Raise by' },
						{ id: 'down', label: 'Lower by' },
					],
				},
				{ type: 'number', id: 'db', label: 'dB', default: 6, min: 0, max: 24 },
			],
			callback: async ({ options }) => {
				const now = Number(self.state?.source?.audio_gain_db) || 0
				const step = Number(options.db) || 0
				const target = options.mode === 'up' ? now + step : options.mode === 'down' ? now - step : step
				const gain_db = Math.max(0, Math.min(24, Math.round(target)))
				await send('/api/control', { action: 'set_audio_gain', gain_db }, `Input gain -> ${gain_db} dB`)
			},
		},

		refresh_audio_devices: {
			name: 'Setup: rescan audio inputs',
			description: 'Re-lists the Mac\'s audio inputs, e.g. after plugging in an interface.',
			options: [],
			callback: async () => send('/api/control', { action: 'refresh_audio_devices' }, 'Rescan audio inputs'),
		},

		youtube_captions: {
			name: 'Outputs: YouTube captions',
			description: 'Pushes captions to the YouTube ingestion URL pasted in Settings.',
			options: [ONOFF],
			callback: async ({ options }) => {
				const enabled = onOff(options.mode, self.state?.youtube?.enabled)
				await send('/api/youtube-captions', { enabled }, `YouTube captions -> ${enabled}`)
			},
		},

		propresenter: {
			name: 'Outputs: ProPresenter',
			description: 'Sends captions straight to ProPresenter, as set up in Settings.',
			options: [ONOFF],
			callback: async ({ options }) => {
				const enabled = onOff(options.mode, self.state?.propresenter?.enabled)
				await send('/api/propresenter', { enabled }, `ProPresenter -> ${enabled}`)
			},
		},

		propresenter_clear: {
			name: 'Outputs: clear ProPresenter',
			description: 'Takes the current caption off the ProPresenter surface.',
			options: [],
			callback: async () => send('/api/propresenter', { clear: true }, 'Clear ProPresenter'),
		},

		tunnel: {
			name: 'Outputs: public tunnel',
			description: 'Lets phones off the venue network reach the captions, as set up in Settings.',
			options: [ONOFF],
			callback: async ({ options }) => {
				const enabled = onOff(options.mode, self.state?.tunnel?.enabled)
				await send('/api/tunnel', { enabled }, `Public tunnel -> ${enabled}`)
			},
		},
	}
}

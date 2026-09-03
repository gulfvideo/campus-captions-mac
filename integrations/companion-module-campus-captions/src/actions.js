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
	{ id: 'deepgram', label: 'Deepgram (cloud, paid)' },
	{ id: 'mock', label: 'Mock (rehearsal script)' },
]

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
			description: 'Starts recognition. On Deepgram this is where billing starts.',
			options: [],
			callback: async () => send('/api/control', { action: 'open_stream' }, 'Open ASR stream'),
		},

		close_stream: {
			name: 'Captions: close ASR stream',
			description: 'Stops recognition. On Deepgram this is where billing stops.',
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
			name: 'Event: set welcome screen text',
			options: [
				{ type: 'textinput', id: 'title', label: 'Title (blank = leave unchanged)', default: '', useVariables: true },
				{ type: 'textinput', id: 'message', label: 'Message (blank = leave unchanged)', default: '', useVariables: true },
			],
			callback: async ({ options }, context) => {
				const body = {}
				const title = await context.parseVariablesInString(options.title)
				const message = await context.parseVariablesInString(options.message)
				if (title.trim()) body.title = title
				if (message.trim()) body.message = message
				if (!Object.keys(body).length) {
					self.log('warn', 'Welcome screen: both fields blank, nothing sent')
					return
				}
				await send('/api/event', body, 'Welcome screen text')
			},
		},
	}
}

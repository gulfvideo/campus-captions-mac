import { combineRgb } from '@companion-module/base'
import { EVENT_STATES } from './actions.js'

const WHITE = combineRgb(255, 255, 255)
const BLACK = combineRgb(0, 0, 0)
const GREEN = combineRgb(0, 140, 70)
const RED = combineRgb(190, 40, 30)
const AMBER = combineRgb(200, 130, 20)

export function getFeedbacks(self) {
	// `self.state` is the last snapshot from the status socket. Before the
	// first one arrives it is null, and every feedback reads false rather
	// than guessing - an unlit key is honest, a wrongly lit one is not.
	const snap = () => self.state

	return {
		event_state: {
			type: 'boolean',
			name: 'Event is in state',
			description: 'Lights the key when the event is in the chosen state.',
			defaultStyle: { bgcolor: GREEN, color: WHITE },
			options: [
				{
					type: 'dropdown',
					id: 'state',
					label: 'State',
					default: 'live',
					choices: EVENT_STATES,
				},
			],
			callback: ({ options }) => snap()?.event?.state === options.state,
		},

		asr_stream_open: {
			type: 'boolean',
			name: 'ASR stream is open',
			description:
				'The tally light: recognition is running.',
			defaultStyle: { bgcolor: RED, color: WHITE },
			options: [],
			callback: () => Boolean(snap()?.asr?.stream_open),
		},

		pipeline_fault: {
			type: 'boolean',
			name: 'Pipeline fault',
			description:
				'Something the audience cannot see is wrong: no capture client, a dead microphone, ' +
				'a closed stream. This is the one worth putting on a key you can see from the desk.',
			defaultStyle: { bgcolor: RED, color: WHITE },
			options: [],
			callback: () => Boolean(snap()?.health?.fault),
		},

		audio_state: {
			type: 'boolean',
			name: 'Audio input is in state',
			description: 'Live means speech is arriving. Silent means the input is quiet. Stale means nothing is arriving at all.',
			defaultStyle: { bgcolor: AMBER, color: BLACK },
			options: [
				{
					type: 'dropdown',
					id: 'state',
					label: 'Audio state',
					default: 'live',
					choices: [
						{ id: 'live', label: 'Live (speech arriving)' },
						{ id: 'silent', label: 'Silent' },
						{ id: 'stale', label: 'Stale (nothing arriving)' },
						{ id: 'no_source', label: 'No capture client' },
					],
				},
			],
			callback: ({ options }) => snap()?.audio?.state === options.state,
		},

		backend_selected: {
			type: 'boolean',
			name: 'Transcription backend is',
			defaultStyle: { bgcolor: AMBER, color: BLACK },
			options: [
				{
					type: 'dropdown',
					id: 'backend',
					label: 'Backend',
					default: 'apple_speech',
					choices: [
						{ id: 'apple_speech', label: 'Apple on-device' },
						{ id: 'whisper', label: 'Whisper' },
						{ id: 'mock', label: 'Mock' },
					],
				},
			],
			callback: ({ options }) => snap()?.source?.backend === options.backend,
		},

		viewers_present: {
			type: 'boolean',
			name: 'Viewers connected',
			description: 'At least this many people have a caption view open.',
			defaultStyle: { bgcolor: GREEN, color: WHITE },
			options: [
				{
					type: 'number',
					id: 'minimum',
					label: 'At least',
					default: 1,
					min: 0,
					max: 10000,
				},
			],
			callback: ({ options }) => (snap()?.viewers?.total ?? 0) >= Number(options.minimum),
		},

		youtube_enabled: {
			type: 'boolean',
			name: 'YouTube captions are on',
			defaultStyle: { bgcolor: RED, color: WHITE },
			options: [],
			callback: () => Boolean(snap()?.youtube?.enabled),
		},

		youtube_error: {
			type: 'boolean',
			name: 'YouTube captions have an error',
			description: 'YouTube is on but the last push failed.',
			defaultStyle: { bgcolor: AMBER, color: BLACK },
			options: [],
			callback: () => Boolean(snap()?.youtube?.enabled && snap()?.youtube?.error),
		},

		propresenter_state: {
			type: 'boolean',
			name: 'ProPresenter output is in state',
			defaultStyle: { bgcolor: GREEN, color: WHITE },
			options: [
				{
					type: 'dropdown',
					id: 'state',
					label: 'State',
					default: 'connected',
					choices: [
						{ id: 'connected', label: 'Connected' },
						{ id: 'connecting', label: 'Connecting' },
						{ id: 'degraded', label: 'Degraded (some pushes failing)' },
						{ id: 'disconnected', label: 'Disconnected' },
						{ id: 'unsupported', label: 'Unsupported ProPresenter version' },
						{ id: 'off', label: 'Off' },
					],
				},
			],
			callback: ({ options }) => snap()?.propresenter?.state === options.state,
		},

		profanity_filter_on: {
			type: 'boolean',
			name: 'Profanity filter is on',
			defaultStyle: { bgcolor: GREEN, color: WHITE },
			options: [],
			callback: () => Boolean(snap()?.source?.mask_profanity),
		},

		audio_clipping: {
			type: 'boolean',
			name: 'Audio input is clipping',
			description: 'The microphone is too hot. Turn the input gain down.',
			defaultStyle: { bgcolor: RED, color: WHITE },
			options: [],
			callback: () => Boolean(snap()?.audio?.clipping),
		},

		tunnel_connected: {
			type: 'boolean',
			name: 'Public tunnel is connected',
			description: 'Whether people off the venue network can reach the phone view.',
			defaultStyle: { bgcolor: GREEN, color: WHITE },
			options: [],
			callback: () => Boolean(snap()?.tunnel?.connected),
		},
	}
}

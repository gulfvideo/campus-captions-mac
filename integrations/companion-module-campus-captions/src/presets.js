import { combineRgb } from '@companion-module/base'

const WHITE = combineRgb(255, 255, 255)
const BLACK = combineRgb(0, 0, 0)
const GREY = combineRgb(28, 30, 34)
const GREEN = combineRgb(0, 140, 70)
const RED = combineRgb(190, 40, 30)
const AMBER = combineRgb(200, 130, 20)

const base = (text, bgcolor = GREY, color = WHITE) => ({
	text,
	size: '14',
	color,
	bgcolor,
})

const press = (actionId, options = {}) => [{ down: [{ actionId, options }], up: [] }]

/**
 * A ready-made page, so the first run is drag-and-drop rather than an hour of
 * wiring. Laid out as an operator would want it: the event's life along the
 * top, the things you touch mid-service below, canned captions last.
 */
export function getPresets(label) {
	// Preset text names this connection by its label, which the operator can
	// rename; index.js rebuilds the presets when it changes.
	const v = (id) => `$(${label}:${id})`
	return {
		start_event: {
			type: 'button',
			category: 'Event',
			name: 'Start event',
			style: base('Start\\nEvent'),
			steps: press('set_event_state', { state: 'live' }),
			feedbacks: [
				{ feedbackId: 'event_state', options: { state: 'live' }, style: { bgcolor: GREEN, color: WHITE } },
			],
		},
		pause_event: {
			type: 'button',
			category: 'Event',
			name: 'Pause',
			style: base('Pause'),
			steps: press('set_event_state', { state: 'paused' }),
			feedbacks: [
				{ feedbackId: 'event_state', options: { state: 'paused' }, style: { bgcolor: AMBER, color: BLACK } },
			],
		},
		resume_event: {
			type: 'button',
			category: 'Event',
			name: 'Resume',
			style: base('Resume'),
			steps: press('set_event_state', { state: 'live' }),
			feedbacks: [],
		},
		end_event: {
			type: 'button',
			category: 'Event',
			name: 'End event',
			style: base('End\\nEvent'),
			steps: press('set_event_state', { state: 'ended' }),
			feedbacks: [
				{ feedbackId: 'event_state', options: { state: 'ended' }, style: { bgcolor: RED, color: WHITE } },
			],
		},
		welcome_screen: {
			type: 'button',
			category: 'Event',
			name: 'Back to welcome screen',
			style: base('Welcome\\nScreen'),
			steps: press('set_event_state', { state: 'starting' }),
			feedbacks: [
				{ feedbackId: 'event_state', options: { state: 'starting' }, style: { bgcolor: AMBER, color: BLACK } },
			],
		},

		next_event: {
			type: 'button',
			category: 'Event',
			name: 'Next event (save transcript, wipe screens)',
			style: base('Next\\nEvent'),
			steps: press('next_event', { title: '' }),
			feedbacks: [],
		},

		toggle_stream: {
			type: 'button',
			category: 'Captions',
			name: 'ASR stream (toggle)',
			style: base('ASR\\nStream'),
			steps: press('toggle_stream'),
			feedbacks: [{ feedbackId: 'asr_stream_open', options: {}, style: { bgcolor: RED, color: WHITE } }],
		},
		clear_captions: {
			type: 'button',
			category: 'Captions',
			name: 'Clear captions',
			style: base('Clear\\nCaptions'),
			steps: press('clear_captions'),
			feedbacks: [],
		},
		mark_video_start: {
			type: 'button',
			category: 'Captions',
			name: 'Mark video start',
			style: base('Mark\\nVideo'),
			steps: press('mark_video_start'),
			feedbacks: [],
		},

		applause: {
			type: 'button',
			category: 'Canned captions',
			name: '[Applause]',
			style: base('[Applause]', GREY),
			steps: press('caption', { text: '[Applause]' }),
			feedbacks: [],
		},
		music: {
			type: 'button',
			category: 'Canned captions',
			name: '[Music]',
			style: base('[Music]', GREY),
			steps: press('caption', { text: '[Music]' }),
			feedbacks: [],
		},
		inaudible: {
			type: 'button',
			category: 'Canned captions',
			name: '[Inaudible]',
			style: base('[Inaudible]', GREY),
			steps: press('caption', { text: '[Inaudible]' }),
			feedbacks: [],
		},

		fault_alarm: {
			type: 'button',
			category: 'Status',
			name: 'Fault alarm',
			style: base('Captions\\nOK'),
			steps: [],
			feedbacks: [
				{
					feedbackId: 'pipeline_fault',
					options: {},
					style: { bgcolor: RED, color: WHITE, text: `FAULT\\n${v('fault')}` },
				},
			],
		},
		viewers: {
			type: 'button',
			category: 'Status',
			name: 'Viewer count',
			style: base(`Viewers\\n${v('viewers_total')}`),
			steps: [],
			feedbacks: [
				{ feedbackId: 'viewers_present', options: { minimum: 1 }, style: { bgcolor: GREEN, color: WHITE } },
			],
		},
		audio_meter: {
			type: 'button',
			category: 'Status',
			name: 'Audio state',
			style: base(`Audio\\n${v('audio_state')}`),
			steps: [],
			feedbacks: [
				{ feedbackId: 'audio_state', options: { state: 'live' }, style: { bgcolor: GREEN, color: WHITE } },
				{ feedbackId: 'audio_state', options: { state: 'stale' }, style: { bgcolor: RED, color: WHITE } },
			],
		},
		gain_down: {
			type: 'button',
			category: 'Audio',
			name: 'Input gain down 3 dB',
			style: base(`Gain -\\n${v('audio_gain_db')} dB`),
			steps: press('set_audio_gain', { mode: 'down', db: 3 }),
			feedbacks: [{ feedbackId: 'audio_clipping', options: {}, style: { bgcolor: RED, color: WHITE } }],
		},
		gain_up: {
			type: 'button',
			category: 'Audio',
			name: 'Input gain up 3 dB',
			style: base(`Gain +\\n${v('audio_gain_db')} dB`),
			steps: press('set_audio_gain', { mode: 'up', db: 3 }),
			feedbacks: [{ feedbackId: 'audio_clipping', options: {}, style: { bgcolor: RED, color: WHITE } }],
		},
		profanity: {
			type: 'button',
			category: 'Audio',
			name: 'Profanity filter (toggle)',
			style: base('Profanity\\nFilter'),
			steps: press('set_profanity_toggle'),
			feedbacks: [{ feedbackId: 'profanity_filter_on', options: {}, style: { bgcolor: GREEN, color: WHITE } }],
		},

		youtube: {
			type: 'button',
			category: 'Outputs',
			name: 'YouTube captions (toggle)',
			style: base('YouTube\\nCaptions'),
			steps: press('youtube_captions', { mode: 'toggle' }),
			feedbacks: [
				{ feedbackId: 'youtube_enabled', options: {}, style: { bgcolor: RED, color: WHITE } },
				{ feedbackId: 'youtube_error', options: {}, style: { bgcolor: AMBER, color: BLACK } },
			],
		},
		propresenter: {
			type: 'button',
			category: 'Outputs',
			name: 'ProPresenter output (toggle)',
			style: base('ProPres\\nOutput'),
			steps: press('propresenter', { mode: 'toggle' }),
			feedbacks: [
				{ feedbackId: 'propresenter_state', options: { state: 'connected' }, style: { bgcolor: GREEN, color: WHITE } },
				{ feedbackId: 'propresenter_state', options: { state: 'degraded' }, style: { bgcolor: AMBER, color: BLACK } },
				{ feedbackId: 'propresenter_state', options: { state: 'disconnected' }, style: { bgcolor: RED, color: WHITE } },
			],
		},
		propresenter_clear: {
			type: 'button',
			category: 'Outputs',
			name: 'Clear ProPresenter',
			style: base('Clear\\nProPres'),
			steps: press('propresenter_clear'),
			feedbacks: [],
		},

		backend: {
			type: 'button',
			category: 'Status',
			name: 'Backend in use',
			style: base(`Backend\\n${v('backend')}`),
			steps: [],
			feedbacks: [],
		},
	}
}

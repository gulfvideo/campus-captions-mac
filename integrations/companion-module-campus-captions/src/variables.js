// Values a button can print, or an expression can branch on. Names are what
// the operator types into a button, so they read as words rather than as the
// relay's internal JSON paths.
export const variableDefinitions = [
	{ variableId: 'event_state', name: 'Event state' },
	{ variableId: 'asr_state', name: 'ASR state' },
	{ variableId: 'asr_stream_open', name: 'ASR stream open' },
	{ variableId: 'audio_state', name: 'Audio input state' },
	{ variableId: 'audio_level', name: 'Audio level (0-100)' },
	{ variableId: 'backend', name: 'Transcription backend' },
	{ variableId: 'device', name: 'Audio input device' },
	{ variableId: 'fault', name: 'Pipeline fault' },
	{ variableId: 'viewers_total', name: 'Viewers - total' },
	{ variableId: 'viewers_phone', name: 'Viewers - phone' },
	{ variableId: 'viewers_wall', name: 'Viewers - wall' },
	{ variableId: 'transcript_lines', name: 'Transcript lines recorded' },
	{ variableId: 'tunnel_connected', name: 'Public tunnel connected' },
	{ variableId: 'last_caption', name: 'Last committed caption' },
]

const FAULT_TEXT = {
	no_source: 'no capture client',
	source_error: 'capture client error',
	audio_stalled: 'audio stalled',
	stream_closed: 'stream closed',
}

export function valuesFromSnapshot(snapshot) {
	const source = snapshot.source ?? {}
	const audio = snapshot.audio ?? {}
	const asr = snapshot.asr ?? {}
	const viewers = snapshot.viewers ?? {}
	const fault = snapshot.health?.fault

	// The source language's line count. `transcript.lines` is keyed by
	// language because a translated stream records separately.
	const lines = snapshot.transcript?.lines
	const lineCount =
		lines && typeof lines === 'object' ? Object.values(lines).reduce((a, b) => a + (Number(b) || 0), 0) : 0

	return {
		event_state: snapshot.event?.state ?? 'unknown',
		asr_state: asr.state ?? 'unknown',
		asr_stream_open: asr.stream_open ? 'yes' : 'no',
		audio_state: audio.state ?? 'unknown',
		// 0-100 reads better on a button than 0.0734.
		audio_level: Math.round((Number(audio.smoothed ?? audio.level) || 0) * 100),
		backend: source.backend ?? 'unknown',
		device: source.device ?? 'none',
		fault: fault ? (FAULT_TEXT[fault] ?? fault) : 'none',
		viewers_total: viewers.total ?? 0,
		viewers_phone: viewers.phone ?? 0,
		viewers_wall: viewers.wall ?? 0,
		transcript_lines: lineCount,
		tunnel_connected: snapshot.tunnel?.connected ? 'yes' : 'no',
		last_caption: asr.last_final_text ?? '',
	}
}

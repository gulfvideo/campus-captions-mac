// Drives every action against a running relay and checks the status socket,
// feedbacks and variables. Run with a throwaway relay:
//   CAPTIONS_URL=127.0.0.1:8765 CAPTIONS_TOKEN=op node test/smoke.js
import assert from 'node:assert/strict'
import { StatusSocket } from '../src/api.js'
import { getActions } from '../src/actions.js'
import { getFeedbacks } from '../src/feedbacks.js'
import { getPresets } from '../src/presets.js'
import { variableDefinitions, valuesFromSnapshot } from '../src/variables.js'

const [host, port] = (process.env.CAPTIONS_URL ?? '127.0.0.1:8000').split(':')
const errors = []
const self = {
	conn: { host, port: Number(port), token: process.env.CAPTIONS_TOKEN ?? '' },
	state: null,
	log: (level, msg) => level === 'error' && errors.push(msg),
}
const context = { parseVariablesInString: async (s) => s }

const snapshot = () =>
	new Promise((resolve, reject) => {
		const sock = new StatusSocket(self.conn, {
			onSnapshot: (s) => (sock.close(), resolve(s)),
			onStateChange: ({ ok, message }) => ok || reject(new Error(message)),
		})
		sock.connect()
	})

const actions = getActions(self)
const run = async (id, options = {}) => {
	errors.length = 0
	await actions[id].callback({ options }, context)
	return [...errors]
}

self.state = await snapshot()
assert.ok(self.state.event, 'status socket delivers the event')

assert.deepEqual(await run('set_event_state', { state: 'live' }), [])
assert.deepEqual(await run('set_welcome_text', { title: 'Chapel', notice: 'Captions are automatic', pause_message: 'Back soon' }), [])
self.state = await snapshot()
assert.equal(self.state.event.state, 'live')
assert.equal(self.state.event.notice, 'Captions are automatic')
assert.equal(valuesFromSnapshot(self.state).event_title, 'Chapel')

assert.deepEqual(await run('caption', { text: '[Applause]' }), [])
assert.deepEqual(await run('reset_transcript'), [])
assert.deepEqual(await run('mark_video_start'), [])
assert.deepEqual(await run('clear_captions'), [])
assert.deepEqual(await run('refresh_audio_devices'), [])
assert.deepEqual(await run('set_audio_gain', { mode: 'up', db: 30 }), [], 'gain is clamped, not rejected')
assert.deepEqual(await run('set_profanity_toggle'), [])
assert.deepEqual(await run('propresenter_clear'), [])

// Unconfigured outputs refuse to switch on, and say why.
assert.match((await run('youtube_captions', { mode: 'on' }))[0], /ingestion URL/)
assert.deepEqual(await run('propresenter', { mode: 'on' }), [])
self.state = await snapshot()
assert.equal(self.state.propresenter.enabled, true)
assert.deepEqual(await run('propresenter', { mode: 'toggle' }), [])
assert.match((await run('tunnel', { mode: 'on' }))[0], /tunnel token/)
assert.deepEqual(await run('youtube_captions', { mode: 'off' }), [])

assert.deepEqual(await run('next_event', { title: 'Evening session' }), [])
self.state = await snapshot()
assert.equal(self.state.event.state, 'starting')
assert.equal(self.state.event.title, 'Evening session')

// Every feedback evaluates against a real snapshot without throwing.
for (const [id, fb] of Object.entries(getFeedbacks(self))) {
	const options = Object.fromEntries((fb.options ?? []).map((o) => [o.id, o.default]))
	assert.equal(typeof fb.callback({ options }), 'boolean', id)
}
assert.equal(getFeedbacks(self).event_state.callback({ options: { state: 'starting' } }), true)

// Every variable is defined and every preset points at real actions/feedbacks.
const values = valuesFromSnapshot(self.state)
assert.deepEqual(Object.keys(values).sort(), variableDefinitions.map((d) => d.variableId).sort())
const fbIds = Object.keys(getFeedbacks(self))
for (const [id, p] of Object.entries(getPresets('captions'))) {
	for (const step of p.steps) for (const a of step.down) assert.ok(actions[a.actionId], `${id}: ${a.actionId}`)
	for (const f of p.feedbacks) assert.ok(fbIds.includes(f.feedbackId), `${id}: ${f.feedbackId}`)
	assert.ok(!JSON.stringify(p).includes('campus-captions:'), `${id} uses the connection label`)
}

// A wrong token is reported as such.
self.conn.token = 'wrong'
assert.match((await run('clear_captions'))[0], /operator token/)

console.log('smoke ok')

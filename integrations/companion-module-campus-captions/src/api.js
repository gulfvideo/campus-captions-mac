import WebSocket from 'ws'

const REQUEST_TIMEOUT_MS = 5000

// The relay closes the status socket with a specific code rather than just
// dropping it, so tell the operator which of the three walls they hit instead
// of leaving "disconnected" on screen.
const CLOSE_REASONS = {
	4401: 'Operator token rejected. Check the token in this connection\'s settings.',
	4403: 'Refused: request did not look same-origin.',
	4404: 'Refused: this looks like the public tunnel hostname. Use the LAN address.',
}

export function baseUrl(config) {
	return `http://${config.host}:${config.port}`
}

/**
 * One authenticated POST. Returns the parsed body, or throws with a message
 * worth putting in front of a person.
 */
export async function postJson(config, path, body) {
	const response = await fetch(`${baseUrl(config)}${path}`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${config.token}`,
			'Content-Type': 'application/json',
		},
		body: JSON.stringify(body),
		signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
	})

	let payload = null
	try {
		payload = await response.json()
	} catch {
		// A non-JSON body is only interesting when the request also failed.
	}

	if (!response.ok) {
		const detail = payload?.detail || `HTTP ${response.status}`
		if (response.status === 401) {
			throw new Error(`${detail} - check the operator token`)
		}
		if (response.status === 404) {
			throw new Error(`${detail} - the relay refuses control through a public hostname`)
		}
		throw new Error(detail)
	}
	return payload
}

/**
 * The relay's status feed: one complete snapshot roughly every 0.4s, pushed.
 *
 * Polling /api/status would work, but the socket already exists and costs the
 * relay nothing extra, so feedbacks stay current without this module adding
 * load to a machine that is also running speech recognition.
 */
export class StatusSocket {
	#config
	#onSnapshot
	#onStateChange
	#socket = null
	#reconnectTimer = null
	#attempts = 0
	#closed = false

	constructor(config, { onSnapshot, onStateChange }) {
		this.#config = config
		this.#onSnapshot = onSnapshot
		this.#onStateChange = onStateChange
	}

	connect() {
		this.#closed = false
		this.#open()
	}

	close() {
		this.#closed = true
		if (this.#reconnectTimer) {
			clearTimeout(this.#reconnectTimer)
			this.#reconnectTimer = null
		}
		if (this.#socket) {
			// Drop the handlers first: a close we asked for should not look
			// like a dropped link and schedule a reconnect.
			this.#socket.removeAllListeners()
			this.#socket.close()
			this.#socket = null
		}
	}

	#open() {
		const url = `ws://${this.#config.host}:${this.#config.port}/ws/status`
		const socket = new WebSocket(url, {
			headers: { Authorization: `Bearer ${this.#config.token}` },
			handshakeTimeout: REQUEST_TIMEOUT_MS,
		})
		this.#socket = socket

		socket.on('open', () => {
			this.#attempts = 0
			this.#onStateChange({ ok: true })
		})

		socket.on('message', (raw) => {
			let snapshot
			try {
				snapshot = JSON.parse(raw.toString())
			} catch {
				return // a frame we cannot read is not worth tearing the link down for
			}
			this.#onSnapshot(snapshot)
		})

		socket.on('error', (error) => {
			// 'close' always follows, and carries the code worth reporting, so
			// this only needs to stop the error reaching the process.
			this.#lastError = error?.message
		})

		socket.on('close', (code) => {
			this.#socket = null
			const reason = CLOSE_REASONS[code]
			this.#onStateChange({ ok: false, fatal: Boolean(reason), message: reason || this.#lastError })
			// An auth or policy refusal will refuse again just as fast. Retry
			// anyway, but slowly, so fixing the token in settings recovers on
			// its own without hammering the relay in the meantime.
			this.#scheduleReconnect(reason ? 15000 : null)
		})
	}

	#scheduleReconnect(fixedDelay) {
		if (this.#closed || this.#reconnectTimer) return
		this.#attempts += 1
		const delay = fixedDelay ?? Math.min(1000 * 2 ** (this.#attempts - 1), 15000)
		this.#reconnectTimer = setTimeout(() => {
			this.#reconnectTimer = null
			if (!this.#closed) this.#open()
		}, delay)
	}
}

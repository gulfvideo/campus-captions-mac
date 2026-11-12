import { InstanceBase, InstanceStatus, runEntrypoint } from '@companion-module/base'
import { StatusSocket } from './api.js'
import { configFields } from './config.js'
import { getActions } from './actions.js'
import { getFeedbacks } from './feedbacks.js'
import { getPresets } from './presets.js'
import { variableDefinitions, valuesFromSnapshot } from './variables.js'

class CampusCaptionsInstance extends InstanceBase {
	/**
	 * The most recent status snapshot, or null before the first one lands.
	 * Actions and feedbacks both read it; nothing else writes it.
	 */
	state = null

	/**
	 * Address and token together. The token arrives separately from the rest
	 * of the config - it is a `secret-text` field, so Companion keeps it in
	 * the secrets store - and everything that talks to the relay wants both,
	 * so they are recombined here once rather than at each call site.
	 */
	conn = { host: '', port: 8000, token: '' }

	#socket = null

	async init(config, _isFirstInit, secrets) {
		this.#absorb(config, secrets)

		this.setActionDefinitions(getActions(this))
		this.setFeedbackDefinitions(getFeedbacks(this))
		this.setVariableDefinitions(variableDefinitions)
		this.setPresetDefinitions(getPresets(this.label))

		this.#start()
	}

	async configUpdated(config, secrets) {
		this.#absorb(config, secrets)
		// Also called when the connection is renamed: preset text carries the label.
		this.setPresetDefinitions(getPresets(this.label))
		this.#stop()
		this.#start()
	}

	#absorb(config, secrets) {
		this.config = config ?? {}
		this.conn = {
			host: this.config.host ?? '',
			port: this.config.port ?? 8000,
			token: secrets?.token ?? '',
		}
	}

	async destroy() {
		this.#stop()
	}

	getConfigFields() {
		return configFields
	}

	#start() {
		if (!this.conn.host || !this.conn.token) {
			this.updateStatus(InstanceStatus.BadConfig, 'Address and operator token are required')
			return
		}

		this.updateStatus(InstanceStatus.Connecting)

		this.#socket = new StatusSocket(this.conn, {
			onSnapshot: (snapshot) => this.#applySnapshot(snapshot),
			onStateChange: ({ ok, fatal, message }) => {
				if (ok) {
					this.updateStatus(InstanceStatus.Ok)
					this.log('info', 'Connected to the caption relay')
					return
				}
				// A refused token is the operator's to fix and will not fix
				// itself, so it is an error rather than a passing disconnect.
				this.updateStatus(
					fatal ? InstanceStatus.AuthenticationFailure : InstanceStatus.Disconnected,
					message
				)
				if (message) this.log(fatal ? 'error' : 'warn', message)
				this.#clearState()
			},
		})
		this.#socket.connect()
	}

	#stop() {
		this.#socket?.close()
		this.#socket = null
		this.#clearState()
	}

	#applySnapshot(snapshot) {
		this.state = snapshot
		this.setVariableValues(valuesFromSnapshot(snapshot))
		// Cheap: Companion only re-renders the keys whose result actually
		// changed, so calling this on every snapshot is fine.
		this.checkFeedbacks()
	}

	#clearState() {
		if (!this.state) return
		this.state = null
		// Blank rather than stale. A key still reading "42 viewers" after the
		// link dropped is worse than one reading nothing.
		this.setVariableValues(
			Object.fromEntries(variableDefinitions.map(({ variableId }) => [variableId, '']))
		)
		this.checkFeedbacks()
	}
}

runEntrypoint(CampusCaptionsInstance, [])

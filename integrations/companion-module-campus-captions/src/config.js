import { Regex } from '@companion-module/base'

export const configFields = [
	{
		type: 'static-text',
		id: 'intro',
		width: 12,
		label: 'Campus Captions',
		value:
			'Point this at the Mac running Campus Captions (<code>127.0.0.1</code> if it is this Mac). ' +
			'For the token, click the Campus Captions menu bar icon &rarr; <b>Copy Token for Companion</b>, then paste it below.',
	},
	{
		type: 'textinput',
		id: 'host',
		label: 'Address',
		width: 6,
		default: '127.0.0.1',
		regex: Regex.HOSTNAME,
		required: true,
		tooltip:
			'The Mac\'s LAN address, e.g. 10.0.1.24. Control is refused through the ' +
			'public tunnel hostname by design, so a tunnel domain will not work here.',
	},
	{
		type: 'number',
		id: 'port',
		label: 'Port',
		width: 3,
		default: 8000,
		min: 1,
		max: 65535,
		required: true,
	},
	{
		// secret-text, not textinput: Companion reports the whole config object
		// to its web UI but only the *keys* of the secrets store, and this
		// field's value goes to the latter. Same rule the relay's own settings
		// page follows - a saved secret never travels back to a browser.
		type: 'secret-text',
		id: 'token',
		label: 'Operator token',
		width: 12,
		required: true,
		tooltip: 'Menu bar icon → Copy Token for Companion.',
	},
]

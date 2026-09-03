import { Regex } from '@companion-module/base'

export const configFields = [
	{
		type: 'static-text',
		id: 'intro',
		width: 12,
		label: 'Campus Captions',
		value:
			'Point this at the Mac running Campus Captions. The operator token is ' +
			'<code>CAPTIONS_OPERATOR_TOKEN</code>, found in the app&apos;s <code>.env</code> file &mdash; ' +
			'use the menu bar&apos;s <b>Show Config Folder in Finder</b> to open the folder it lives in.',
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
		tooltip: "CAPTIONS_OPERATOR_TOKEN from the app's .env file.",
	},
]

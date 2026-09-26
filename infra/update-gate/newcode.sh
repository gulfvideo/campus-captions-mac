#!/bin/sh
# Issue one tester an access code. Prints the code to hand over, and the
# command that authorises it.
#
#   ./newcode.sh "Jane at the north campus"
#
# The code is shown once, here, and never stored anywhere in plaintext - not in
# KV, not in the Worker, not in this repository. Losing it means issuing a new
# one, which is the correct trade for a credential.
set -eu
label="${1:?usage: newcode.sh \"tester name\"}"
code=$(openssl rand -hex 16)
key=$(printf %s "$code" | shasum -a 256 | cut -d' ' -f1)

printf '\nGive this to %s:\n\n    %s\n\n' "$label" "$code"
printf 'Authorise it:\n\n    npx wrangler kv key put --binding CODES \\\n'
printf '      "%s" \x27{"tester":"%s","issued":"%s"}\x27 --remote\n\n' \
  "$key" "$label" "$(date +%F)"
printf 'Revoke it later:\n\n    npx wrangler kv key delete --binding CODES "%s" --remote\n\n' "$key"

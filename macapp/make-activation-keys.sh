#!/bin/sh
# Generates the Ed25519 keypair activation uses.
#
#   macapp/make-activation-keys.sh
#
# Prints the public key to paste into CampusCaptions.swift and the private key
# to store as a Cloudflare Worker secret. The private half is shown once and
# written nowhere - if it is lost, generate a new pair and ship a new build;
# every code issued under the old one stops verifying, which is the correct
# behaviour for a signing key you no longer control.
set -eu
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

openssl genpkey -algorithm ed25519 -out "$tmp/priv.pem" 2>/dev/null
# The raw 32 bytes are the tail of the DER encoding for Ed25519; CryptoKit's
# rawRepresentation and the Worker's WebCrypto import both want those, not PEM.
pub=$(openssl pkey -in "$tmp/priv.pem" -pubout -outform DER 2>/dev/null | tail -c 32 | base64)
priv=$(openssl pkey -in "$tmp/priv.pem" -outform DER 2>/dev/null | tail -c 32 | base64)

cat <<EOF

Public key - paste into Activation.publicKey in macapp/CampusCaptions.swift:

    $pub

Private key - store as a Worker secret, then forget it:

    cd infra/update-gate && npx wrangler secret put ACTIVATION_PRIVATE_KEY
    (paste) $priv

EOF

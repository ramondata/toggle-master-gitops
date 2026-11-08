#!/usr/bin/env bash
# Binario oficial, versao e checksum fixos; instalacao somente para este usuario.
set -euo pipefail
version=2.103.3
[[ "$(uname -s)" == Linux ]] || { echo 'Execute este instalador no AWS CloudShell (Linux).' >&2; exit 2; }
case "$(uname -m)" in
  x86_64) arch=amd64; digest=4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d ;;
  aarch64|arm64) arch=arm64; digest=ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63 ;;
  *) echo 'Arquitetura nao suportada.' >&2; exit 2 ;;
esac
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
archive="gh_${version}_linux_${arch}.tar.gz"
curl -fsSL --connect-timeout 10 --max-time 120 "https://github.com/cli/cli/releases/download/v$version/$archive" -o "$work/$archive"
printf '%s  %s\n' "$digest" "$work/$archive" | sha256sum --check --status
tar -xzf "$work/$archive" -C "$work"
mkdir -p "$HOME/.local/bin"
install -m 755 "$work/gh_${version}_linux_${arch}/bin/gh" "$HOME/.local/bin/gh"
"$HOME/.local/bin/gh" --version

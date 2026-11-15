#!/usr/bin/env bash
# Run only from a web release directory, never from the engine deployment checkout.
set -euo pipefail
die() { echo "$1" >&2; exit 1; }
[[ $# == 4 && "$1" == --expected-sha && "$3" == --config ]] || die 'Usage: deploy_web.sh --expected-sha SHA --config /absolute/web.env'
sha="$2"
config="$4"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die 'Expected a full commit SHA.'
[[ "$config" == /* && -f "$config" ]] || die 'An existing absolute web config path is required.'
for executable in docker python3 flock; do command -v "$executable" >/dev/null || die "Missing $executable"; done
[[ -f docker-compose.web.yml && -f images.tar.gz ]] || die 'Missing web release files.'
# Also serializes manual invocations outside Actions. Never touches the engine lock.
exec 9>"$(dirname "$config")/deploy.lock"
flock -n 9 || die 'Another web deployment is in progress.'
export WEB_IMAGE_TAG="$sha"
compose=(docker compose --env-file "$config" -p crypto-trading-web -f docker-compose.web.yml)
"${compose[@]}" config --quiet
# Reject accidental engine files/extra services and missing/broad credentials before cutover.
"${compose[@]}" config --format json | python3 -I -c '
import json, os, re, subprocess, sys
c = json.load(sys.stdin)
assert set(c["services"]) == {"backend", "web"}, "Unexpected services"
b = c["services"]["backend"]
assert not b.get("ports"), "Backend must not publish ports"
assert b["environment"]["BACKEND_DB_USERNAME"] == "backend_reader", "Use backend_reader"
assert re.fullmatch(r"jdbc:postgresql://[A-Za-z0-9._-]+:[0-9]+/[A-Za-z0-9_-]+", b["environment"]["BACKEND_DB_URL"]), "Use a plain Docker-network JDBC URL without credential overrides"
for secret in c["secrets"].values():
    path = secret["file"]
    assert os.path.isfile(path) and os.path.getsize(path) > 0, "Missing or empty secret file"
subprocess.run(["docker", "network", "inspect", c["networks"]["database"]["name"]], check=True, stdout=subprocess.DEVNULL)
'
docker load --input images.tar.gz >/dev/null
for image in crypto-trading-web-backend crypto-trading-web-frontend; do
    revision="$(docker image inspect "$image:$sha" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
    [[ "$revision" == "$sha" ]] || die 'Image revision does not match requested SHA.'
done
# Syntax, certificate/key readability and key matching are validated before replacement.
# A network-less container avoids taking the running proxy's fixed network address.
"${compose[@]}" config --format json | python3 -I -c '
import json, subprocess, sys
c = json.load(sys.stdin)
args = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:size=32m,mode=1777"]
for name in ("tls_certificate", "tls_key"):
    args += ["--mount", "type=bind,src=" + c["secrets"][name]["file"] + ",dst=/run/secrets/" + name + ",readonly"]
args += [c["services"]["web"]["image"], "-t"]
subprocess.run(args, check=True)
'
echo 'Preflight passed. Replacing only the two web services.'
"${compose[@]}" up -d --no-build --wait --wait-timeout 180 backend web
# Real certificate trust/hostname checks happen at the public URL in the workflow.
# Do not destroy volumes, migrate schemas, change roles, or automatically roll back on failure.
echo "Web containers healthy at release $sha. Existing sessions require login again."

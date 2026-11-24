#!/bin/sh
set -eu
umask 022
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; exit 1; }
# Benign controls: source and deliberate canary available only in mounted source.
[ "$(cat /source/fixture.txt)" = "synthetic-source" ] && pass source_read || fail source_read
[ "$(cat /source/visible-canary.txt)" = "FAKE-CREDENTIAL-POSITIVE-CONTROL" ] && pass visible_canary || fail visible_canary
printf 'workspace-ok' > /work/result.txt
chmod 644 /work/result.txt
[ "$(cat /work/result.txt)" = workspace-ok ] && pass workspace_write || fail workspace_write
printf 'temporary-ok' > /tmp/test
pass temporary_write
# Negative controls: no host paths or sockets are mounted, and writes outside /work fail.
[ ! -e "$HOST_CANARY" ] && pass host_canary_absent || fail host_canary_absent
[ ! -e /home/kensei/.config/code-shipper/tokens/fixer ] && pass fixer_path_absent || fail fixer_path_absent
[ ! -e /home/kensei/.config/gh/hosts.yml ] && pass github_path_absent || fail github_path_absent
[ ! -e /home/kensei/.hermes/.env ] && pass hermes_path_absent || fail hermes_path_absent
[ ! -e /home/kensei/.ssh/id_ed25519 ] && pass ssh_path_absent || fail ssh_path_absent
[ ! -e /var/run/docker.sock ] && [ ! -e /run/user/1001/docker.sock ] && pass docker_sockets_absent || fail docker_sockets_absent
[ ! -e /run/dbus/system_bus_socket ] && pass system_bus_absent || fail system_bus_absent
if (printf forbidden > /source/blocked-write) 2>/dev/null; then fail source_read_only; else pass source_read_only; fi
if (printf forbidden > /root-write) 2>/dev/null; then fail root_read_only; else pass root_read_only; fi
if env | /bin/grep -E '^(GH_|GITHUB_|.*TOKEN=|.*API_KEY=|SSH_AUTH_SOCK=|DBUS_SESSION_BUS_ADDRESS=)'; then fail no_auth_env; else pass no_auth_env; fi
[ "$(/bin/grep '^NoNewPrivs:' /proc/self/status | /bin/busybox awk '{print $2}')" = 1 ] && pass no_new_privileges || fail no_new_privileges
[ "$(/bin/grep '^CapEff:' /proc/self/status | /bin/busybox awk '{print $2}')" = 0000000000000000 ] && pass capabilities_dropped || fail capabilities_dropped
[ "$(/bin/grep '^Seccomp:' /proc/self/status | /bin/busybox awk '{print $2}')" = 2 ] && pass seccomp_enabled || fail seccomp_enabled
[ ! -d /proc/"$HOST_PID" ] && pass host_process_absent || fail host_process_absent
[ "$(readlink /proc/self/ns/net)" != "$HOST_NET_NS" ] && pass network_namespace_separate || fail network_namespace_separate
# No non-loopback route, not a DNS timeout mistaken for isolation.
if /bin/busybox awk 'NR>1 && $1 != "lo" {bad=1} END {exit !bad}' /proc/net/route; then fail no_egress_route; else pass no_egress_route; fi
printf 'DONE\n'

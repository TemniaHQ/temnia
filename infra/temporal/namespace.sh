#!/bin/sh
# Registers the default namespace once the server answers. Idempotent. POSIX sh.
set -eu
: "${TEMPORAL_ADDRESS:=temporal:7233}" "${DEFAULT_NAMESPACE:=default}" "${DEFAULT_NAMESPACE_RETENTION:=72h}"

until temporal operator cluster health --address "${TEMPORAL_ADDRESS}" >/dev/null 2>&1; do
  echo "waiting for temporal at ${TEMPORAL_ADDRESS}"
  sleep 2
done

if temporal operator namespace describe --address "${TEMPORAL_ADDRESS}" --namespace "${DEFAULT_NAMESPACE}" >/dev/null 2>&1; then
  echo "namespace ${DEFAULT_NAMESPACE} exists"
else
  temporal operator namespace create --address "${TEMPORAL_ADDRESS}" --namespace "${DEFAULT_NAMESPACE}" --retention "${DEFAULT_NAMESPACE_RETENTION}"
  echo "namespace ${DEFAULT_NAMESPACE} created"
fi

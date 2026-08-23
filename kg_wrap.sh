#!/bin/sh
[ -n "$HTTPS_PROXY" ] || export HTTPS_PROXY=http://172.17.0.1:7890
[ -n "$HTTP_PROXY" ] || export HTTP_PROXY=http://172.17.0.1:7890
exec node /ql/data/scripts/kgcheckin/main.js

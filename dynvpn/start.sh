#!/bin/sh

# helper script to start a dynvpn instance

if [ $# == 0 ]; then 
    PYTHON=python3.11
else
    PYTHON=$1
fi

set -o nounset

: ${PYTHONPATH:=""}
export PYTHONPATH=./src:$PYTHONPATH


$PYTHON -m dynvpn --local-config local.yml --global-config global.yml



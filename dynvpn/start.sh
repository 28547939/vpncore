#!/bin/sh

# helper script to start a dynvpn instance

if [ $# == 1 ]; then 
    PYTHON=python3.11
    SITE_ID=$1
else
    PYTHON=$1
    SITE_ID=$2
fi

set -o nounset

: ${PYTHONPATH:=""}
export PYTHONPATH=./src:$PYTHONPATH


$PYTHON -m dynvpn --site-id $SITE_ID --local-config local.yml --global-config global.yml



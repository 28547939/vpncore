#!/bin/sh
#

set -o nounset

NAME=$1

if [ -f $(dirname $0)/../pid/$NAME ]; then
    exit 0
else
    exit 1
fi



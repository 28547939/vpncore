#!/bin/sh

# to be run in the host

set -o nounset

NAME=$1
DEFAULTROUTE=$2
CONFIG_DIR=$3

jexec $NAME route add 192.168.0.0/16 $DEFAULTROUTE

for dir in $CONFIG_DIR/* ; do
    #echo $dir
    for f in $dir/* ; do
        grep -E '^remote' "$f" | sed -nEe 's/^remote ([^[:space:]]+) .+$/\1/p' | sort | uniq | \
        while read x ; do
            jexec $NAME route add "$x" $DEFAULTROUTE
        done
    done
done

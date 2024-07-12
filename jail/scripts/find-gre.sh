#!/bin/sh

set -o nounset

find_gre () {
    cat $GRECONFIG | \
        jq -er '[ .[] | [ .inet[1], .tunnel[1] ] ] | map(select(.[1] == "'"$1"'")) | .[0][0]'
}

#!/usr/bin/env bash

if [ $# == 0 ] ;
then
    pwd | xclip -sel clip
elif [ $1 == "-d" ] ;
then
    pwd | xclip
elif [ $1 == "--help" ] ;
then 
echo "`basename $0` (Yank Path): Copies path to clipboard
----------------------
Possible Arguments:
----------------------
no args     copy to clipboard with xclip 
-d          copy to default xclip clipboard
--help      showing this menu"
else
    echo "Invalid argument $1, try: `basename $0` --help"
fi

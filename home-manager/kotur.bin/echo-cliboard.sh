#!/usr/bin/env bash

if [ $# == 0 ] ;
then
    echo `xclip -sel clip -o`
elif [ $1 == "-d" ] ;
then
    echo `xclip -o`
elif [ $1 == "--help" ] ;
then 
echo "`basename $0` (ec): Echoes clipboard
----------------------
Possible Arguments:
----------------------
no args     echo clipboard with xclip 
-d          echo default xclip clipboard
--help      showing this menu"
else
    echo "Invalid argument $1, try: `basename $0` --help"
fi

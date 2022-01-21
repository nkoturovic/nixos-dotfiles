#!/bin/sh

pushd ~/.dotfiles
    home-manager switch -f ./users/nix/home.nix
popd

#!/bin/sh

pushd ~/.dotfiles
sudo nixos-rebuild switch --flake .#kotur-pc
popd

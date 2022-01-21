#!/bin/sh

pushd ~/.dotfiles
nix build .#homeManagerConfigurations.kotur.activationPackage
./result/activate
popd

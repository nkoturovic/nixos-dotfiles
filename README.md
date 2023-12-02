## Instructions

* home-manager is ~/.config/home-manager
* configuration.nix is /etc/nixos/configuration.nix

### Install base system

* Install basic nixos
  * Ex. GUI install, but chose minimal
  * You can add encryption as well
* After installing copy configuration.nix to the /mnt
* Boot the system
* Copy configuration.nix to /etc/nixos/
* run nixos-rebuild switch

### Install home-manager

* Follow the standalone install tutorial

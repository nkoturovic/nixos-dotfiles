## Instructions

* Make symbolic links configuration.nix -> /etc/nixos/configuration.nix
* home-manager is ~/.config/home-manager

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
* Make symbolic links home-manager -> /.config/home-manager
* Run home-manager switch

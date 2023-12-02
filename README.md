## Instructions

* Make symbolic links configuration.nix -> /etc/nixos/configuration.nix
* home-manager is ~/.config/home-manager

### Install base system

* Install basic nixos
  * Ex. GUI install, but chose minimal
  * You can add encryption as well
* After installing copy configuration.nix to the /mnt
* Boot the system
* Symlink configuration.nix to /etc/nixos/configuration.nix
* run nixos-rebuild switch

### Install home-manager

* Follow the standalone install tutorial
* Make symbolic links home-manager -> /.config/home-manager
* Run home-manager switch

### Colors

* if color scheme is not dark, run: configure-gtk

### TODO

* Move sway to home-manager
* Use flakes for building configuration.nix and home-manager

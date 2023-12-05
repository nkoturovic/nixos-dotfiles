## New instructions (flakes)

Taken from: [github.com/Misterio77/nix-starter-configs](https://github.com/Misterio77/nix-starter-configs)

```sh
export NIX_CONFIG="experimental-features = nix-command flakes"
```

To install from the installer run:

```sh
# On live CD
nixos-install --flake .#kotur-pc

# On distro
sudo nixos-rebuild switch --flake .#kotur-pc
```

To avoid using `.#kotur-pc`, you can create a symlink
(this is possible because the current hostname is default output inspected by nixos-rebuild)
kotur-pc -> (nixos)

```sh
cd /etc/
sudo ln -s /home/kotur/.nixos-dotfiles nixos
sudo nixos-rebuild switch
```

Home manager goes similar

```sh
home-manager switch --flake .#kotur
```

Similarly, to avoid using `.#kotur`, you can create a symlink
(username is default output inspected by home manager) username -> home-manager

```sh
cd /home/kotur/.config
ln -s /home/kotur/.nixos-dotfiles home-manager
home-manager switch
```

If you think about symlinks for nixos and home-manager, it should be  the first thing you create,
even before running the installer/home manager for the first time ;)
 
Enjoy!!

## Old Instructions

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

```sh
nix-channel --add https://github.com/nix-community/home-manager/archive/release-23.11.tar.gz home-manager
nix-channel --update
```

* Be sure to logout/login to continue


```sh
nix-shell '<home-manager>' -A install
```

* Make symbolic links home-manager -> /.config/home-manager
* Run home-manager switch

### Colors

* if color scheme is not dark, run: configure-gtk

### TODO

* [x] Use flakes for building configuration.nix and home-manager
* [ ] Move sway to home-manager
* [ ] Fix lock screen and sleep


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

To avoid using .#kotur-pc, you can create a symlink - is it possible??

```sh
cd /etc/
sudo ln -s /home/kotur/.nixos-dotfiles nixos
sudo nixos-rebuild switch
```

Home manager goes similar

```sh
home-manager switch --flake .#kotur@kotur-pc
```

And to avoid using the hostname -- is it possible?

```sh
ln -s /home/kotur/.nixos-dotfiles home-manager
home-manager switch
```

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

* Move sway to home-manager
* Use flakes for building configuration.nix and home-manager




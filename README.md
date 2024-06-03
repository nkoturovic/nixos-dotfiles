## New instructions (flakes)

Taken from: [github.com/Misterio77/nix-starter-configs](https://github.com/Misterio77/nix-starter-configs)

```sh
export NIX_CONFIG="experimental-features = nix-command flakes"
```

Be sure that `<hostname>` is set to use right `configuration.nix` and `hardware-configuration.nix`
This should be done through `nixos-generate-config`, it generate only `hardware-configuration` if `configuration.nix` exists.
But, if `configuration.nix` is initially generated it won't do any harm, as you can only copy `hardware-configuration.nix`, 
put it to `./nixos/` folder and make sure to change include directive for it in the original `./nixos/kotur-pc.configuration.nix`

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

If home-manager is not installed on the system, use:

```sh
nix shell nixpkgs#home-manager
# and then run the previous command
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

The only downside is that system and home-manager installers need to be ran separately
 
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
nix-channel --add https://github.com/nix-community/home-manager/archive/release-24.05.tar.gz home-manager
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

### Commands

Removing old generations

```sh
sudo nixos-collect-gabage [-d]
nixos-collect-gabage [-d] # clean home-manager generations
```

### Registry

Be sure that you set correct version of nixpkgs in flake registry (user). 
Global one can't be overrider, it comes from: https://github.com/NixOS/flake-registry
You can either override system, or user.. This determines which version of nixpkgs is default for flakes

### TODO

* [x] Use flakes for building configuration.nix and home-manager
* [ ] Consider adding home-manager as a nixos module
* [ ] Move sway to home-manager
* [ ] Fix lock screen and sleep


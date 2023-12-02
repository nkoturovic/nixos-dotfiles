# Manage network connections
nmtui

# Configure display
wdisplays

cryptsetup luksOpen /dev/sda2 nixos
sudo mount /dev/mapper/nixos /mnt

# Update theme
configure-gtk

# List installed packages
nix-env -qa --installed "*"

# Run python server
python -m http.server 8000

# Clean-up nix store
sudo nix-collect-garbage --delete-old

# prune old gens
sudo nixos-rebuild switch/boot


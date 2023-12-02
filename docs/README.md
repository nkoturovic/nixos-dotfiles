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

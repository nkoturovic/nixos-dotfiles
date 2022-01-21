{ config, pkgs, ... }:

{
  # Home Manager needs a bit of information about you and the
  # paths it should manage.
  home.username = "kotur";
  home.homeDirectory = "/home/kotur";

  # This value determines the Home Manager release that your
  # configuration is compatible with. This helps avoid breakage
  # when a new Home Manager release introduces backwards
  # incompatible changes.
  #
  # You can update Home Manager without changing this value. See
  # the Home Manager release notes for a list of state version
  # changes in each release.
  # home.stateVersion = pkgs.lib.trivial.release; # "21.11";
 

  home.packages = with pkgs; [
      alacritty
      neovim
      git
      git-crypt
      gnupg
      neofetch
      pinentry_qt
  ];

  home.file = {
    ".config/alacritty/alacritty.yaml".text = ''
      env:
        TERM: xterm-256color
    '';
  };

  # Let Home Manager install and manage itself.
  programs.home-manager.enable = true;
  programs.gpg = {
      enable = true;
  };

  services.gpg-agent = {
      enable = true;
      pinentryFlavor = "qt";
  };
}

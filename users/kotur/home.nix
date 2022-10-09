{ config, pkgs, lib, ... }:

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
  home.stateVersion = "22.05";

  home.packages = with pkgs; [ 
    # neovim
    meld
    trash-cli
    nodejs
    rustup
    python310
    python310Packages.pip
    gh
    openssl
  ];

  # Raw configuration files
  home.file.".p10k.zsh".source = ./dotfiles/.p10k.zsh;
  home.file.".commonrc".source = ./dotfiles/.commonrc;
  home.file.".profile".source = ./dotfiles/.profile;
  home.file.".zshenv".text = ''
    source .profile
    source .p10k.zsh
    source .commonrc
  '';

  # Let Home Manager install and manage itself.
  programs.home-manager.enable = true;

  programs.git = {
    enable = true;
    userName = "Nebojsa Koturovic";
    userEmail = "contact@kotur.me";
    aliases = {
      st = "status";
      dt = "!git difftool --dir-diff --no-symlinks -t meld";
      hist = "!git --no-pager log --graph --pretty=format:'%C(green)%h%C(reset) - %C(italic)%C(cyan)%an%C(reset) (%C(yellow)%ar%C(reset))%n%C(bold)%s%C(reset)%n%b'";
      lg = "!git --no-pager log --graph --pretty=format:'%C(green)%h%C(reset) - %C(italic)%C(cyan)%an%C(reset) (%C(yellow)%ar%C(reset)): %s'";
    };
    # Global Git config
    extraConfig = {
      core = {
        editor = "lvim";
        pager = "lvim";
        whitespace = "trailing-space,space-before-tab";
      };

      # commit.gpgsign = "true";
      # gpg.program = "gpg2";

      # protocol.keybase.allow = "always";
      credential.helper = "!gh auth git-credential";
      # pull.rebase = "false";
    }; 
  };

  programs.zsh = {
    enable = true;
    # zsh conf here

    zplug = {
      enable = true;
      plugins = [
        { name = "zsh-users/zsh-autosuggestions"; } # Simple plugin installation
        { name = "romkatv/powerlevel10k"; tags = [ as:theme depth:1 ]; } # Installations with additional options. For the list of options, please refer to Zplug README.
      ];
    };
  };
}

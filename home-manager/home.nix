{ pkgs, ... }:

{
  # Home Manager needs a bit of information about you and the paths it should
  # manage.
  home.username = "kotur";
  home.homeDirectory = "/home/kotur";

  # This value determines the Home Manager release that your configuration is
  # compatible with. This helps avoid breakage when a new Home Manager release
  # introduces backwards incompatible changes.
  #
  # You should not change this value, even if you update Home Manager. If you do
  # want to update the value, then make sure to first check the Home Manager
  # release notes.
  home.stateVersion = "23.11"; # Please read the comment before changing.

  # The home.packages option allows you to install Nix packages into your
  # environment.
  home.packages = with pkgs; [
    # # Adds the 'hello' command to your environment. It prints a friendly
    # # "Hello, world!" when run.
    # pkgs.hello

    # # It is sometimes useful to fine-tune packages, for example, by applying
    # # overrides. You can do that directly here, just don't forget the
    # # parentheses. Maybe you want to install Nerd Fonts with a limited number of
    # # fonts?
    # (pkgs.nerdfonts.override { fonts = [ "FantasqueSansMono" ]; })

    # # You can also create simple shell scripts directly inside your
    # # configuration. For example, this adds a command 'my-hello' to your
    # # environment:
    # (pkgs.writeShellScriptBin "my-hello" ''
    #   echo "Hello, ${config.home.username}!"
    # '')
    zsh
    neovim
    #jetbrains-mono
    inconsolata-nerdfont
    gh
  ];

  # Home Manager is pretty good at managing dotfiles. The primary way to manage
  # plain files is through 'home.file'.
  home.file = {
    # # Building this configuration will create a copy of 'dotfiles/screenrc' in
    # # the Nix store. Activating the configuration will then make '~/.screenrc' a
    # # symlink to the Nix store copy.
    # ".screenrc".source = dotfiles/screenrc;

    # # You can also set the file content immediately.
    # ".gradle/gradle.properties".text = ''
    #   org.gradle.console=verbose
    #   org.gradle.daemon.idletimeout=3600000
    # '';o

    # Raw configuration files
    ".p10k.zsh".source = ./dotfiles/.p10k.zsh;
    ".commonrc".source = ./dotfiles/.commonrc;
    ".profile".source = ./dotfiles/.profile;
    ".zshenv".text = ''
       source ~/.profile
       source ~/.p10k.zsh
       source ~/.commonrc
    '';
    ".config/sway/config".source = ./dotfiles/sway;
    ".config/alacritty/alacritty.toml".source = ./dotfiles/alacritty.toml;
  };

  # Home Manager can also manage your environment variables through
  # 'home.sessionVariables'. If you don't want to manage your shell through Home
  # Manager then you have to manually source 'hm-session-vars.sh' located at
  # either
  #
  #  ~/.nix-profile/etc/profile.d/hm-session-vars.sh
  #
  # or
  #
  #  /etc/profiles/per-user/kotur/etc/profile.d/hm-session-vars.sh
  #
  home.sessionVariables = {
    # EDITOR = "emacs";
  };

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
   
    zplug = {
      enable = true;
      plugins = [
        { name = "zsh-users/zsh-autosuggestions"; }
        { name = "romkatv/powerlevel10k"; tags = [ as:theme depth:1 ]; }
      ];
    };
  };
}

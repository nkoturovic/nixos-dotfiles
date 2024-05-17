# Use this to configure your home environment (it replaces ~/.config/nixpkgs/home.nix)
{ inputs
, lib
, config
, pkgs
, pkgs-unstable
, ...
}: {

  # Home Manager needs a bit of information about you and the paths it should manage.
  home = {
    username = "kotur";
    homeDirectory = "/home/kotur";
  };

  home.stateVersion = "23.11";

  nixpkgs = {
    # You can add overlays here
    overlays = [
      # If you want to use overlays exported from other flakes:
      # neovim-nightly-overlay.overlays.default

      # Or define it inline, for example:
      # (final: prev: {
      #   hi = final.hello.overrideAttrs (oldAttrs: {
      #     patches = [ ./change-hello-to-hi.patch ];
      #   });
      # })
    ];
    # Configure your nixpkgs instance
    config = {
      # Disable if you don't want unfree packages
      allowUnfree = true;
      # Workaround for https://github.com/nix-community/home-manager/issues/2942
      allowUnfreePredicate = _: true;
    };
  };

  home.packages = with pkgs; [
    # It is sometimes useful to fine-tune packages, for example, by applying
    # overrides. You can do that directly here, just don't forget the
    # parentheses. Maybe you want to install Nerd Fonts with a limited number of fonts?
    # (pkgs.nerdfonts.override { fonts = [ "FantasqueSansMono" ]; })
    # You can also create simple shell scripts directly inside your
    # configuration. For example, this adds a command 'my-hello' to your environment:
    # (pkgs.writeShellScriptBin "my-hello" ''
    #   echo "Hello, ${config.home.username}!"
    # '')
    pkgs-unstable.alacritty
    zsh
    gcc
    binutils
    file
    trash-cli
    pkgs-unstable.neovim
    inconsolata-nerdfont
    jetbrains-mono
    gh
    fuzzel
    waybar
    pcmanfm
    loupe
    swaynotificationcenter
    swaylock-fancy
    nwg-bar
    clipman
    fzf
    unzip
    ripgrep
    nodejs_21
    cargo
    v4l-utils
    # pkgs-unstable.qtcreator
    clang-tools # clangd required for better diagnostics in qtcreator
    gdb
    cmake
    gnumake
    zsh-powerlevel10k
    zsh-fzf-history-search
  ];

  # You can import other home-manager modules here
  imports = [
    # If you want to use home-manager modules from other flakes (such as nix-colors):
    # inputs.nix-colors.homeManagerModule

    # You can also split up your configuration and import pieces of it here:
    # ./nvim.nix
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
    ".p10k.zsh".source = ./kotur.dotfiles/.p10k.zsh;
    ".commonrc".source = ./kotur.dotfiles/.commonrc;
    ".profile".source = ./kotur.dotfiles/.profile;
    ".zshenv".text = ''
      source ~/.profile
      source ~/.p10k.zsh
      source ~/.commonrc

      HISTSIZE=10000000
      SAVEHIST=10000000
      setopt HIST_EXPIRE_DUPS_FIRST
      setopt HIST_IGNORE_DUPS
      setopt HIST_IGNORE_ALL_DUPS
      setopt HIST_IGNORE_SPACE
      setopt HIST_FIND_NO_DUPS
      setopt HIST_SAVE_NO_DUPS

      ZSH_FZF_HISTORY_SEARCH_BIND='^f'
      ZSH_FZF_HISTORY_SEARCH_REMOVE_DUPLICATES=1

      # Reverse tab cycle
      bindkey '^[[Z' reverse-menu-complete

    '';
    ".config/sway/config".source = ./kotur.dotfiles/sway;
    ".config/alacritty/alacritty.toml".source = ./kotur.dotfiles/alacritty.toml;
    ".config/waybar/style.css".source = ./kotur.dotfiles/waybar-style.css;
    ".config/waybar/config".source = ./kotur.dotfiles/waybar-config.json;
    "Pictures/background.jpg".source = ./kotur.dotfiles/pexels-stevan-aksentijevic-3958744.jpg;
    ".config/nwg-bar/style.css".source = ./kotur.dotfiles/nwg-bar-style.css;
    ".config/nwg-bar/bar.json".source = ./kotur.dotfiles/nwg-bar.json;
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
    EDITOR = "nvim";
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
        editor = "nvim";
        pager = "nvim";
        # whitespace = "trailing-space,space-before-tab";
      };

      init = {
        defaultBranch = "main";
      };

      color.diff = false;
      pager.branch = false;

      # commit.gpgsign = "true";
      # gpg.program = "gpg2";

      # protocol.keybase.allow = "always";
      credential.helper = "!gh auth git-credential";
      # pull.rebase = "false";
    };
  };

  programs.zsh = {
    enable = true;
    enableAutosuggestions = true;
    enableCompletion = true;
    syntaxHighlighting.enable = true;
    initExtra = ''
      ${builtins.readFile ./kotur.dotfiles/.zshrc}
      source ${pkgs.zsh-powerlevel10k}/share/zsh-powerlevel10k/powerlevel10k.zsh-theme  
      test -f ~/.config/zsh/.p10k.zsh && source ~/.config/zsh/.p10k.zsh  
      source ${pkgs.zsh-fzf-history-search}/share/zsh-fzf-history-search/zsh-fzf-history-search.plugin.zsh
    '';

   plugins = [
        {
          name = "zsh-nix-shell";
          file = "nix-shell.plugin.zsh";
          src = pkgs.fetchFromGitHub {
            owner = "chisui";
            repo = "zsh-nix-shell";
            rev = "v0.8.0";
            sha256 = "1lzrn0n4fxfcgg65v0qhnj7wnybybqzs4adz7xsrkgmcsr0ii8b7";
          };
        }
      ];

    oh-my-zsh = {
      enable = true;
      plugins = [
        "git" "kubectl" "fzf"
        # { name = "joshskidmore/zsh-fzf-history-search"; }
        # { name = "romkatv/powerlevel10k"; tags = [ as:theme depth:1 ]; }
        # { name = "Aloxaf/fzf-tab"; }
      ];
    };
  };

  # Nicely reload system units when changing configs
  systemd.user.startServices = "sd-switch";

  # https://nixos.wiki/wiki/FAQ/When_do_I_update_stateVersion
}

export PATH=~/.npm-global/bin:$PATH

if [ -d "$HOME/.local/bin" ] ; then
    PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$HOME/.cargo/bin" ] ; then
    PATH="$HOME/.cargo/bin:$PATH"
fi

alias aladark="ln -fs ~/.config/alacritty/themes/dark.toml ~/.config/alacritty/themes/active.toml"
alias alalight="ln -fs ~/.config/alacritty/themes/light.toml ~/.config/alacritty/themes/active.toml"
func alatheme() {
  ln -fs ~/.config/alacritty/themes/$1.toml ~/.config/alacritty/themes/active.toml
}
export GPG_TTY=$(tty)

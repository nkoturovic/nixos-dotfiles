export PATH=~/.npm-global/bin:$PATH

if [ -d "$HOME/.local/bin" ] ; then
    PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$HOME/.cargo/bin" ] ; then
    PATH="$HOME/.cargo/bin:$PATH"
fi

PATH="/media/kotur/4054940b-6749-437e-98fd-3f34dccde050/Unreal_Projects/scripts:$PATH"

alias aladark="ln -fs ~/.config/alacritty/themes/dark.toml ~/.config/alacritty/themes/active.toml"
alias alalight="ln -fs ~/.config/alacritty/themes/light.toml ~/.config/alacritty/themes/active.toml"
func alatheme() {
  ln -fs ~/.config/alacritty/themes/$1.toml ~/.config/alacritty/themes/active.toml
}
export GPG_TTY=$(tty)
